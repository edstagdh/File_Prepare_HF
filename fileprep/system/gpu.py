"""
GPU (hardware) acceleration: detection, ffmpeg arguments and CPU fallback.

Detection does not trust ``ffmpeg -hwaccels`` / ``-encoders`` alone (they list what the build supports,
not what this machine can run): every backend is tested by decoding a tiny clip and encoding a short
test pattern with each of its H.264 / HEVC / AV1 encoders. The result is cached until re-detected.

Where the GPU is used:
* decoding the source video for thumbnails and preview segments;
* encoding the intermediate H.264 preview segments;
* full video re-encoding when a job selects ``encoding.device = gpu``.
The final WebP / GIF / WebM preview encoders have no GPU versions in ffmpeg and stay on the CPU.
"""

import asyncio
import os
import platform
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional

from loguru import logger

from fileprep.utils.process import run_command

CODECS = ("h264", "hevc", "av1")


@dataclass(frozen=True)
class Backend:
    name: str
    label: str
    decode_args: tuple[str, ...]
    encoders: dict
    vaapi: bool = False  # encoders need -vaapi_device + hwupload


def _backends(vaapi_device: str) -> dict[str, Backend]:
    windows = platform.system() == "Windows"
    vaapi_decode = ("-hwaccel", "vaapi", "-hwaccel_device", vaapi_device)
    vaapi_encoders = {"h264": "h264_vaapi", "hevc": "hevc_vaapi", "av1": "av1_vaapi"}
    return {
        "nvidia": Backend("nvidia", "NVIDIA (CUDA / NVENC)", ("-hwaccel", "cuda"),
                          {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"}),
        "intel": Backend("intel", "Intel (Quick Sync)", ("-hwaccel", "qsv"),
                         {"h264": "h264_qsv", "hevc": "hevc_qsv", "av1": "av1_qsv"}),
        "amd": (Backend("amd", "AMD (D3D11VA / AMF)", ("-hwaccel", "d3d11va"),
                        {"h264": "h264_amf", "hevc": "hevc_amf", "av1": "av1_amf"}) if windows else
                Backend("amd", "AMD (VAAPI)", vaapi_decode, vaapi_encoders, vaapi=True)),
        "apple": Backend("apple", "Apple (VideoToolbox)", ("-hwaccel", "videotoolbox"),
                         {"h264": "h264_videotoolbox", "hevc": "hevc_videotoolbox"}),
        "vaapi": Backend("vaapi", "VAAPI (Linux Intel/AMD)", vaapi_decode, vaapi_encoders, vaapi=True),
    }


def _preference() -> list[str]:
    return {"Windows": ["nvidia", "intel", "amd"], "Darwin": ["apple"]}.get(platform.system(), ["nvidia", "vaapi", "intel"])


@dataclass
class BackendStatus:
    name: str
    label: str
    decode: bool = False
    encoders: dict = field(default_factory=dict)  # codec -> encoder name (working) or None
    error: str = ""


@dataclass
class GpuReport:
    os_name: str
    setting: str
    backends: list[BackendStatus] = field(default_factory=list)
    selected: Optional[str] = None
    checked_at: float = 0.0

    def backend(self) -> Optional[BackendStatus]:
        return next((b for b in self.backends if b.name == self.selected), None)

    def to_dict(self) -> dict:
        return asdict(self)


_cache: Optional[GpuReport] = None
_locks: dict[int, asyncio.Lock] = {}


def _settings():
    from fileprep.config.store import store
    return store.app()


async def _test_decode(backend: Backend, sample: str, ffmpeg: str) -> tuple[bool, str]:
    _, err, code = await run_command([ffmpeg, "-hide_banner", "-loglevel", "error", *backend.decode_args,
                                      "-i", sample, "-f", "null", "-"])
    return code == 0, err.strip().splitlines()[-1] if err.strip() else ""


async def _test_encode(backend: Backend, encoder: str, ffmpeg: str, vaapi_device: str) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if backend.vaapi:
        cmd += ["-vaapi_device", vaapi_device]
    cmd += ["-f", "lavfi", "-i", "testsrc=duration=0.5:size=640x360:rate=25"]
    if backend.vaapi:
        cmd += ["-vf", "format=nv12,hwupload"]
    cmd += ["-c:v", encoder, "-f", "null", "-"]
    _, _, code = await run_command(cmd)
    return code == 0


async def detect(force: bool = False) -> GpuReport:
    """Test every backend of this OS (cached; ``force`` re-detects)."""
    global _cache
    if _cache and not force:
        return _cache
    lock = _locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
    async with lock:
        if _cache and not force:
            return _cache
        app = _settings()
        hw, ffmpeg = app.hardware, app.environment.ffmpeg_path
        report = GpuReport(os_name=platform.system(), setting=hw.gpu, checked_at=time.time())
        stdout, _, _ = await run_command([ffmpeg, "-hide_banner", "-encoders"])
        built_encoders = stdout
        with tempfile.TemporaryDirectory() as tmp:
            sample = os.path.join(tmp, "sample.mp4")
            await run_command([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               "testsrc=duration=1:size=640x360:rate=25", "-pix_fmt", "yuv420p", sample])
            backends = _backends(hw.vaapi_device)
            for name in _preference():
                backend = backends[name]
                status = BackendStatus(name=name, label=backend.label)
                if os.path.isfile(sample):
                    status.decode, status.error = await _test_decode(backend, sample, ffmpeg)
                for codec in CODECS:
                    encoder = backend.encoders.get(codec)
                    ok = bool(encoder) and f" {encoder} " in built_encoders and \
                         await _test_encode(backend, encoder, ffmpeg, hw.vaapi_device)
                    status.encoders[codec] = encoder if ok else None
                report.backends.append(status)
        usable = [b for b in report.backends if b.decode or any(b.encoders.values())]
        if hw.gpu == "auto":
            report.selected = usable[0].name if usable else None
        elif hw.gpu != "off":
            report.selected = hw.gpu if any(b.name == hw.gpu for b in usable) else None
        logger.info(f"GPU detection: selected={report.selected or 'none (CPU)'} "
                    + "; ".join(f"{b.name}: decode={b.decode} enc={[e for e in b.encoders.values() if e]}" for b in report.backends))
        _cache = report
        return report


# ------------------------------------------------------------------ argument builders
async def decode_args(feature: str) -> str:
    """Input-side hwaccel flags (shell string ending with a space) for 'thumbnails' / 'previews'."""
    hw = _settings().hardware
    enabled = {"thumbnails": hw.decode_thumbnails, "previews": hw.decode_previews}.get(feature, False)
    if hw.gpu == "off" or not enabled:
        return ""
    report = await detect()
    backend = report.backend()
    if not backend or not backend.decode:
        return ""
    return " ".join(_backends(hw.vaapi_device)[backend.name].decode_args) + " "


CPU_SEGMENT_ENCODE = "-c:v libx264 -crf 23 -preset fast"


async def segment_encode_args() -> tuple[str, str, str]:
    """(pre-input args, codec args, extra -vf suffix) for the intermediate H.264 preview segments."""
    hw = _settings().hardware
    if hw.gpu == "off" or not hw.encode_preview_segments:
        return "", CPU_SEGMENT_ENCODE, ""
    report = await detect()
    backend_status = report.backend()
    encoder = backend_status.encoders.get("h264") if backend_status else None
    if not encoder:
        return "", CPU_SEGMENT_ENCODE, ""
    backend = _backends(hw.vaapi_device)[backend_status.name]
    pre = f"-vaapi_device {hw.vaapi_device} " if backend.vaapi else ""
    return pre, f"-c:v {encoder} " + " ".join(quality_args(encoder, 23, "fast")), (",format=nv12,hwupload" if backend.vaapi else "")


def quality_args(encoder: str, quality: int, preset: str) -> list[str]:
    """Constant-quality arguments for a GPU encoder (``quality`` ~ CQ/QP 1-51, lower = better)."""
    presets = {"fast": 0, "balanced": 1, "quality": 2}[preset]
    if encoder.endswith("_nvenc"):
        return ["-preset", ("p3", "p5", "p7")[presets], "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
    if encoder.endswith("_qsv"):
        return ["-preset", ("faster", "medium", "slower")[presets], "-global_quality", str(quality)]
    if encoder.endswith("_amf"):
        return ["-quality", ("speed", "balanced", "quality")[presets], "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality)]
    if encoder.endswith("_vaapi"):
        return ["-rc_mode", "CQP", "-qp", str(quality)]
    if encoder.endswith("_videotoolbox"):  # 1-100 scale, higher = better (approximate mapping)
        return ["-q:v", str(max(1, min(100, 100 - quality * 2)))]
    return []


@dataclass
class VideoEncoder:
    """How the full video is encoded: args placed before -i, codec args, -vf suffix, and a label."""
    device: str
    codec: str
    encoder: str
    pre_input: list[str]
    codec_args: list[str]
    vf_suffix: str = ""


async def full_video_encoder(codec: str, device: str, quality: int, preset: str) -> Optional[VideoEncoder]:
    """GPU encoder for a full re-encode, or None when no working encoder exists for ``codec``."""
    if device != "gpu":
        return None
    hw = _settings().hardware
    if hw.gpu == "off":
        return None
    report = await detect()
    status = report.backend()
    encoder = status.encoders.get(codec) if status else None
    if not encoder:
        return None
    backend = _backends(hw.vaapi_device)[status.name]
    pre = (["-vaapi_device", hw.vaapi_device] if backend.vaapi else []) + list(backend.decode_args if status.decode else ())
    return VideoEncoder("gpu", codec, encoder, pre, ["-c:v", encoder, *quality_args(encoder, quality, preset)],
                        ",format=nv12,hwupload" if backend.vaapi else "")


async def run_with_cpu_fallback(feature: str, build: Callable[[str], str],
                                run: Callable[[str], Awaitable[tuple]] = run_command) -> tuple:
    """Run ``build(hw_flags)``; when the GPU variant fails and fallback is on, run ``build("")`` on the CPU."""
    hw_flags = await decode_args(feature)
    result = await run(build(hw_flags))
    if hw_flags and result[2] != 0 and _settings().hardware.cpu_fallback:
        logger.warning(f"GPU decoding failed for {feature} - retrying on the CPU")
        result = await run(build(""))
    return result
