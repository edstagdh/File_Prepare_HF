"""
Persistent, logged-in tracker browser sessions (Selenium), one dedicated thread per tracker.

The browser is started and logged in once, then kept open: every processed file only navigates to the
upload page, fills the form, attaches the torrent and runs the dupe check, after which you review and
submit the form yourself in that browser window and confirm in the web UI (semi-automatic, as before).

Sessions are shared by all jobs through :data:`registry` and requests are handled one at a time per
tracker. An idle session closes itself after ``browser.session_idle_close_minutes``. Every request
carries an ``ask`` callable that shows questions to the user of the job that owns the request.
"""

import concurrent.futures
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from fileprep.config.models import Selector, TrackerAccount, TrackerConfig
from fileprep.errors import UserCancelled

BY_MAP = {"id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR, "xpath": By.XPATH, "class": By.CLASS_NAME,
          "tag": By.TAG_NAME, "link": By.LINK_TEXT}
AskFn = Callable[[str, list[str]], str]


def locator(selector: Selector) -> tuple[str, str]:
    return BY_MAP[selector.by], selector.value


@dataclass
class UploadRequest:
    """Browser part of one upload. Files are prepared (torrent, template, tags) by the caller."""
    job_id: Optional[str]
    title: str
    tags: str
    description: str
    image_url: Optional[str]
    torrent_file: str
    ask: AskFn
    future: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)


