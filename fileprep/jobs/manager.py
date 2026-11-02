"""
Job manager: creation, preview, queueing, scheduling, stop / cancel, prompts and logs.

Every preview or run executes in its own worker thread with its own asyncio event loop, so CPU-bound
work inside a job (PIL, OpenCV, ffmpeg pipes) never blocks the web server or other jobs.

Scheduling rules (``Configs/app.json`` > jobs):
* at most ``max_concurrent_jobs`` runs at the same time;
* at most ``max_cpu_encode_jobs`` running jobs that re-encode on the CPU (x265 alone uses ~70% of the CPU)
  and ``max_gpu_encode_jobs`` that re-encode on the GPU (default 1 each);
* two jobs never run on the same working directory, and a job may not write into a folder another queued /
  running job uses. A parent and a child folder can run at the same time (jobs only read the files directly
  inside their folder).
Queued jobs that cannot start yet are skipped, so a non-HEVC job can start while an HEVC job waits.
"""

import asyncio
import collections
import concurrent.futures
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from fileprep import paths
from fileprep.config.models import JobConfig
from fileprep.config.store import store
from fileprep.log import asyncio_exception_handler, job_log_format
from fileprep.jobs.models import ACTIVE_STATES, EDITABLE_STATES, RESUMABLE_STATES, Job, PendingPrompt
from fileprep.pipeline.plan import JobPlan
from fileprep.runtime import Question, Runtime, use_runtime

LOG_BUFFER_LINES = 2000


class JobError(Exception):
    """Invalid operation for the job's current state."""


@dataclass
class _Worker:
    thread: threading.Thread
    loop: Optional[asyncio.AbstractEventLoop] = None
    task: Optional[asyncio.Task] = None
    stop_flag: threading.Event = field(default_factory=threading.Event)
    prompt_futures: dict[str, concurrent.futures.Future] = field(default_factory=dict)


class JobPrompter:
    """Shows questions in the web UI and waits for the answer (posted by the web thread)."""
    supports_multiline = True
    supports_list = True

    def __init__(self, manager: "JobManager", job_id: str):
        self.manager = manager
        self.job_id = job_id

    async def ask(self, question: Question) -> str:
        future = self.manager._open_prompt(self.job_id, question)
        return await asyncio.wrap_future(future)


