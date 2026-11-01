import asyncio

from PIL import Image, features

from fileprep.config.models import JobConfig, TorrentSettings
from fileprep.media.cover import convert_image_format
from fileprep.pipeline import planner
from fileprep.uploaders import service


def test_pillow_and_hosts_support_avif():
    assert features.check("avif")
    assert service.supports("pixhost", "a.avif")
    assert not service.supports("hamster", "a.avif") and not service.supports("imgbb", "a.avif")


def test_convert_cover_to_avif(tmp_path):
    source = tmp_path / "cover.jpg"
    Image.new("RGB", (64, 48), "red").save(source)
    ok, path = asyncio.run(convert_image_format(str(source), str(tmp_path), "avif"))
    assert ok and path.endswith(".avif") and Image.open(path).format == "AVIF"


def test_avif_previews_planned_and_excluded_from_torrent(tmp_path):
    job = JobConfig()
    job.processing.outputs.video_preview = True
    job.video_preview.create_avif_preview = job.video_preview.create_avif_sheet = True
    names = [o.path for o in planner._outputs(job, str(tmp_path), "Base", "")]
    assert any(n.endswith("Base_preview.avif") for n in names) and any(n.endswith("Base_preview_sheet.avif") for n in names)
    # lists saved before AVIF support get the AVIF suffixes next to the WebP ones
    old = TorrentSettings(excluded_suffixes=["_preview.webp", "_preview_sheet.webp", "_template.txt"])
    assert old.excluded_suffixes == ["_preview.webp", "_preview.avif", "_preview_sheet.webp", "_preview_sheet.avif", "_template.txt"]
