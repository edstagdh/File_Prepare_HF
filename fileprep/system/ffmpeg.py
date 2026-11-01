"""
ffmpeg / ffprobe detection: path, version, build date, required encoders and per-OS update links.

A tool passes when either its semantic version or its build date meets the configured minimum
(legacy rule: git/nightly builds often report only a date).
"""

import platform
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from fileprep.utils.process import run_command

REQUIRED_ENCODERS = {
    "libx265": "HEVC re-encoding",
    "libwebp": "WebP previews",
    "libvpx-vp9": "WebM previews",
    "libsvtav1": "AV1 CPU encoding (fast)",
    "libaom-av1": "AV1 CPU encoding (slow fallback)",
}

UPDATE_LINKS = {
    "Windows": [
        ("gyan.dev builds (recommended)", "https://www.gyan.dev/ffmpeg/builds/"),
        ("BtbN GitHub builds", "https://github.com/BtbN/FFmpeg-Builds/releases"),
        ("winget", "winget upgrade --id Gyan.FFmpeg"),
    ],
    "Darwin": [
        ("Homebrew", "brew upgrade ffmpeg"),
        ("evermeet.cx static builds", "https://evermeet.cx/ffmpeg/"),
    ],
    "Linux": [
        ("John Van Sickle static builds", "https://johnvansickle.com/ffmpeg/"),
        ("BtbN GitHub builds", "https://github.com/BtbN/FFmpeg-Builds/releases"),
        ("Distribution package", "sudo apt install ffmpeg  /  sudo dnf install ffmpeg"),
    ],
}


@dataclass
class ToolStatus:
    name: str
    command: str
    found: bool = False
    path: Optional[str] = None
    version: Optional[str] = None
    build_date: Optional[str] = None
    version_ok: bool = False
    date_ok: bool = False
    ok: bool = False
    raw_first_line: str = ""
    error: Optional[str] = None


@dataclass
class FfmpegReport:
    os_name: str
    ffmpeg: ToolStatus
    ffprobe: ToolStatus
    encoders: dict[str, bool] = field(default_factory=dict)
    update_links: list[tuple[str, str]] = field(default_factory=list)
    checked_at: float = 0.0

    @property
    def ok(self) -> bool:
        return self.ffmpeg.ok and self.ffprobe.ok

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def _parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


async def check_tool(name: str, command: str, min_version: str, min_date: str) -> ToolStatus:
    status = ToolStatus(name=name, command=command)
    status.path = shutil.which(command)
    stdout, stderr, code = await run_command([command, "-version"])
    if code != 0:
        status.error = (stderr or "not found").splitlines()[-1] if (stderr or "").strip() else "not found or failed to run"
        return status
    status.found = True
    output = f"{stdout}\n{stderr}"
    status.raw_first_line = output.strip().splitlines()[0] if output.strip() else ""
    version_match = re.search(r"version\s+n?(\d+)\.(\d+)(?:\.(\d+))?", output)
    if version_match:
        parts = [int(p) for p in version_match.groups() if p is not None]
        status.version = ".".join(map(str, parts))
        status.version_ok = tuple(parts + [0] * (3 - len(parts))) >= _parse_version(min_version)
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", output)
    status.date_ok = True  # legacy rule: no build date -> date check passes
    if date_match:
        status.build_date = date_match.group(1)
        try:
            status.date_ok = datetime.strptime(status.build_date, "%Y-%m-%d") >= datetime.strptime(min_date, "%Y-%m-%d")
        except ValueError:
            pass
    status.ok = status.version_ok or status.date_ok
    return status


async def list_encoders(command: str) -> dict[str, bool]:
    stdout, _, code = await run_command([command, "-hide_banner", "-encoders"])
    if code != 0:
        return {encoder: False for encoder in REQUIRED_ENCODERS}
    return {encoder: bool(re.search(rf"\s{re.escape(encoder)}\s", stdout)) for encoder in REQUIRED_ENCODERS}


_cache: Optional[FfmpegReport] = None


async def check_ffmpeg(force: bool = False, max_age: float = 300) -> FfmpegReport:
    """Full ffmpeg/ffprobe report (cached for ``max_age`` seconds)."""
    global _cache
    if _cache and not force and time.time() - _cache.checked_at < max_age:
        return _cache
    from fileprep.config.store import store
    env = store.app().environment
    ffmpeg = await check_tool("ffmpeg", env.ffmpeg_path, env.ffmpeg_min_version, env.ffmpeg_min_build_date)
    ffprobe = await check_tool("ffprobe", env.ffprobe_path, env.ffmpeg_min_version, env.ffmpeg_min_build_date)
    encoders = await list_encoders(env.ffmpeg_path) if ffmpeg.found else {e: False for e in REQUIRED_ENCODERS}
    os_name = platform.system()
    _cache = FfmpegReport(os_name=os_name, ffmpeg=ffmpeg, ffprobe=ffprobe, encoders=encoders,
                          update_links=UPDATE_LINKS.get(os_name, UPDATE_LINKS["Linux"]), checked_at=time.time())
    return _cache


async def verify_ffmpeg_and_ffprobe() -> tuple[bool, int]:
    """Legacy-compatible check used by the CLI: (ok, exit_code)."""
    report = await check_ffmpeg(force=True)
    if report.ok:
        return True, 0
    failing = report.ffmpeg if not report.ffmpeg.ok else report.ffprobe
    return False, 22 if not failing.found else 33
