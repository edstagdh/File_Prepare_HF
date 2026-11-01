"""Failures must not leave temp files behind or fail the file needlessly."""
import asyncio
import os

import httpx

from fileprep.config.models import JobConfig
from fileprep.media import cover, video_preview
from fileprep.runtime import ConsolePrompter, Runtime, use_runtime
from fileprep.uploaders import service


def test_preview_temp_folder_removed_when_generation_raises(tmp_path, monkeypatch):
    async def boom(*args, **kwargs):
        os.makedirs(tmp_path / "base-temp", exist_ok=True)
        (tmp_path / "base-temp" / "segment.mp4").write_bytes(b"x")
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(video_preview, "process_video", boom)
    settings = JobConfig().video_preview
    try:
        asyncio.run(video_preview.process_video_preview(str(tmp_path / "v.mp4"), str(tmp_path), "base", False, settings))
    except RuntimeError:
        pass
    assert not (tmp_path / "base-temp").exists()


def test_cover_unavailable_does_not_fail_the_file(tmp_path, monkeypatch):
    calls = []
    real = httpx.AsyncClient

    def client(**kwargs):
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(502)

        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(cover.httpx, "AsyncClient", client)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(cover.asyncio, "sleep", lambda s: real_sleep(0))
    ok = asyncio.run(cover.cover_image_download_and_conversion("https://site/img.jpg", "https://tpdb/img.jpg", "v.mp4", "v.mp4",
                                                               str(tmp_path), "jpg", False, None, "force regenerate"))
    assert ok is True and len(calls) == 6  # 3 attempts per URL, then continue without cover
    assert not any(p.name.startswith("temp_image") for p in tmp_path.iterdir())


def test_single_host_failure_message(tmp_path):
    from loguru import logger
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    big = tmp_path / "base_preview.gif"
    big.write_bytes(b"0" * (11 * 1024 * 1024))
    config = JobConfig()
    config.processing.image_hosts.preview_hosts = ["pixhost"]
    try:
        with use_runtime(Runtime(config=config, prompter=ConsolePrompter())):
            assert asyncio.run(service.upload_asset(str(big), "base", "Preview GIF", "previews")) == []
    finally:
        logger.remove(sink)
    text = " ".join(messages)
    assert "above pixhost's 10 MB limit" in text and "next host" not in text
