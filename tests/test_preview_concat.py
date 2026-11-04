import asyncio
import shutil
import subprocess

import pytest

from fileprep.config.models import TRANSITIONS
from fileprep.media import video_preview as vp

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.mark.parametrize("mode", ["fade", "wipeleft", "random", "none"])
def test_segments_are_joined_in_one_pass(tmp_path, mode):
    files = []
    for i in range(4):
        f = tmp_path / f"s{i}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24",
                        "-t", "1.5", "-c:v", "libx264", "-preset", "ultrafast", str(f)], check=True)
        files.append(f)
    concat = tmp_path / "list.txt"
    concat.write_text("".join(f"file '{f}'\n" for f in files))
    ok, out = asyncio.run(vp.concat_video_segments(str(concat), str(tmp_path / "out.mp4"), mode, TRANSITIONS, 0.25))
    assert ok
    length = asyncio.run(vp._media_duration(out))
    expected = 4 * 1.5 - (0 if mode == "none" else 3 * 0.25)
    assert abs(length - expected) < 0.2, (mode, length)


def test_segments_with_different_formats_are_normalised(tmp_path):
    """GPU-encoded segments can differ in frame rate, time base, size, SAR or pixel format - xfade needs them equal."""
    specs = [("30000/1001", "320x180", "yuv420p", "1:1", "libx264"), ("30", "320x180", "yuv420p", "1:1", "mpeg4"),
             ("25", "322x180", "yuv444p", "4:3", "libx264")]
    files = []
    for i, (rate, size, pix, sar, codec) in enumerate(specs):
        f = tmp_path / f"s{i}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}",
                        "-t", "1.5", "-vf", f"setsar={sar}", "-pix_fmt", pix, "-c:v", codec, str(f)], check=True)
        files.append(f)
    concat = tmp_path / "list.txt"
    concat.write_text("".join(f"file '{f}'\n" for f in files))
    ok, out = asyncio.run(vp.concat_video_segments(str(concat), str(tmp_path / "out.mp4"), "fade", TRANSITIONS, 0.25))
    assert ok and abs(asyncio.run(vp._media_duration(out)) - (4.5 - 0.5)) < 0.2


def test_ffmpeg_error_keeps_the_reason():
    stderr = ("[Parsed_xfade_3 @ 0x1] First input link main frame rate (30000/1001) do not match the corresponding second "
              "input link xfade frame rate (30/1)\n[fc#0 @ 0x2] Error configuring filter graph\n"
              "frame=    0 fps=0.0 q=0.0 Lsize=       0KiB\nConversion failed!")
    text = vp.ffmpeg_error(stderr)
    assert "frame rate (30000/1001) do not match" in text and "Lsize" not in text