class TrackerSession:
    def __init__(self, tracker_name: str, config: TrackerConfig, account: TrackerAccount, job_id: Optional[str] = None):
        # composition (not a Thread subclass) so private names cannot clash with threading internals
        self.thread = threading.Thread(target=self._run, name=f"tracker-{tracker_name}", daemon=True)
        self.job_id = job_id
        self.tracker_name = tracker_name
        self.config = config
        self.account = account
        self.site = config.sites[tracker_name]
        self.requests: "queue.Queue[Optional[UploadRequest]]" = queue.Queue()
        self.driver = None
        self.logged_in = False
        self.last_used = time.time()
        self.closed = threading.Event()
        self.closing = threading.Event()  # set by close(): a question being waited for is abandoned
        self.state = "starting"

    # ------------------------------------------------------------------ public
    def submit(self, request: UploadRequest) -> concurrent.futures.Future:
        if self.closed.is_set():
            raise RuntimeError("Tracker session is closed")
        self.requests.put(request)
        return request.future

    def close(self) -> None:
        """Close the browser and end the thread (after the current step; a pending question is abandoned)."""
        self.closing.set()
        self.requests.put(None)

    def status(self) -> dict:
        return {"id": session_id(self.job_id, self.tracker_name), "job_id": self.job_id,
                "tracker": self.tracker_name, "state": self.state, "logged_in": self.logged_in,
                "pending": self.requests.qsize(), "idle_seconds": int(time.time() - self.last_used)}

    def start(self) -> None:
        self.thread.start()

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    # ------------------------------------------------------------------ thread
    def _run(self) -> None:
        idle_limit = self.config.browser.session_idle_close_minutes * 60
        try:
            while True:
                self.state = "idle" if self.driver else "waiting"
                try:
                    request = self.requests.get(timeout=30)
                except queue.Empty:
                    if time.time() - self.last_used > idle_limit:
                        logger.info(f"Tracker '{self.tracker_name}' browser idle for {idle_limit // 60} min - closing it.")
                        break
                    continue
                if request is None:
                    break
                self.last_used = time.time()
                with logger.contextualize(job_id=request.job_id):
                    try:
                        self.state = "uploading"
                        request.future.set_result(self._process_request(request))
                    except UserCancelled as exc:
                        logger.warning(f"Tracker '{self.tracker_name}': {exc}.")
                        request.future.set_exception(exc)
                        if not self.closing.is_set():
                            self._reset_upload_page()  # ready for the job's next file, still logged in
                    except Exception as exc:
                        logger.exception(f"Tracker '{self.tracker_name}' upload failed")
                        self._reset_browser()
                        if not request.future.done():
                            request.future.set_exception(exc)
                self.last_used = time.time()
        finally:
            self.closed.set()
            self.state = "closed"
            self._quit()
            while not self.requests.empty():  # fail anything still queued
                pending = self.requests.get_nowait()
                if pending and not pending.future.done():
                    pending.future.set_exception(RuntimeError("Tracker session closed"))

    # ------------------------------------------------------------------ browser
    def _start_browser(self) -> None:
        b = self.config.browser
        if b.browser == "firefox":
            from selenium.webdriver.firefox.options import Options
            from selenium.webdriver.firefox.service import Service
            options = Options()
            if b.firefox_profile_path:
                options.add_argument("-profile")
                options.add_argument(b.firefox_profile_path)
            service = Service(executable_path=b.firefox_driver_path) if b.firefox_driver_path else Service()
            logger.info("Launching Firefox for tracker uploads...")
            self.driver = webdriver.Firefox(service=service, options=options)
        else:
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            options = Options()
            options.add_argument("--incognito")
            options.add_argument("--disable-blink-features=AutomationControlled")
            if b.chrome_profile_path:
                options.add_argument(f"user-data-dir={b.chrome_profile_path}")
            service = Service(executable_path=b.chrome_driver_path) if b.chrome_driver_path else Service()
            logger.info("Launching Chrome for tracker uploads...")
            self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_window_size(1280, 800)
        self.logged_in = False

    def _wait(self, timeout: Optional[int] = None) -> WebDriverWait:
        return WebDriverWait(self.driver, timeout or self.config.browser.page_timeout_seconds)

    def _quit(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
        self.driver = None
        self.logged_in = False

    def _reset_upload_page(self) -> None:
        """Discard the filled form: reload an empty upload page (the login is kept)."""
        if not self._alive():
            return
        try:
            self.driver.get(self.site.upload_url)
            logger.info(f"Tracker '{self.tracker_name}': upload form reset, ready for the next file.")
        except WebDriverException as exc:
            logger.warning(f"Tracker '{self.tracker_name}': could not reset the upload page ({exc.msg}) - "
                           f"the browser restarts with the next upload.")
            self._reset_browser()

    def _reset_browser(self) -> None:
        """After an error the browser state is unknown: start fresh on the next request."""
        self._quit()

    def _alive(self) -> bool:
        if not self.driver:
            return False
        try:
            _ = self.driver.current_url
            return True
        except WebDriverException:
            return False

    def _is_logged_in(self) -> bool:
        try:
            self.driver.find_element(*locator(self.site.form.login_success))
            return True
        except WebDriverException:
            return False

    def _login(self, request: UploadRequest) -> None:
        form = self.site.form
        self.state = "logging in"
        self.driver.get(self.site.login_url or self.site.upload_url)
        if self._is_logged_in():  # profile / cookies already logged in
            self.logged_in = True
            return
        self._wait().until(EC.presence_of_element_located(locator(form.login_username)))
        self.driver.find_element(*locator(form.login_username)).send_keys(self.account.username)
        self.driver.find_element(*locator(form.login_password)).send_keys(self.account.password)
        self.driver.find_element(*locator(form.login_submit)).click()
        logger.info(f"Tracker '{self.tracker_name}': login submitted.")
        if self.site.two_factor_auth:
            self._confirm(request, "Did you finish the login process with 2 factor authentication in the tracker browser window? "
                                   "(yes[y]/no[n]/cancel[c]): ", "2FA cancelled")
        try:
            self._wait().until(EC.presence_of_element_located(locator(form.login_success)))
        except TimeoutException:
            raise RuntimeError(f"Tracker '{self.tracker_name}': login failed or timed out")
        logger.success(f"Tracker '{self.tracker_name}': logged in.")
        self.logged_in = True

    def _confirm(self, request: UploadRequest, text: str, cancel_message: str) -> None:
        while True:
            answer = request.ask(text, ["yes", "no", "cancel"]).strip().lower()
            if answer in ("yes", "y"):
                return
            if answer in ("cancel", "c"):
                raise UserCancelled(cancel_message)
            logger.info("Continue in the browser window, then answer 'yes' (or 'cancel' to abort).")

    # ------------------------------------------------------------------ upload
    def _process_request(self, request: UploadRequest) -> bool:
        if not self._alive():
            self._start_browser()
        if not self.logged_in:
            self._login(request)
        form = self.site.form
        self.driver.get(self.site.upload_url)
        logger.info(f"Tracker '{self.tracker_name}': opened upload page.")
        try:
            self._wait().until(EC.presence_of_element_located(locator(form.title_field)))
        except TimeoutException:
            if not self._is_logged_in():  # session expired: log in again once
                logger.warning(f"Tracker '{self.tracker_name}': session expired, logging in again.")
                self.logged_in = False
                self._login(request)
                self.driver.get(self.site.upload_url)
                self._wait().until(EC.presence_of_element_located(locator(form.title_field)))
            else:
                raise

        values = [(form.category_field, self.site.category), (form.title_field, request.title),
                  (form.tags_field, request.tags), (form.image_field, request.image_url or ""),
                  (form.description_field, request.description)]
        for selector, value in values:
            if not selector.value or not value:
                continue
            self._wait().until(EC.presence_of_element_located(locator(selector)))
            self.driver.find_element(*locator(selector)).send_keys(value)
        self.driver.find_element(*locator(form.torrent_field)).send_keys(request.torrent_file)
        self._dupe_check(request.torrent_file)
        self.state = "waiting for you"
        self._confirm(request, f"[{self.tracker_name}] Review the upload form in the tracker browser window and submit it. "
                               "Did you finish submitting the form? (yes[y]/no[n]/cancel[c]): ", "Upload cancelled by user")
        logger.success(f"Tracker '{self.tracker_name}': form submitted.")
        # Each tracker gets its own torrent (own announce URL); it is not needed once uploaded.
        try:
            Path(request.torrent_file).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"Failed to delete torrent file {request.torrent_file}: {exc}")
        return True

    def _dupe_check(self, torrent_file: str) -> None:
        form = self.site.form
        if not form.dupe_check_button.value:
            logger.info(f"Tracker '{self.tracker_name}': no dupe check button configured - skipped.")
            return
        try:
            self._wait().until(EC.element_to_be_clickable(locator(form.dupe_check_button))).click()
            logger.info("Clicked 'Check for dupes' button.")
            places = [locator(sel) for sel in (form.dupe_message, form.dupe_message_alt) if sel.value]

            def message(driver):
                for by, value in places:
                    try:
                        element = driver.find_element(by, value)
                        if element.is_displayed() and element.text.strip():
                            return element.text.strip()
                    except StaleElementReferenceException:
                        return False
                    except WebDriverException:
                        continue
                return False

            text = WebDriverWait(self.driver, 60, poll_frequency=0.5).until(message)
            lower = text.lower()
            found = bool(form.dupe_found_text) and form.dupe_found_text.lower() in lower
            none = bool(form.dupe_none_text) and form.dupe_none_text.lower() in lower
            if found:
                logger.warning(f"Duplicate detected: {text}")
            elif none:
                logger.success(f"No duplicates detected: {text}")
            else:
                logger.info(f"Dupe check completed: {text}")
            if found or none:
                # the torrent is attached to the form and checked - this tracker's copy is no longer needed
                try:
                    Path(torrent_file).unlink(missing_ok=True)
                    logger.info(f"Deleted file: {torrent_file}")
                except OSError as exc:
                    logger.warning(f"failed to delete existing torrent file: {torrent_file} ({exc})")
        except TimeoutException:
            logger.warning("Dupe check message did not appear in time.")
        except WebDriverException as exc:
            logger.error(f"Failed during dupe check: {exc}")


