"""Suffix for all files and per-file names applied to an existing preview."""
import asyncio
import time

import pytest

from fileprep.config.models import JobConfig
from fileprep.jobs.manager import JobError, JobManager
from fileprep.jobs.models import Job
from fileprep.pipeline import naming, planner
from fileprep.pipeline.plan import FilePlan, JobPlan, SceneMatch, config_hash


@pytest.fixture
def job(tmp_path, monkeypatch):
    async def not_hevc(*args):
        return False

    monkeypatch.setattr(planner, "is_video_hevc_or_av1", not_hevc)
    for name in ("a.mp4", "b.mp4"):
        (tmp_path / name).write_bytes(b"x")
    config = JobConfig()
    config.processing.input.working_directory = str(tmp_path)
    config.processing.encoding.re_encode = False
    match = SceneMatch(scene_title="Scene", performers=[["Jane Doe", 1]], site="Studio", date="2025-01-01")
    files = [FilePlan(source_name=n, source_size=1, source_mtime=0, match=match.model_copy(), auto_base_name=f"Studio.25.01.01.{who}")
             for n, who in (("a.mp4", "Jane.Doe"), ("b.mp4", "Anna.Lee"))]
    plan = JobPlan(created_at=time.time(), working_directory=str(tmp_path), config_hash="", inputs_hash="x", files=files)
    asyncio.run(planner.reapply_names(config, plan))
    return Job(id="j1", name="j", config=config, state="ready", plan=plan)


def test_clean_suffix():
    assert naming.clean_suffix(" .VR2Normal. ") == "VR2Normal" and naming.clean_suffix("Part 2") == "Part.2"
    with pytest.raises(ValueError):
        naming.clean_suffix("bad/suffix")


def test_suffix_and_custom_name_without_new_preview(job, tmp_path):
    manager = JobManager(jobs_dir=str(tmp_path / "jobs"))
    (tmp_path / "jobs").mkdir()
    manager._jobs["j1"] = job
    assert job.plan.files[0].new_file_name == "Studio.25.01.01.Jane.Doe.mp4"
    manager.set_names("j1", suffix="VR2Normal")
    files = {f.source_name: f for f in manager.get("j1").plan.files}
    assert files["a.mp4"].new_file_name == "Studio.25.01.01.Jane.Doe.VR2Normal.mp4"
    assert files["a.mp4"].title.endswith(" - VR2Normal")
    assert manager.get("j1").state == "ready" and manager.get("j1").plan_is_current  # no new preview needed
    manager.set_names("j1", file_name="b.mp4", new_name="My.Own.Name.mp4")
    files = {f.source_name: f for f in manager.get("j1").plan.files}
    assert files["b.mp4"].new_file_name == "My.Own.Name.mp4" and files["b.mp4"].custom_name
    assert files["a.mp4"].new_file_name.endswith(".VR2Normal.mp4")
    # a custom name equal to another file's new name is blocked
    manager.set_names("j1", file_name="b.mp4", new_name="Studio.25.01.01.Jane.Doe.VR2Normal")
    assert manager.get("j1").state == "blocked"
    assert any(i.code == "DUPLICATE_TARGET" for i in manager.get("j1").plan.files[1].issues)
    manager.set_names("j1", file_name="b.mp4", new_name="")  # back to the TPDB name
    assert manager.get("j1").state == "ready"
    with pytest.raises(JobError):
        manager.set_names("j1", suffix="bad/suffix")


def test_other_setting_change_still_needs_preview(job, tmp_path):
    manager = JobManager(jobs_dir=str(tmp_path / "jobs"))
    (tmp_path / "jobs").mkdir()
    manager._jobs["j1"] = job
    config = job.config.model_copy(deep=True)
    config.processing.outputs.mediainfo_file = not config.processing.outputs.mediainfo_file
    manager.update_config("j1", config)
    assert manager.get("j1").state == "draft"
    assert config_hash(config.model_dump(mode="json")) != manager.get("j1").plan.config_hash


def test_file_name_mode_per_file_without_new_preview(job, tmp_path):
    manager = JobManager(jobs_dir=str(tmp_path / "jobs"))
    (tmp_path / "jobs").mkdir()
    manager._jobs["j1"] = job
    with pytest.raises(JobError):  # preview without the names of every mode (older version)
        manager.set_names("j1", file_name="a.mp4", mode="title")
    for f in job.plan.files:  # what a new preview stores
        who = f.auto_base_name.split(".", 4)[-1]
        f.name_candidates = {"names": f"Studio.25.01.01.{who}", "title": "Studio.25.01.01.Scene",
                             "both": f"Studio.25.01.01.{who}.Scene", "tpdb_id": "Studio.25.01.01.123"}
    manager.set_names("j1", suffix="VR2Normal")
    manager.set_names("j1", file_name="a.mp4", mode="title")
    files = {f.source_name: f for f in manager.get("j1").plan.files}
    assert files["a.mp4"].new_file_name == "Studio.25.01.01.Scene.VR2Normal.mp4" and files["a.mp4"].name_mode == "title"
    assert files["b.mp4"].new_file_name == "Studio.25.01.01.Anna.Lee.VR2Normal.mp4"  # other files keep the job's mode
    assert manager.get("j1").config.processing.naming.filename_mode_overrides == {"a.mp4": "title"}
    assert manager.get("j1").state == "ready" and manager.get("j1").plan_is_current
    manager.set_names("j1", file_name="b.mp4", mode="title")  # same name twice -> blocked
    assert manager.get("j1").state == "blocked"
    manager.set_names("j1", file_name="b.mp4", mode="")  # back to the job's mode
    assert manager.get("j1").state == "ready" and "b.mp4" not in manager.get("j1").config.processing.naming.filename_mode_overrides
    # a too long name falls back like the job's mode does (both -> names)
    job.plan.files[0].name_candidates["both"] = "Studio.25.01.01.Jane.Doe." + "Very.Long.Title." * 4
    job.config.processing.naming.max_filename_length = 40
    job.config.processing.naming.filename_mode_overrides["a.mp4"] = "both"
    files = {f.source_name: f for f in asyncio.run(planner.reapply_names(job.config, job.plan)).files}
    assert files["a.mp4"].new_base_name == "Studio.25.01.01.Jane.Doe"
    assert any(i.code == "NAME_FALLBACK" for i in files["a.mp4"].issues)
    with pytest.raises(JobError):
        manager.set_names("j1", file_name="a.mp4", mode="bogus")