class JobManager:
    def __init__(self, jobs_dir=paths.JOBS_DIR):
        self.jobs_dir = jobs_dir
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._workers: dict[str, _Worker] = {}
        self._logs: dict[str, collections.deque] = {}
        self._log_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)
        self._last_save: dict[str, float] = {}
        self._sink_id: Optional[int] = None

    # ================================================================== lifecycle
    def start(self) -> None:
        os.makedirs(self.jobs_dir, exist_ok=True)
        self._sink_id = logger.add(self._log_sink, level="DEBUG", filter=lambda r: "job_id" in r["extra"] and r["extra"]["job_id"],
                                   format=job_log_format, enqueue=False, catch=True)
        for name in os.listdir(self.jobs_dir):
            if not name.endswith(".json"):
                continue
            try:
                job = Job.model_validate_json(open(os.path.join(self.jobs_dir, name), encoding="utf-8").read())
            except Exception as exc:
                logger.error(f"Cannot load job file {name}: {exc}")
                continue
            if job.state in ("running", "previewing", "stopping"):
                job.state, job.error = ("interrupted" if job.activity == "run" else "draft"), "Server restarted"
                job.prompts = []
            self._jobs[job.id] = job
            self._save(job, force=True)
        self.cleanup_finished()
        self._schedule()

    def shutdown(self) -> None:
        with self._lock:
            workers = list(self._workers.items())
        for job_id, worker in workers:
            self._cancel_worker(worker)
        for _, worker in workers:
            worker.thread.join(timeout=15)
        from fileprep.tracker.session import registry
        registry.shutdown()
        if self._sink_id is not None:
            logger.remove(self._sink_id)

    # ================================================================== queries
    def list_jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return job

    def snapshot(self, job_id: str) -> dict:
        with self._lock:
            return json.loads(self.get(job_id).model_dump_json())

    def logs(self, job_id: str, after: int = 0) -> tuple[list[str], int]:
        """Log lines with index >= ``after`` and the next index to ask for."""
        buffer = self._logs.get(job_id)
        if buffer is None:
            buffer = self._load_log_tail(job_id)
        with self._log_locks[job_id]:
            lines = list(buffer)
        total = getattr(buffer, "total", len(lines))
        first_index = total - len(lines)
        start = max(after - first_index, 0)
        return lines[start:], total

    def active_directories(self, exclude: str) -> dict[str, str]:
        """Folders used by other queued/running jobs: their working folder and the output folders of their preview."""
        with self._lock:
            used = {}
            for j in self._jobs.values():
                if j.id == exclude or j.state not in ("queued", "running", "stopping") or not j.working_directory:
                    continue
                used[j.working_directory] = j.id
                for fp in (j.plan.files if j.plan else []):
                    if fp.output_directory:
                        used.setdefault(fp.output_directory, j.id)
            return used

    # ================================================================== mutations
    def create(self, config: JobConfig, name: str, user: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], name=name.strip() or os.path.basename(config.processing.input.working_directory.rstrip("\\/")) or "job",
                  created_by=user, config=config)
        with self._lock:
            self._jobs[job.id] = job
            self._save(job, force=True)
        logger.info(f"Job {job.id} created by {user}: {job.name}")
        return job

    def update_config(self, job_id: str, config: JobConfig, name: Optional[str] = None) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.state not in EDITABLE_STATES:
                raise JobError(f"Job is {job.state} - stop or cancel it before changing its settings.")
            names_only = job.plan_is_current and _only_names_changed(job.config, config)
            job.config = config
            if name:
                job.name = name
            if names_only:
                self._reapply_names(job)
            elif not job.plan_is_current:
                job.state = "draft"
            self._save(job, force=True)
            return job

    def set_names(self, job_id: str, suffix: Optional[str] = None, file_name: Optional[str] = None,
                  new_name: Optional[str] = None) -> Job:
        """Change the suffix for all files and/or one file's name after the preview (no new preview needed)."""
        from fileprep.pipeline.naming import clean_suffix
        with self._lock:
            job = self.get(job_id)
            config = job.config.model_copy(deep=True)
            if suffix is not None:
                try:
                    config.processing.naming.extra_suffix = clean_suffix(suffix)
                except ValueError as exc:
                    raise JobError(str(exc))
            if file_name is not None:
                self._plan_file_name(job, file_name)
                originals = {new: old for old, new in (job.plan.renames if job.plan else [])}
                key = originals.get(file_name, file_name)
                new_name = (new_name or "").strip()
                if new_name.lower().endswith(".mp4"):
                    new_name = new_name[:-4]
                if new_name:
                    config.processing.naming.filename_overrides[key] = new_name
                else:
                    config.processing.naming.filename_overrides.pop(key, None)
        return self.update_config(job_id, config)

    def _reapply_names(self, job: Job) -> None:
        from fileprep.pipeline.planner import reapply_names
        # own thread + loop: this may be called from the web server's running event loop (only probes local files - quick)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, reapply_names(job.config, job.plan)).result()
        job.state = "blocked" if job.plan.blocking else "ready"
        logger.info(f"Job {job.id}: names updated in the preview")

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self.get(job_id)
            if job.state in ACTIVE_STATES:
                raise JobError("Cancel the job before deleting it.")
            del self._jobs[job_id]
        for suffix in (".json", ".log"):
            try:
                os.remove(os.path.join(self.jobs_dir, job_id + suffix))
            except FileNotFoundError:
                pass
        self._logs.pop(job_id, None)

    def start_preview(self, job_id: str) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.state not in EDITABLE_STATES:
                raise JobError(f"Cannot preview a job that is {job.state}.")
            job.state, job.activity, job.error = "previewing", "preview", None
            job.progress.stage, job.progress.percent, job.progress.detail = "Preview", 0, ""
            self._save(job, force=True)
            self._spawn(job, "preview")
            return job

    def enqueue(self, job_id: str) -> Job:
        """Queue a previewed job (or resume a stopped / interrupted / failed one)."""
        app = store.app()
        with self._lock:
            job = self.get(job_id)
            resuming = job.state in RESUMABLE_STATES and job.plan_is_current and job.results
            if not resuming:
                if job.state not in EDITABLE_STATES:
                    raise JobError(f"Job is {job.state}.")
                if app.jobs.require_preview:
                    if job.state != "ready":
                        raise JobError("Run a preview without blocking issues first (or turn off 'Require a clean preview' in Settings > Application > Jobs).")
                    if not job.plan_is_current:
                        raise JobError("Settings changed since the preview - run a new preview.")
                if job.plan_is_current and job.plan.blocking:
                    raise JobError("The preview has blocking issues.")
                job.results = {}
            for other_dir, other_id in self.active_directories(job.id).items():
                if os.path.normcase(os.path.abspath(other_dir)) == os.path.normcase(os.path.abspath(job.working_directory)):
                    raise JobError(f"Job {other_id} is already queued/running on this directory.")
            job.state, job.queued_at, job.error, job.activity = "queued", time.time(), None, "run"
            self._save(job, force=True)
        self._schedule()
        return self.get(job_id)

    def stop(self, job_id: str) -> Job:
        """Graceful stop: finish the current file, keep results, allow a later start."""
        with self._lock:
            job = self.get(job_id)
            if job.state == "queued":
                job.state = "stopped" if job.results else "ready"
            elif job.state in ("running", "previewing"):
                worker = self._workers.get(job_id)
                if worker:
                    worker.stop_flag.set()
                job.state = "stopping" if job.state == "running" else job.state
            else:
                raise JobError(f"Job is {job.state}.")
            self._save(job, force=True)
        self._schedule()
        return job

    def cancel(self, job_id: str) -> Job:
        """Immediate abort: the current step (e.g. ffmpeg) is killed."""
        with self._lock:
            job = self.get(job_id)
            if job.state == "queued":
                job.state = "cancelled"
            elif job.state in ("running", "previewing", "stopping"):
                worker = self._workers.get(job_id)
                if worker:
                    self._cancel_worker(worker)
            else:
                raise JobError(f"Job is {job.state}.")
            self._save(job, force=True)
        self._schedule()
        return job

    def answer_prompt(self, job_id: str, prompt_id: str, answer: str) -> None:
        with self._lock:
            worker = self._workers.get(job_id)
            future = worker.prompt_futures.pop(prompt_id, None) if worker else None
            job = self.get(job_id)
            job.prompts = [p for p in job.prompts if p.id != prompt_id]
            self._save(job, force=True)
        if not future:
            raise JobError("This question is no longer waiting for an answer.")
        self._append_log(job_id, f"{time.strftime('%H:%M:%S')} | ANSWER   | {answer if answer.strip() else '(empty)'}")
        future.set_result(answer)

    def _plan_file_name(self, job: Job, file_name: str) -> str:
        """Validate that ``file_name`` is a plain input file of this job's preview."""
        if not job.plan or file_name not in {f.source_name for f in job.plan.files} or os.path.basename(file_name) != file_name:
            raise JobError("Unknown file for this job - run the preview again.")
        return file_name

    def move_out(self, job_id: str, file_name: str) -> str:
        """Move one input file out of the working directory (``input.excluded_folder``); the preview must be run again."""
        with self._lock:
            job = self.get(job_id)
            if job.state not in EDITABLE_STATES:
                raise JobError(f"Job is {job.state} - stop or cancel it first.")
            self._plan_file_name(job, file_name)
            directory = job.working_directory
            target_dir = os.path.join(directory, job.config.processing.input.excluded_folder or "_excluded")
            source, target = os.path.join(directory, file_name), os.path.join(target_dir, file_name)
            if not os.path.isfile(source):
                raise JobError(f"File not found: {file_name}")
            if os.path.exists(target):
                raise JobError(f"{target} already exists.")
            os.makedirs(target_dir, exist_ok=True)
            shutil.move(source, target)
            job.config.processing.matching.uuid_overrides.pop(file_name, None)
            job.state = "draft"
            self._save(job, force=True)
        logger.info(f"Job {job_id}: moved {file_name} to {target_dir}")
        return target

    def set_uuid(self, job_id: str, file_name: str, tpdb_uuid: str) -> Job:
        """Match one file directly by a TPDB UUID (empty = remove); the preview must be run again."""
        tpdb_uuid = tpdb_uuid.strip()
        if tpdb_uuid and not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", tpdb_uuid):
            raise JobError("Not a TPDB UUID (expected xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx, shown on the scene page).")
        with self._lock:
            job = self.get(job_id)
            self._plan_file_name(job, file_name)
            config = job.config.model_copy(deep=True)
            if tpdb_uuid:
                config.processing.matching.uuid_overrides[file_name] = tpdb_uuid.lower()
            else:
                config.processing.matching.uuid_overrides.pop(file_name, None)
        return self.update_config(job_id, config)

    def cleanup_finished(self) -> int:
        days = store.app().jobs.keep_finished_jobs_days
        limit = time.time() - days * 86400
        stale = [j.id for j in self.list_jobs() if j.state in ("completed", "cancelled") and (j.finished_at or j.updated_at) < limit]
        for job_id in stale:
            self.delete(job_id)
        return len(stale)

    # ================================================================== scheduling
    def scheduler_state(self) -> dict:
        with self._lock:
            running = [j for j in self._jobs.values() if j.state in ("running", "stopping")]
            return {"max_concurrent_jobs": store.app().jobs.max_concurrent_jobs, "running": len(running),
                    "cpu_encode_running": [j.id for j in running if j.encode_slot == "cpu"],
                    "gpu_encode_running": [j.id for j in running if j.encode_slot == "gpu"],
                    "queued": sum(j.state == "queued" for j in self._jobs.values())}

    def _schedule(self) -> None:
        limits = store.app().jobs
        max_jobs = limits.max_concurrent_jobs
        slot_limit = {"cpu": limits.max_cpu_encode_jobs, "gpu": limits.max_gpu_encode_jobs}
        with self._lock:
            running = [j for j in self._jobs.values() if j.state in ("running", "stopping")]
            queued = sorted((j for j in self._jobs.values() if j.state == "queued"), key=lambda j: j.queued_at or 0)
            for job in queued:
                if len(running) >= max_jobs:
                    break
                slot = job.encode_slot
                if slot and sum(r.encode_slot == slot for r in running) >= slot_limit[slot]:
                    job.progress.detail = f"Waiting: {slot.upper()} encoding slot busy"
                    continue
                busy = {os.path.normcase(os.path.abspath(r.working_directory)) for r in running}
                if os.path.normcase(os.path.abspath(job.working_directory)) in busy:
                    job.progress.detail = "Waiting: directory in use"
                    continue
                job.state, job.started_at, job.finished_at = "running", job.started_at or time.time(), None
                job.progress.stage, job.progress.detail = "Starting", ""
                self._save(job, force=True)
                self._spawn(job, "run")
                running.append(job)

    # ================================================================== workers
    def _spawn(self, job: Job, mode: str) -> None:
        worker = _Worker(thread=threading.Thread(target=self._thread_main, args=(job.id, mode),
                                                 name=f"job-{job.id}-{mode}", daemon=True))
        self._workers[job.id] = worker
        worker.thread.start()

    def _cancel_worker(self, worker: _Worker) -> None:
        worker.stop_flag.set()
        for future in list(worker.prompt_futures.values()):
            future.cancel()
        if worker.loop and worker.task and not worker.task.done():
            worker.loop.call_soon_threadsafe(worker.task.cancel)

    def _thread_main(self, job_id: str, mode: str) -> None:
        worker = self._workers[job_id]
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(asyncio_exception_handler)
        asyncio.set_event_loop(loop)
        worker.loop = loop
        try:
            worker.task = loop.create_task(self._job_main(job_id, mode, worker))
            loop.run_until_complete(worker.task)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(f"Job {job_id} worker crashed")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
            with self._lock:
                self._workers.pop(job_id, None)
            self._schedule()

    async def _job_main(self, job_id: str, mode: str, worker: _Worker) -> None:
        job = self.get(job_id)
        config = job.config.model_copy(deep=True)
        notify = None
        if config.processing.notifications.enabled:
            from fileprep.notifiers import get_notifier
            notify = get_notifier(config.processing.notifications.notifier)
        runtime = Runtime(config=config, prompter=JobPrompter(self, job_id), job_id=job_id, notify=notify,
                          progress_callback=lambda stage, pct, detail: self._progress(job_id, stage, pct, detail),
                          stop_requested=worker.stop_flag.is_set)
        with logger.contextualize(job_id=job_id), use_runtime(runtime):
            try:
                if mode == "preview":
                    await self._run_preview(job_id, config)
                else:
                    await self._run_job(job_id, config)
            except asyncio.CancelledError:
                logger.warning("Job cancelled.")
                self._finish(job_id, "cancelled" if mode == "run" else "draft", "Cancelled by user")
                raise
            except Exception as exc:
                logger.exception(f"Job {mode} failed")
                self._finish(job_id, "failed" if mode == "run" else "draft", str(exc))
            finally:
                if mode == "run" and notify and store.app().jobs.notify_on_finish:
                    state = self.get(job_id).state
                    try:
                        await notify(f"Job '{self.get(job_id).name}' finished: {state}")
                    except Exception:
                        logger.warning("Finish notification failed")

    async def _run_preview(self, job_id: str, config: JobConfig) -> None:
        from fileprep.pipeline.planner import build_plan
        logger.info("=" * 80)
        logger.info("Preview started (no files are modified)")
        plan: JobPlan = await build_plan(config, self.active_directories(job_id))
        summary = plan.summary()
        logger.info(f"Preview finished: {summary['files']} file(s), {summary['errors']} error(s), {summary['warnings']} warning(s)")
        with self._lock:
            job = self.get(job_id)
            job.plan = plan
            job.state = "blocked" if plan.blocking else "ready"
            job.progress.stage, job.progress.percent, job.progress.detail = "Preview", 100, ""
            job.prompts = []
            self._save(job, force=True)

    async def _run_job(self, job_id: str, config: JobConfig) -> None:
        from fileprep.pipeline.runner import RunHooks, run_plan, verify_plan_current
        from fileprep.tracker.upload import upload_to_tracker
        job = self.get(job_id)
        logger.info("=" * 80)
        logger.info(f"Run started: {job.name}")
        if not job.plan_is_current:
            # running without a (current) preview: the matching phase runs now, questions are asked as usual
            from fileprep.pipeline.planner import build_plan
            logger.info("No current preview - checking the files first")
            plan = await build_plan(config, self.active_directories(job_id))
            with self._lock:
                job = self.get(job_id)
                job.plan = plan
                self._save(job, force=True)
            if plan.blocking:
                logger.error("Blocking issues found - nothing was processed. See the plan below.")
                self._finish(job_id, "blocked", "Blocking issues found")
                return
        elif not job.results:
            verify_plan_current(config, job.plan)

        def on_file_update(result):
            with self._lock:
                self.get(job_id).results[result.source_name] = result
                self._save(self.get(job_id), force=True)

        async def submit_tracker_upload(**kwargs) -> bool:
            return await upload_to_tracker(config, **kwargs)

        summary = await run_plan(config, job.plan, RunHooks(on_file_update=on_file_update,
                                                            submit_tracker_upload=submit_tracker_upload), job.results)
        stopped = self._workers.get(job_id) and self._workers[job_id].stop_flag.is_set()
        remaining = any(r.status not in ("success", "skipped") for r in self.get(job_id).results.values()) or \
                    len(self.get(job_id).results) < sum(1 for f in job.plan.files if f.match and f.status != "error")
        if stopped and remaining:
            self._finish(job_id, "stopped", None)
        else:
            self._finish(job_id, "failed" if summary.failed else "completed",
                         f"{summary.failed} file(s) failed" if summary.failed else None)
        logger.info(f"Run finished: {self.get(job_id).state}")

    def _finish(self, job_id: str, state: str, error: Optional[str]) -> None:
        with self._lock:
            job = self.get(job_id)
            job.state, job.error = state, error
            job.prompts = []
            if job.activity == "run":
                job.finished_at = time.time()
            self._save(job, force=True)

    # ================================================================== prompts / progress / logs
    def _open_prompt(self, job_id: str, question: Question) -> concurrent.futures.Future:
        future: concurrent.futures.Future = concurrent.futures.Future()
        lines, _ = self.logs(job_id)
        context_lines = store.app().jobs.prompt_context_lines
        prompt = PendingPrompt(id=uuid.uuid4().hex[:8], title=question.title, text=question.text.strip(),
                               choices=question.choices, multiline=question.multiline, context=lines[-context_lines:],
                               kind=question.kind, item_label=question.item_label)
        with self._lock:
            worker = self._workers.get(job_id)
            if not worker:
                future.set_exception(RuntimeError("Job is not running"))
                return future
            worker.prompt_futures[prompt.id] = future
            job = self.get(job_id)
            job.prompts.append(prompt)
            self._save(job, force=True)
        self._append_log(job_id, f"{time.strftime('%H:%M:%S')} | QUESTION | {prompt.text}")
        return future

    def _progress(self, job_id: str, stage: str, percent, detail: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress.stage, job.progress.percent, job.progress.detail = stage, percent, detail
            job.progress.updated_at = time.time()
            self._save(job)

    def _log_sink(self, message) -> None:
        job_id = message.record["extra"].get("job_id")
        if job_id:
            self._append_log(job_id, str(message).rstrip("\n"))

    def _append_log(self, job_id: str, line: str) -> None:
        with self._log_locks[job_id]:
            buffer = self._logs.get(job_id)
            if buffer is None:
                buffer = self._logs[job_id] = _CountingDeque(maxlen=LOG_BUFFER_LINES)
            buffer.append(line)
            try:
                with open(os.path.join(self.jobs_dir, f"{job_id}.log"), "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    def _load_log_tail(self, job_id: str) -> collections.deque:
        buffer = _CountingDeque(maxlen=LOG_BUFFER_LINES)
        try:
            with open(os.path.join(self.jobs_dir, f"{job_id}.log"), encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    buffer.append(line.rstrip("\n"))
        except FileNotFoundError:
            pass
        self._logs[job_id] = buffer
        return buffer

    # ================================================================== persistence
    def _save(self, job: Job, force: bool = False) -> None:
        now = time.time()
        job.updated_at = now
        if not force and now - self._last_save.get(job.id, 0) < 2:
            return
        self._last_save[job.id] = now
        path = os.path.join(self.jobs_dir, f"{job.id}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(job.model_dump_json(indent=1))
        os.replace(tmp, path)


def _only_names_changed(old: JobConfig, new: JobConfig) -> bool:
    """True when the two configs differ only in the suffix for all files / custom file names."""
    def strip(config: JobConfig) -> dict:
        data = config.model_dump(mode="json")
        data["processing"]["naming"].pop("extra_suffix", None)
        data["processing"]["naming"].pop("filename_overrides", None)
        return data
    return strip(old) == strip(new)


class _CountingDeque(collections.deque):
    """Deque that remembers how many items were ever appended (stable log line indexes for polling)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.total = 0

    def append(self, item):
        super().append(item)
        self.total += 1


manager = JobManager()