def session_id(job_id: Optional[str], tracker_name: str) -> str:
    return f"{job_id or 'cli'}-{tracker_name}"


class TrackerSessionRegistry:
    """One session (browser window) per job and tracker, closed when the job ends (re-created when the browser
    settings change)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[str, TrackerSession]] = {}

    def get(self, tracker_name: str, config: TrackerConfig, account: TrackerAccount,
            job_id: Optional[str] = None) -> TrackerSession:
        key = config.browser.model_dump_json() + config.sites[tracker_name].model_dump_json() + account.username
        sid = session_id(job_id, tracker_name)
        with self._lock:
            existing = self._sessions.get(sid)
            if existing and existing[0] == key and existing[1].is_alive() and not existing[1].closing.is_set():
                return existing[1]
            if existing and existing[1].is_alive():
                existing[1].close()
            session = TrackerSession(tracker_name, config, account, job_id)
            session.start()
            self._sessions[sid] = (key, session)
            return session

    def status(self) -> list[dict]:
        with self._lock:
            return [s.status() for _, s in self._sessions.values() if s.is_alive()]

    def close(self, sid: str) -> bool:
        with self._lock:
            entry = self._sessions.pop(sid, None)
        if entry:
            entry[1].close()
        return bool(entry)

    def close_job(self, job_id: Optional[str]) -> int:
        """Close every browser of a job (called when the job ends: completed, stopped, failed or cancelled)."""
        with self._lock:
            sids = [sid for sid, (_, s) in self._sessions.items() if s.job_id == job_id]
            entries = [self._sessions.pop(sid) for sid in sids]
        for _, session in entries:
            logger.info(f"Closing the '{session.tracker_name}' tracker browser of this job.")
            session.close()
        return len(entries)

    def shutdown(self) -> None:
        with self._lock:
            sessions = [s for _, s in self._sessions.values()]
            self._sessions.clear()
        for session in sessions:
            session.close()


registry = TrackerSessionRegistry()


def delete_prefixed_files(base_path: str, suffixes: list[str], prefix: str = "") -> None:
    """Delete ``<base_path>/<prefix><suffix>`` for every suffix (helper files after the last tracker)."""
    for suffix in suffixes:
        file_path = Path(base_path) / f"{prefix}{suffix}"
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Deleted file: {file_path}")
            except OSError as exc:
                logger.warning(f"Failed to delete {file_path}: {exc}")


def tracker_title(title: str, codec: Optional[str], self_encoded: Optional[bool], resolution: Optional[str]) -> str:
    """Upload title with codec / re-encode / resolution tags (legacy format)."""
    if codec in ("av1", "hevc"):
        if self_encoded:
            return f"{title} - {codec.upper()} - Re-encode - {resolution}"
        return f"{title} - {codec.upper()} - {resolution}"
    return f"{title} - {resolution}"


__all__ = ["UploadRequest", "TrackerSession", "registry", "delete_prefixed_files", "tracker_title"]
