import asyncio
import json
import time

from loguru import logger

from fileprep.config.models import JobConfig
from fileprep.errors import UserCancelled, describe, is_expected
from fileprep.pipeline import rollback
from fileprep.pipeline.plan import FilePlan, FileResult, PlannedOutput
from fileprep.runtime import Runtime, use_runtime


class Answer:
    def __init__(self, answer):
        self.answer, self.questions = answer, []

    async def ask(self, question):
        self.questions.append(question)
        return self.answer


def _setup(tmp_path):
    work = tmp_path / "work"
    folder = work / "Studio.26.01.01.Jane.Doe"
    folder.mkdir(parents=True)
    base = folder.name
    (folder / f"{base}.mp4").write_bytes(b"video")
    for name in (f"{base}.webp", f"{base}_preview.webp", f"{base}_template.txt", f"{base}_tags.txt", "notes.txt"):
        (folder / name).write_text("x")
    (folder / f"{base}_hamster.txt").write_text(json.dumps({f"{base} - cover": [{"image_url": "https://hamster/img/1.webp"}]}))
    (folder / f"{base}_pixhost.txt").write_text(json.dumps({f"{base} - cover": [{"image_url": "https://pix/1.webp"}]}))
    (work / f"{base}.torrent").write_text("t")
    fp = FilePlan(source_name="original name.mp4", source_size=5, source_mtime=0, new_file_name=f"{base}.mp4",
                  outputs=[PlannedOutput(kind="cover", path=str(folder / f"{base}.webp"), exists=False)])
    return work, folder, base, fp


def test_undo_deletes_created_files_and_moves_the_video_back(tmp_path, monkeypatch):
    work, folder, base, fp = _setup(tmp_path)
    deleted = []
    monkeypatch.setattr("fileprep.uploaders.pixhost.pixhost_pending_uploads", lambda: [
        {"key": f"{base} - cover", "local_file": str(folder / f"{base}.webp"), "manage_url": "https://pix/manage/1",
         "timestamp": "2999-01-01T00:00:00+00:00"},
        {"key": f"{base} - cover", "local_file": str(folder / f"{base}.webp"), "manage_url": "https://pix/manage/old",
         "timestamp": "2000-01-01T00:00:00+00:00"}])  # uploaded by an earlier run: kept

    async def fake_delete(url):
        deleted.append(url)
        return True
    monkeypatch.setattr("fileprep.uploaders.pixhost.pixhost_delete_image", fake_delete)
    result = FileResult(source_name=fp.source_name, started_at=time.time(), completed_steps=["rename", "moved to folder"])
    prompter = Answer("undo")
    with use_runtime(Runtime(config=JobConfig(), prompter=prompter)):
        done = asyncio.run(rollback.offer_undo(JobConfig(), fp, result, directory=str(work), output_directory=str(folder),
                                               base_name=base, video_path=str(folder / f"{base}.mp4"),
                                               reason="The upload to hf was cancelled"))
    assert done and prompter.questions[0].choices == ["undo", "keep"]
    assert deleted == ["https://pix/manage/1"]
    assert (work / "original name.mp4").read_bytes() == b"video"
    assert not (work / f"{base}.torrent").exists()
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]  # unknown files are never deleted
    assert result.status == "failed" and "undone" in result.error and result.completed_steps == []


def test_keep_leaves_everything(tmp_path):
    work, folder, base, fp = _setup(tmp_path)
    before = sorted(p.name for p in folder.iterdir())
    with use_runtime(Runtime(config=JobConfig(), prompter=Answer("keep"))):
        done = asyncio.run(rollback.offer_undo(JobConfig(), fp, FileResult(source_name=fp.source_name), directory=str(work),
                                               output_directory=str(folder), base_name=base,
                                               video_path=str(folder / f"{base}.mp4"), reason="x"))
    assert not done and sorted(p.name for p in folder.iterdir()) == before


