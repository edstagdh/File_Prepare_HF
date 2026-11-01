import asyncio
import json

from fileprep import runtime
from fileprep.config.models import JobConfig
from fileprep.runtime import ConsolePrompter, Runtime, use_runtime
from fileprep.uploaders import service


def _run(config, file_path, calls, results):
    async def fake_upload(host, path, base, label):
        calls.append(host)
        return results[host]

    service._upload, original = fake_upload, service._upload
    try:
        with use_runtime(Runtime(config=config, prompter=ConsolePrompter())):
            return asyncio.run(service.upload_asset(str(file_path), "base", "cover", "cover"))
    finally:
        service._upload = original


def test_fallback_stops_at_first_success(tmp_path):
    image = tmp_path / "base.webp"
    image.write_bytes(b"x")
    config = JobConfig()
    config.processing.image_hosts.mode = "fallback"
    config.processing.image_hosts.cover_hosts = ["imgbox", "hamster", "pixhost", "imgbb"]
    calls = []
    ok = _run(config, image, calls, {"hamster": False, "pixhost": True, "imgbb": True, "imgbox": True})
    assert ok == ["pixhost"] and calls == ["hamster", "pixhost"]  # imgbox skipped: no webp support


def test_all_mode_uploads_everywhere(tmp_path):
    image = tmp_path / "base.jpg"
    image.write_bytes(b"x")
    config = JobConfig()
    config.processing.image_hosts.mode = "all"
    config.processing.image_hosts.cover_hosts = ["hamster", "pixhost"]
    calls = []
    assert _run(config, image, calls, {"hamster": True, "pixhost": True}) == ["hamster", "pixhost"]


def test_find_uploaded_url_respects_host_order(tmp_path):
    (tmp_path / "base_imgbb.txt").write_text(json.dumps({"base - cover": [{"direct_link": "https://imgbb/x.webp"}]}))
    (tmp_path / "base_pixhost.txt").write_text(json.dumps({"base - cover": [{"image_url": "https://pix/x.webp"}]}))
    assert service.find_uploaded_url(str(tmp_path), "base", "cover", ["pixhost", "imgbb"]) == "https://pix/x.webp"
    assert service.find_uploaded_url(str(tmp_path), "base", "cover", ["imgbb", "pixhost"]) == "https://imgbb/x.webp"
    assert service.find_uploaded_url(str(tmp_path), "base", "thumbnails") is None
    assert runtime.suggest_choices("Keep it? [K/R]: ") == ["k", "r"]
