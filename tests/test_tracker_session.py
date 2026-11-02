"""TrackerSession thread logic with a fake Selenium driver (login once, re-login on expiry, user confirmation)."""
import os

from selenium.common.exceptions import NoSuchElementException

from fileprep.config.models import TrackerAccount, TrackerConfig, TrackerSite
from fileprep.tracker import session as session_module
from fileprep.tracker.session import TrackerSessionRegistry, UploadRequest


class FakeElement:
    def __init__(self, driver, key):
        self.driver, self.key, self.text = driver, key, "No exact size dupes" if key == "messagebar" else ""

    def send_keys(self, value):
        self.driver.typed.setdefault(self.driver.page, []).append((self.key, value))

    def click(self):
        if self.key == "login_button":
            self.driver.logged_in = True
            self.driver.logins += 1

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class FakeDriver:
    instances = []

    def __init__(self, *args, **kwargs):
        self.logged_in, self.logins, self.page, self.typed, self.current_url = False, 0, "", {}, ""
        self.quit_called, self.visits = False, []
        FakeDriver.instances.append(self)

    def set_window_size(self, *a):
        pass

    def get(self, url):
        self.page = self.current_url = url
        self.visits.append(url)

    def quit(self):
        self.quit_called = True

    def find_element(self, by, value):
        login_fields = {"username", "password", "login_button"}
        if value in login_fields and not self.logged_in:
            return FakeElement(self, value)
        if self.logged_in and value not in login_fields:
            return FakeElement(self, "messagebar" if value == "messagebar" else value)
        raise NoSuchElementException(value)


def test_session_logs_in_once_and_reuses_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module.webdriver, "Chrome", FakeDriver)
    config = TrackerConfig()
    config.browser.browser = "chrome"
    config.browser.page_timeout_seconds = 3
    config.sites["emp"] = TrackerSite(login_url="http://t/login", upload_url="http://t/upload.php", category="Cat")
    registry = TrackerSessionRegistry()
    asked = []
    for i in range(3):
        torrent = tmp_path / f"t{i}.torrent"
        torrent.write_bytes(b"x")
        if i == 2:
            FakeDriver.instances[0].logged_in = False  # session expired on the site
        session = registry.get("emp", config, TrackerAccount(username="u", password="p", announce_url="a"))
        request = UploadRequest(job_id="j", title=f"T{i}", tags="a b", description="d", image_url="http://img",
                                torrent_file=str(torrent), ask=lambda text, choices: asked.append(text) or "yes")
        assert session.submit(request).result(timeout=30) is True
        assert not os.path.exists(torrent)  # removed after the dupe check (legacy behaviour)
    driver = FakeDriver.instances[0]
    assert len(FakeDriver.instances) == 1  # one browser for all uploads
    assert driver.logins == 2  # initial login + one re-login after expiry
    assert len(asked) == 3  # one "form submitted?" confirmation per upload
    assert ("title", "T1") in driver.typed["http://t/upload.php"] and ("category", "Cat") in driver.typed["http://t/upload.php"]
    registry.shutdown()


def test_cancel_resets_the_form_and_the_job_end_closes_the_browser(tmp_path, monkeypatch):
    import time
    from fileprep.errors import UserCancelled
    FakeDriver.instances = []
    monkeypatch.setattr(session_module.webdriver, "Chrome", FakeDriver)
    config = TrackerConfig()
    config.browser.browser = "chrome"
    config.browser.page_timeout_seconds = 3
    config.sites["emp"] = TrackerSite(login_url="http://t/login", upload_url="http://t/upload.php")
    registry = TrackerSessionRegistry()
    account = TrackerAccount(username="u", password="p", announce_url="a")
    session = registry.get("emp", config, account, "job1")
    answers = iter(["cancel", "yes"])
    for i in range(2):
        torrent = tmp_path / f"t{i}.torrent"
        torrent.write_bytes(b"x")
        future = session.submit(UploadRequest(job_id="job1", title=f"T{i}", tags="", description="d", image_url=None,
                                              torrent_file=str(torrent), ask=lambda text, choices: next(answers)))
        if i == 0:
            try:
                future.result(timeout=30)
                raise AssertionError("cancel must fail the upload")
            except UserCancelled:
                pass
        else:
            assert future.result(timeout=30) is True  # next file: same window, still logged in
    driver = FakeDriver.instances[0]
    assert len(FakeDriver.instances) == 1 and driver.logins == 1 and not driver.quit_called
    assert driver.visits.count("http://t/upload.php") == 3  # upload, reset after the cancel, next upload
    # another job gets its own browser; ending a job closes only its own
    other = registry.get("emp", config, account, "job2")
    assert other is not session and {s["job_id"] for s in registry.status()} == {"job1", "job2"}
    assert registry.close_job("job1") == 1
    session.thread.join(timeout=10)
    assert driver.quit_called and not session.is_alive()
    assert [s["job_id"] for s in registry.status()] == ["job2"]
    registry.shutdown()


def test_cancel_answer_fails_the_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module.webdriver, "Chrome", FakeDriver)
    config = TrackerConfig()
    config.browser.browser = "chrome"
    config.browser.page_timeout_seconds = 3
    config.sites["emp"] = TrackerSite(login_url="http://t/login", upload_url="http://t/upload.php")
    registry = TrackerSessionRegistry()
    session = registry.get("emp", config, TrackerAccount(username="u", password="p", announce_url="a"))
    request = UploadRequest(job_id="j", title="T", tags="", description="d", image_url=None,
                            torrent_file=str(tmp_path / "x.torrent"), ask=lambda text, choices: "cancel")
    future = session.submit(request)
    from fileprep.errors import UserCancelled
    try:
        future.result(timeout=30)
        raised = False
    except UserCancelled:
        raised = True
    assert raised
    assert session.driver is not None  # a cancel keeps the browser (and the login) open
    registry.shutdown()
