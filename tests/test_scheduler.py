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
