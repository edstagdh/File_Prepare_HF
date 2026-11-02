import time

from fileprep.config.models import JobConfig
from fileprep.jobs.manager import JobManager
from fileprep.jobs.models import Job


def make(job_id, directory, device):
    config = JobConfig()
    config.processing.input.working_directory = directory
    config.processing.encoding.re_encode = bool(device)
    config.processing.encoding.device = device or "cpu"
    return Job(id=job_id, name=job_id, config=config, state="queued", queued_at=time.time())


def test_one_cpu_and_one_gpu_encode_job_run(tmp_path, monkeypatch):
    manager = JobManager(jobs_dir=str(tmp_path))
    spawned = []
    monkeypatch.setattr(manager, "_spawn", lambda job, mode: spawned.append(job.id))
    for job in (make("a", "/a", "cpu"), make("b", "/b", "cpu"), make("c", "/c", None), make("d", "/c", None), make("e", "/e", "gpu")):
        manager._jobs[job.id] = job
        time.sleep(0.001)
    manager._schedule()
    assert spawned == ["a", "c", "e"]  # b waits for the CPU slot, d for directory /c, e uses the GPU slot
    assert manager.get("b").state == "queued" and "CPU" in manager.get("b").progress.detail
    assert manager.get("d").progress.detail == "Waiting: directory in use"
    manager._jobs["a"].state = "completed"
    manager._schedule()
    assert spawned == ["a", "c", "e", "b"]


def test_parent_and_child_folders_do_not_block_each_other(tmp_path, monkeypatch):
    import asyncio
    from fileprep.config.models import JobConfig
    from fileprep.pipeline import planner
    from fileprep.pipeline.plan import FilePlan, JobPlan
    parent, child = tmp_path / "X", tmp_path / "X" / "Y"
    child.mkdir(parents=True)
    manager = JobManager(jobs_dir=str(tmp_path / "jobs"))
    monkeypatch.setattr(manager, "_spawn", lambda job, mode: None)
    running = make("y", str(child), "cpu")
    running.state = "running"
    running.plan = JobPlan(created_at=0, working_directory=str(child), config_hash="", inputs_hash="",
                           files=[FilePlan(source_name="a.mp4", source_size=1, source_mtime=0, output_directory=str(child / "Studio.A"))])
    manager._jobs["y"] = running
    used = manager.active_directories("x")
    assert set(used) == {str(child), str(child / "Studio.A")}

    config = JobConfig()
    config.processing.input.working_directory = str(parent)
    plan = asyncio.run(planner.build_plan(config, used))
    assert not any(i.code == "DIRECTORY_IN_USE" for i in plan.issues)  # parent folder is free
    config.processing.input.working_directory = str(child)
    plan = asyncio.run(planner.build_plan(config, used))
    assert any(i.code == "DIRECTORY_IN_USE" for i in plan.issues)  # same folder is not

    # a file of the parent job whose sub folder would be the child job's folder conflicts
    plan = JobPlan(created_at=0, working_directory=str(parent), config_hash="", inputs_hash="",
                   files=[FilePlan(source_name="Y.mp4", source_size=1, source_mtime=0, output_directory=str(child))])
    planner._detect_job_conflicts(plan, {planner._norm(d): j for d, j in used.items()})
    assert plan.files[0].status == "error" and plan.files[0].issues[0].code == "OUTPUT_DIRECTORY_IN_USE"
