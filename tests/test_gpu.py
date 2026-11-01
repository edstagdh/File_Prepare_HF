"""GPU backend selection, encoder arguments, CPU fallback and the encoding settings migration."""
import asyncio

from fileprep.config.models import EncodingSettings
from fileprep.system import gpu


def fake_report(selected, decode=True, encoders=None):
    status = gpu.BackendStatus(name=selected, label=selected, decode=decode,
                               encoders=encoders or {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": None})
    return gpu.GpuReport(os_name="Windows", setting="auto", backends=[status], selected=selected)


def test_legacy_re_encode_flag_is_mapped():
    assert EncodingSettings.model_validate({"re_encode_hevc": False}).re_encode is False
    e = EncodingSettings()
    assert e.re_encode and e.codec == "hevc" and e.device == "cpu"


def test_quality_args_per_vendor():
    assert gpu.quality_args("hevc_nvenc", 26, "balanced") == ["-preset", "p5", "-rc", "vbr", "-cq", "26", "-b:v", "0"]
    assert gpu.quality_args("av1_qsv", 30, "quality")[-2:] == ["-global_quality", "30"]
    assert "-qp_i" in gpu.quality_args("hevc_amf", 24, "fast")
    assert gpu.quality_args("hevc_videotoolbox", 25, "balanced") == ["-q:v", "50"]


def test_full_video_encoder_selection(monkeypatch):
    async def detect(force=False):
        return fake_report("nvidia")

    monkeypatch.setattr(gpu, "detect", detect)
    enc = asyncio.run(gpu.full_video_encoder("hevc", "gpu", 26, "balanced"))
    assert enc.encoder == "hevc_nvenc" and enc.pre_input == ["-hwaccel", "cuda"] and "-cq" in enc.codec_args
    assert asyncio.run(gpu.full_video_encoder("av1", "gpu", 26, "balanced")) is None  # no working AV1 encoder
    assert asyncio.run(gpu.full_video_encoder("hevc", "cpu", 26, "balanced")) is None


def test_decode_flags_and_cpu_fallback(monkeypatch):
    async def detect(force=False):
        return fake_report("nvidia")

    monkeypatch.setattr(gpu, "detect", detect)
    assert asyncio.run(gpu.decode_args("thumbnails")) == "-hwaccel cuda "
    commands = []

    async def run(cmd):
        commands.append(cmd)
        return "", "", 1 if "cuda" in cmd else 0

    result = asyncio.run(gpu.run_with_cpu_fallback("thumbnails", lambda hw: f"ffmpeg {hw}-i x", run=run))
    assert result[2] == 0 and commands == ["ffmpeg -hwaccel cuda -i x", "ffmpeg -i x"]
