"""No-match vs connection failure handling, UUID overrides and the move-out / UUID job actions."""
import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest

from fileprep.config.models import JobConfig
from fileprep.jobs.manager import JobError, JobManager
from fileprep.jobs.models import Job
from fileprep.pipeline import matching
from fileprep.pipeline.naming import detect_flags
from fileprep.pipeline.plan import FilePlan, JobPlan
from fileprep.runtime import Question, Runtime, use_runtime
from fileprep.tpdb import api_client
from fileprep.tpdb.api_client import TpdbConnectionError, send_request


class Answer:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def ask(self, question: Question) -> str:
        self.asked.append(question.text)
        return self.answer


def call(handler, answer="n"):
    real = httpx.AsyncClient
    api_client.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    prompter = Answer(answer)
    try:
        with use_runtime(Runtime(config=JobConfig(), prompter=prompter)):
            return asyncio.run(send_request("https://api.test/scenes", "t", "q", 2, 0)), prompter
    finally:
        api_client.httpx.AsyncClient = real


def test_404_is_no_result_without_retry():
    hits = []
    result, prompter = call(lambda r: hits.append(1) or httpx.Response(404))
    assert result is None and len(hits) == 1 and not prompter.asked


def test_auth_failure_raises():
    with pytest.raises(TpdbConnectionError):
        call(lambda r: httpx.Response(401))


def test_network_failure_raises_after_retries_and_user_decline():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TpdbConnectionError):
        call(handler, answer="n")


def test_server_error_then_success_after_user_retry():
    responses = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"data": [{"id": "x"}]})])
    result, prompter = call(lambda r: next(responses), answer="y")
    assert result == {"data": [{"id": "x"}]} and len(prompter.asked) == 1


def test_no_match_is_a_skip_warning_and_uuid_override_is_used(tmp_path, monkeypatch):
    seen = {}

    async def fake_query_api(*args, **kwargs):
        seen["uuid"] = kwargs["existing_tpdb_uuid"]
        return (None,) * 9 + ("0000-00-00", None, None, None, None)

    monkeypatch.setattr(matching, "query_api", fake_query_api)
    video = tmp_path / "A.24.01.01.X.mp4"
    video.write_bytes(b"x")
    settings = JobConfig().processing
    settings.matching.rematch_using_embedded_uuid = False
    settings.matching.uuid_overrides = {video.name: "11111111-2222-3333-4444-555555555555"}
    match, issues = asyncio.run(matching.resolve_match(video, detect_flags(video.name), settings, None))
    assert match is None and issues[0].level == "warning" and issues[0].code == "E38_NO_MATCH"
    assert seen["uuid"] == "11111111-2222-3333-4444-555555555555"


def _job_with_plan(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    config = JobConfig()
    config.processing.input.working_directory = str(tmp_path)
    job = Job(id="j1", name="j", config=config, state="ready")
    job.plan = JobPlan(created_at=time.time(), working_directory=str(tmp_path), config_hash="x", inputs_hash="y",
                       files=[FilePlan(source_name="a.mp4", source_size=1, source_mtime=0, status="skipped")])
    return job


def test_set_uuid_and_move_out(tmp_path):
    manager = JobManager(jobs_dir=str(tmp_path / "jobs"))
    os.makedirs(manager.jobs_dir)
    manager._jobs["j1"] = _job_with_plan(tmp_path)
    with pytest.raises(JobError):
        manager.set_uuid("j1", "a.mp4", "not-a-uuid")
    with pytest.raises(JobError):
        manager.move_out("j1", "../etc.mp4")
    manager.set_uuid("j1", "a.mp4", "11111111-2222-3333-4444-555555555555")
    assert manager.get("j1").config.processing.matching.uuid_overrides["a.mp4"].startswith("1111")
    assert manager.get("j1").state == "draft"
    target = manager.move_out("j1", "a.mp4")
    assert Path(target) == tmp_path / "_excluded" / "a.mp4" and Path(target).exists() and not (tmp_path / "a.mp4").exists()
    assert "a.mp4" not in manager.get("j1").config.processing.matching.uuid_overrides