def test_expected_failures_are_logged_without_traceback():
    from fileprep.log import _one_line_failures
    lines = []
    sink = logger.add(lambda m: lines.append(m.record), format="{message}")
    patched = logger.patch(_one_line_failures)
    try:
        for exc in (UserCancelled("Upload cancelled by user"), TimeoutError("read timed out"), ZeroDivisionError("division by zero")):
            try:
                raise exc
            except Exception:
                patched.exception("Step failed")
    finally:
        logger.remove(sink)
    assert [r["message"] for r in lines] == ["Step failed: Upload cancelled by user", "Step failed: TimeoutError: read timed out",
                                             "Step failed: ZeroDivisionError: division by zero"]
    assert [r["exception"] is None for r in lines] == [True, True, False]  # bugs keep their traceback (file log)
    assert is_expected(RuntimeError("x")) and not is_expected(RecursionError("x"))
    assert describe(UserCancelled("Cancelled")) == "Cancelled"


def test_cancelled_run_marks_the_file_in_progress_as_interrupted(tmp_path, monkeypatch):
    from fileprep.pipeline import runner
    from fileprep.pipeline.plan import JobPlan
    from fileprep.pipeline.plan import SceneMatch
    job = JobConfig()
    job.processing.input.working_directory = str(tmp_path)
    fps = [FilePlan(source_name=f"{n}.mp4", source_size=1, source_mtime=0, new_file_name=f"New{n}.mp4", match=SceneMatch())
           for n in "AB"]
    plan = JobPlan(created_at=0, working_directory=str(tmp_path), config_hash="", inputs_hash="", files=fps)
    started = asyncio.Event()

    async def slow_process(job_, fp, result, hooks):
        result.completed_steps = ["rename"]
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(runner, "process_file", slow_process)
    monkeypatch.setattr(runner, "list_input_files", lambda *a, **k: [f.source_name for f in fps])
    results = {}

    async def main():
        task = asyncio.create_task(runner.run_plan(job, plan, runner.RunHooks(), results))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with use_runtime(Runtime(config=job, prompter=Answer("keep"))):
        asyncio.run(main())
    assert results["A.mp4"].interrupted and results["A.mp4"].status == "failed"
    assert "B.mp4" not in results or not results["B.mp4"].interrupted


def test_manager_offers_undo_after_cancel(tmp_path, monkeypatch):
    from fileprep.jobs.manager import JobManager
    from fileprep.jobs.models import Job
    from fileprep.pipeline.plan import JobPlan
    work, folder, base, fp = _setup(tmp_path)
    fp.output_directory = str(folder)
    monkeypatch.setattr("fileprep.uploaders.pixhost.pixhost_pending_uploads", lambda: [])
    config = JobConfig()
    config.processing.input.working_directory = str(work)
    done = FilePlan(source_name="done.mp4", source_size=1, source_mtime=0, new_file_name="Done.mp4")
    job = Job(id="j1", name="j1", config=config, state="running",
              plan=JobPlan(created_at=0, working_directory=str(work), config_hash="", inputs_hash="", files=[fp, done]),
              results={fp.source_name: FileResult(source_name=fp.source_name, status="failed", interrupted=True,
                                                  completed_steps=["rename", "cover"], started_at=time.time(),
                                                  final_path=str(folder / f"{base}.mp4")),
                       "done.mp4": FileResult(source_name="done.mp4", status="success", completed_steps=["rename"])})
    (tmp_path / "jobs").mkdir()
    manager = JobManager(jobs_dir=str(tmp_path / "jobs"))
    manager._jobs[job.id] = job
    prompter = Answer("undo")
    with use_runtime(Runtime(config=config, prompter=prompter)):
        asyncio.run(manager._offer_undo_after_cancel("j1", config))
    assert len(prompter.questions) == 1 and "cancelled" in prompter.questions[0].text
    assert (work / "original name.mp4").exists() and not (folder / f"{base}.webp").exists()
    result = manager.get("j1").results[fp.source_name]
    assert "undone" in result.error and not result.interrupted
    assert manager.get("j1").results["done.mp4"].status == "success"  # finished files are not touched
