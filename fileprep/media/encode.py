"""HEVC (x265) re-encoding, codec detection and self-encode signature checks."""
import math
import os
import re
import shutil
import time
import asyncio
import contextlib
import tempfile
from loguru import logger

from fileprep import runtime
from fileprep.config.models import EncodingSettings
from fileprep.media import encode_ledger
from fileprep.system import gpu
from fileprep.system.ffmpeg import check_ffmpeg
from pymediainfo import MediaInfo
from tqdm import tqdm
from fileprep.media.probe import get_frame_rate, get_video_duration, get_video_resolution, get_video_resolution_and_orientation
from fileprep.utils.process import run_command

X265_VERSION_RE = re.compile(r'x265\s+(?P<version>[^\s:]+)')

# Cache for the current machine's detected x265/ffmpeg encoder signature
# (see get_current_x265_encoder_signature). Populated at most once per
# process run.
_X265_SELF_PROBE_LOCKS: dict[int, asyncio.Lock] = {}  # one lock per event loop (each job runs its own loop)


def _probe_lock() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    return _X265_SELF_PROBE_LOCKS.setdefault(loop_id, asyncio.Lock())


_x265_self_probe_cache = None


async def re_encode_video(new_filename, directory, is_vertical, encoding: EncodingSettings):
    """
    Re-encode ``directory/new_filename`` with the job's codec (HEVC / AV1) on the selected device (CPU / GPU)
    and replace the original (kept as *_old.mp4 when ``keep_original_file``). Returns True on success.
    """
    file_path = os.path.join(directory, new_filename)
    if is_vertical is None:
        _, is_vertical = await get_video_resolution_and_orientation(str(file_path))
    if encoding.device == "cpu" and encoding.codec == "hevc" and not (0 <= encoding.crf < 30):
        logger.error(f"processing failed for {file_path}, unexpected CRF value: {encoding.crf}")
        return False

    gpu_encoder = None
    if encoding.device == "gpu":
        gpu_encoder = await gpu.full_video_encoder(encoding.codec, "gpu", encoding.gpu_quality, encoding.gpu_preset)
        if gpu_encoder is None:
            if not encoding.fallback_to_cpu:
                logger.error(f"No working GPU {encoding.codec.upper()} encoder (see Tools > ffmpeg) and CPU fallback is off.")
                return False
            logger.warning(f"No working GPU {encoding.codec.upper()} encoder - encoding on the CPU instead.")

    temp_output = await encode_video_file(file_path, is_vertical, encoding, gpu_encoder)
    if temp_output is False and gpu_encoder and encoding.fallback_to_cpu:
        logger.warning("GPU encoding failed - retrying on the CPU.")
        gpu_encoder = None
        temp_output = await encode_video_file(file_path, is_vertical, encoding, None)
    if temp_output is False:
        logger.error(f"processing failed for {file_path}")
        return False

    try:
        if encoding.keep_original_file:
            old_file_path = os.path.join(directory, f"{os.path.splitext(new_filename)[0]}_old{os.path.splitext(new_filename)[1]}")
            os.rename(file_path, old_file_path)
            logger.info(f"Original file renamed to: {old_file_path}")
        else:
            os.remove(file_path)
        final_output = os.path.join(directory, new_filename)
        shutil.move(temp_output, final_output)
        logger.info(f"Replaced original file with the {encoding.codec.upper()} version: {final_output}")
    except Exception as e:
        logger.error(f"Failed to replace {file_path}: {e}")
        return False
    encoder_name = gpu_encoder.encoder if gpu_encoder else await cpu_encoder_name(encoding.codec)
    await encode_ledger.record(final_output, {"codec": encoding.codec, "device": "gpu" if gpu_encoder else "cpu",
                                              "encoder": encoder_name})
    return True


async def cpu_encoder_name(codec: str) -> str:
    if codec == "hevc":
        return "libx265"
    report = await check_ffmpeg()
    return "libsvtav1" if report.encoders.get("libsvtav1") else "libaom-av1"


async def encode_video_file(file_path, is_vertical, encoding: EncodingSettings, gpu_encoder: "gpu.VideoEncoder | None"):
    """
    Encode one file to a temporary file, reporting progress to the job (and a tqdm bar on a terminal).
    The ffmpeg process is killed and the partial output removed when the job is cancelled.

    Returns the temporary file path, or False on failure.
    """
    width, height, bit_rate = await get_video_resolution(file_path)
    directory, filename = os.path.split(file_path)
    temp_output = await generate_temp_filename(directory, filename)
    if os.path.exists(temp_output):  # left over by an interrupted run - ffmpeg would ask before overwriting
        logger.warning(f"Removing stale temporary file: {temp_output}")
        os.remove(temp_output)
    duration, fps = await get_video_duration(file_path)
    duration = int(duration)
    keyint = int(fps * 2)  # every 2 seconds
    threads = max(1, math.ceil(os.cpu_count() * encoding.cpu_usage_percent / 100))
    limit = encoding.limit_cpu_usage and not gpu_encoder

    if gpu_encoder:
        pre_input, video_args, vf_suffix = gpu_encoder.pre_input, [*gpu_encoder.codec_args, "-g", str(keyint)], gpu_encoder.vf_suffix
        stage = f"{encoding.codec.upper()} encode (GPU {gpu_encoder.encoder})"
    elif encoding.codec == "hevc":
        x265_params = (
            f"crf={encoding.crf}:"
            "preset=medium:"
            "ref=3:"
            "limit-refs=2:"
            f"keyint={keyint}:"
        )
        if limit:
            x265_params += f"pools={threads}:"
        pre_input, video_args, vf_suffix = [], ["-c:v", "libx265", "-x265-params", x265_params], ""
        stage = "HEVC encode (CPU)"
    else:
        if await cpu_encoder_name("av1") == "libsvtav1":
            video_args = ["-c:v", "libsvtav1", "-crf", str(encoding.av1_crf), "-preset", str(encoding.av1_preset), "-g", str(keyint)]
            if limit:
                video_args += ["-svtav1-params", f"lp={threads}"]
        else:
            video_args = ["-c:v", "libaom-av1", "-crf", str(encoding.av1_crf), "-b:v", "0", "-cpu-used", "6", "-row-mt", "1",
                          "-g", str(keyint)] + (["-threads", str(threads)] if limit else [])
        pre_input, vf_suffix = [], ""
        stage = f"AV1 encode (CPU {video_args[1]})"
    if encoding.codec == "hevc":
        video_args += ["-vtag", "hvc1"]

    ffmpeg_cmd = ["ffmpeg", "-hide_banner", *pre_input, "-i", file_path, "-map", "0:v:0", "-map", "0:a?", *video_args,
                  "-c:a", "aac", "-b:a", "128k", "-map_metadata", "-1"]
    ffmpeg_cmd += ["-map_chapters", "-1" if encoding.remove_existing_chapters else "0"]
    ffmpeg_cmd += ["-dn", "-sn", ]

    scale = ""
    if encoding.downscale_to_1080p and width and height:
        if not is_vertical and (width > 1920 or height > 1080):
            scale = "scale='min(1920,iw)':'min(1080,ih)'"
        elif is_vertical and height > 1080:
            scale = "scale=-2:1080"
    vf = (scale + vf_suffix).lstrip(",")
    if vf:
        ffmpeg_cmd += ["-vf", vf]
    ffmpeg_cmd.append(temp_output)
    logger.info(f"Encoding with {stage}")

    time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
    size_pattern = re.compile(r"size=\s*(\d+)KiB")
    speed_pattern = re.compile(r"speed=([\d\.x]+)")

    start_time = time.time()
    last_update = 0
    stderr_tail: list[str] = []
    pbar = tqdm(total=duration, unit="s", ncols=120, bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}s",
                disable=runtime.current().job_id is not None)  # web jobs report progress to the UI instead

    def handle_line(line: str) -> None:
        nonlocal last_update
        stderr_tail.append(line)
        del stderr_tail[:-20]
        now = time.time()
        if now - last_update < 3:  # update every 3 seconds
            return
        t_match = time_pattern.search(line)
        s_match = size_pattern.search(line)
        sp_match = speed_pattern.search(line)
        if not (t_match and s_match):
            return
        encoded_time = parse_ffmpeg_time(t_match.group(1))
        current_size_kib = int(s_match.group(1))
        try:
            speed = float(sp_match.group(1).replace('x', '')) if sp_match else 1.0
        except ValueError:
            speed = 1.0
        speed = speed or 1.0
        elapsed = now - start_time
        remaining = max(0, duration / speed - elapsed)
        predicted_size = format_size((current_size_kib / encoded_time) * duration) if encoded_time > 0 else "estimating…"
        msg = (f"Speed: {speed:.2f}x ETA: {format_eta(int(remaining))} "
               f"Est Size: {predicted_size} Bitrate: {format_bitrate(current_size_kib, encoded_time)}")
        pbar.update(encoded_time - pbar.n)  # jump to current second
        pbar.set_description(msg)
        runtime.report_progress(stage, min(100.0, encoded_time * 100 / duration) if duration else None, msg)
        last_update = now

    process = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE)
    try:
        buffer = ""
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")
            *lines, buffer = re.split(r"[\r\n]", buffer)  # ffmpeg ends progress lines with \r
            for line in lines:
                if line.strip():
                    handle_line(line)
        await process.wait()
    except asyncio.CancelledError:
        logger.warning(f"Encoding cancelled, stopping ffmpeg for {file_path}")
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=10)
        with contextlib.suppress(OSError):
            if os.path.exists(temp_output):
                os.remove(temp_output)
        raise
    finally:
        pbar.close()

    if process.returncode != 0 or not os.path.exists(temp_output):
        with contextlib.suppress(OSError):
            if os.path.exists(temp_output):
                os.remove(temp_output)  # partial output of a failed encode
        logger.error(f"Re-encoding failed for {file_path} (code {process.returncode}): {' | '.join(stderr_tail[-5:])}")
        return False

    return temp_output


async def is_video_hevc_or_av1(file_path: str, warn_CRF_match: bool, re_encode_hevc_CRF: int) -> bool:
    """
    Check if the video is encoded with HEVC or AV1 using pymediainfo.
    Log codec and CRF if detected.
    Return True if HEVC or AV1, False otherwise.
    """

    if not os.path.isfile(file_path):
        logger.error(f"File does not exist: {file_path}")
        return False

    try:
        media_info = MediaInfo.parse(file_path)
    except Exception as e:
        logger.error(f"Failed to parse file with pymediainfo: {file_path}: {e}")
        return False

    video_track = None
    for track in media_info.tracks:
        if track.track_type == "Video":
            video_track = track
            break

    if not video_track:
        logger.error(f"No video track found: {file_path}")
        return False

    codec = (video_track.format or "").lower()
    codec_clean = codec.replace(" ", "").replace("-", "")

    is_hevc = codec_clean in {"hevc", "hvc1", "hev1"}
    is_av1 = codec_clean == "av1"

    # Extract CRF from encoding settings
    crf_value = None
    encoding_settings = getattr(video_track, "encoding_settings", "") or ""

    if encoding_settings:
        # Look for patterns like `crf=24.0`
        match = re.search(r"crf\s*=\s*([0-9]+(?:\.[0-9]+)?)", encoding_settings.lower())
        if match:
            # Convert 24.0 → 24
            crf_value = int(float(match.group(1)))
            logger.info(f"Detected CRF {crf_value} for {file_path}")

    # Log HEVC / AV1 detection
    if is_hevc:
        if crf_value:
            if crf_value != re_encode_hevc_CRF and warn_CRF_match:
                logger.warning(f"CRF value of file: {file_path} does not match configured CRF value - file: {crf_value} vs Configured CRF value: {re_encode_hevc_CRF}")
        else:
            logger.warning(f"CRF value not found in file: {file_path}")
        logger.info(f"{file_path} detected as HEVC (H.265). CRF={crf_value}")
        return True

    if is_av1:
        logger.info(f"{file_path} detected as AV1. CRF={crf_value}")
        return True

    # logger.info(f"{file_path} codec is '{codec}', not HEVC/AV1")
    return False


def parse_ffmpeg_time(time_str):
    """Convert HH:MM:SS.xx to seconds."""
    try:
        parts = time_str.split(':')
        seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return seconds
    except Exception:
        return 0


def format_eta(seconds):
    """Format seconds into human-readable ETR like '2m 15s'."""
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    elif minutes:
        return f"{minutes}m {sec}s"
    else:
        return f"{sec}s"


def format_size(size_kib):
    """Format file size in MB or KB based on the size."""
    if size_kib < 1024:
        return f"{size_kib} KB"
    size_mb = size_kib / 1024
    return f"{size_mb:.0f} MB"


def format_bitrate(size_kib, elapsed_time):
    """Calculate the bitrate from size and elapsed time and format it."""
    if elapsed_time <= 0:
        return "N/A"
    bitrate_kbps = (size_kib * 8) / elapsed_time
    return f"{bitrate_kbps / 1000:.2f} Mbps"


async def generate_temp_filename(directory, original_name):
    """Generate a temporary filename for re-encoded file."""
    name, ext = os.path.splitext(original_name)
    return os.path.join(directory, f"{name}_temp{ext}")


def _parse_x265_settings(settings_str):
    """Parse a x265 'Encoding settings' tag string into a dict of key -> value."""
    settings = {}
    if not settings_str:
        return settings
    for part in settings_str.split(' / '):
        part = part.strip()
        if not part:
            continue
        if '=' in part:
            key, _, value = part.partition('=')
            settings[key.strip()] = value.strip()
        else:
            # bare flags like "wpp" / "no-pmode"
            if part.startswith('no-'):
                settings[part[3:]] = 'False'
            else:
                settings[part] = 'True'
    return settings


def _extract_x265_version(settings_str):
    if not settings_str:
        return None
    match = X265_VERSION_RE.search(settings_str)
    return match.group('version') if match else None


async def get_encoder_settings_tag(file_path):
    """Return the raw x265 'Encoding settings' string from the video track, or None."""
    try:
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == "Video":
                settings = getattr(track, "encoding_settings", None)
                return settings.strip() if settings else None
        return None
    except Exception:
        logger.exception(f"Error retrieving encoder settings tag from {file_path}")
        return None


async def get_video_writing_library(file_path):
    """
    Return the raw 'Writing library' tag from the VIDEO track specifically
    (not the General track, whose writing_application/writing_library
    describes the muxer, e.g. 'Lavf60.16.100' -- see
    check_existing_Encoder_Library for that unrelated check). For x265
    encodes this is the full encoder build string, e.g.:
        x265 3.6+103-792de227f:[Windows][GCC 14.2.0][64 bit] 8bit+10bit+12bit
    Returns None if there's no video track or no writing_library tag.
    """
    try:
        media_info = MediaInfo.parse(file_path)

        for track in media_info.tracks:
            if track.track_type == "Video":
                writing_library = getattr(track, "writing_library", None)
                return writing_library.strip() if writing_library else None

        return None

    except Exception:
        logger.exception(f"Error retrieving video writing library from {file_path}")
        return None


async def get_current_x265_encoder_signature():
    """
    Detect the video-track 'Writing library' string that THIS machine's
    currently installed libx265/ffmpeg build produces, by running a
    throwaway one-frame encode to a temp file and reading its tag back via
    get_video_writing_library() -- the exact same extraction path used to
    read every other file's tag, so both sides of every comparison in
    check_self_encoded() go through identical parsing.

    Cached for the lifetime of the process: populated on first use behind
    a per-event-loop asyncio.Lock (so two files racing to probe at once don't run the
    encode twice), meaning the throwaway encode runs at most once per run.
    Simply re-running the app after upgrading ffmpeg/libx265 picks up the
    new signature automatically -- nothing to configure or keep in sync
    manually.

    Returns:
        {"writing_library": str | None, "version": str | None}
        Both None if the probe itself failed (e.g. libx265 unavailable).
        This only ever ADDS points in check_self_encoded, never subtracts,
        so a failed probe just means those two signals stay unavailable
        for this run -- everything else keeps working.
    """
    global _x265_self_probe_cache

    if _x265_self_probe_cache is not None:
        return _x265_self_probe_cache

    async with _probe_lock():
        # Another caller may have populated it while we waited for the lock.
        if _x265_self_probe_cache is not None:
            return _x265_self_probe_cache

        result = {"writing_library": None, "version": None}
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        temp_path = temp_file.name
        temp_file.close()

        try:
            probe_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                "-frames:v", "1",
                "-c:v", "libx265",
                temp_path,
            ]
            stdout, stderr, code = await run_command(probe_cmd)

            if code == 0 and os.path.exists(temp_path):
                writing_library = await get_video_writing_library(temp_path)
                result["writing_library"] = writing_library
                result["version"] = _extract_x265_version(writing_library) if writing_library else None
            else:
                logger.warning(f"x265 self-encode signature probe failed (exit_code={code}): {stderr}")

        except Exception:
            logger.exception("Error probing current x265 encoder signature")

        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    logger.warning(f"Failed to remove x265 probe temp file: {temp_path}")

        _x265_self_probe_cache = result
        return result


async def check_self_encoded(file_path, fps=None):
    """
    Inspect the embedded x265 settings and decide whether they match this
    pipeline's own encode profile (SELF) vs. a third-party file (PRT).

    Signals, most to least reliable:
      1. GOP structure: SELF uses keyint ~= round(2*fps) with min-keyint well
         below fps; x265's own default (used by most other encodes) sets
         min-keyint=fps and keyint=10*fps.
      2. Writing library (video track) exact match against what THIS
         machine's currently installed libx265/ffmpeg build produces (see
         get_current_x265_encoder_signature) -- the full build string
         (version + OS + compiler + bit-depth flags), so a match is
         effectively "same machine/environment", not just "same x265
         release". Auto-detected every run, never manually configured, so
         it stays correct across ffmpeg upgrades with nothing to update.
         Non-definitive: an older self-encoded file made with a previous
         ffmpeg build simply won't match this and forfeits these points,
         same as any other missing signal -- it never counts against it.
      3. 'numa-pools' present — used in 90%+ of SELF encodes to cap CPU usage.
      4. limit-refs == 2 — a minor secondary tell from your profile.
      5. x265 version substring match against the current build (weaker
         than #2 -- this can true-match across different OS/compiler builds
         that happen to share the same x265 release).

    Returns a dict with the boolean verdict plus the individual signal
    results, so you can log/tune thresholds later:
        {
            "self_encoded": bool,
            "score": int,
            "keyint_match": bool | None,
            "writing_library_match": bool | None,
            "numa_pools_present": bool | None,
            "limit_refs_match": bool | None,
            "version_match": bool | None,
        }
    """
    result = {
        "self_encoded": False,
        "score": 0,
        "keyint_match": None,
        "writing_library_match": None,
        "numa_pools_present": None,
        "limit_refs_match": None,
        "version_match": None,
    }

    settings_str = await get_encoder_settings_tag(file_path)
    if not settings_str or "keyint" not in settings_str:
        return result  # no x265 settings tag embedded — can't classify

    settings = _parse_x265_settings(settings_str)

    if fps is None:
        fps = await get_frame_rate(file_path)

    current_signature = await get_current_x265_encoder_signature()

    score = 0

    # 1. GOP / keyint pattern (primary signal)
    if fps and "keyint" in settings and "min-keyint" in settings:
        try:
            keyint = int(settings["keyint"])
            min_keyint = int(settings["min-keyint"])
            expected_self = round(2 * fps)
            expected_default = round(10 * fps)

            dist_to_self = abs(keyint - expected_self)
            dist_to_default = abs(keyint - expected_default)

            keyint_match = (
                    dist_to_self <= 2
                    and dist_to_self < dist_to_default
                    and min_keyint < fps
            )
            result["keyint_match"] = keyint_match
            if keyint_match:
                score += 3  # dominant signal
        except (ValueError, TypeError):
            pass

    # 2. Writing library exact match against the current machine's build
    if current_signature["writing_library"]:
        file_writing_library = await get_video_writing_library(file_path)
        if file_writing_library:
            writing_library_match = file_writing_library == current_signature["writing_library"]
            result["writing_library_match"] = writing_library_match
            if writing_library_match:
                score += 2

    # 3. numa-pools present
    if "numa-pools" in settings:
        result["numa_pools_present"] = True
        score += 1
    else:
        result["numa_pools_present"] = False

    # 4. limit-refs == 2
    if "limit-refs" in settings:
        limit_refs_match = settings["limit-refs"].strip() == "2"
        result["limit_refs_match"] = limit_refs_match
        if limit_refs_match:
            score += 1

    # 5. x265 version substring match (weaker than the full writing-library match)
    if current_signature["version"]:
        file_version = _extract_x265_version(settings_str)
        if file_version:
            version_match = (
                    current_signature["version"].strip() in file_version
                    or file_version in current_signature["version"].strip()
            )
            result["version_match"] = version_match
            if version_match:
                score += 1

    result["score"] = score
    # keyint match alone is near-decisive; require it, plus corroboration
    # from at least one other signal to avoid coincidental keyint hits.
    result["self_encoded"] = bool(result["keyint_match"]) and score >= 4

    return result


async def get_video_codec(file_path, fps=None):
    """
    Return (codec, self_encoded):
      codec        -> 'avc', 'hevc', 'av1', or None if unsupported
      self_encoded -> True if embedded x265 settings match this pipeline's
                       own encode profile (only ever True for 'hevc').
                       Compared automatically against the CURRENT machine's
                       installed libx265/ffmpeg build (see
                       get_current_x265_encoder_signature) -- no manual
                       configuration needed, and it stays correct after an
                       ffmpeg upgrade since the signature is re-probed once
                       per run rather than hardcoded.
    """
    command = (
        f'ffprobe -v error -select_streams v:0 '
        f'-show_entries stream=codec_name '
        f'-of default=noprint_wrappers=1:nokey=1 "{file_path}"'
    )

    codec = None
    try:
        stdout, stderr, code = await run_command(command)
        codec_name = stdout.strip().lower()

        codec_map = {
            "h264": "avc",
            "avc1": "avc",
            "hevc": "hevc",
            "h265": "hevc",
            "hev1": "hevc",
            "av01": "av1",
            "av1": "av1",
        }

        codec = codec_map.get(codec_name)
        if codec is None:
            logger.warning(f"Codec '{codec_name}' is not supported for {file_path}")

    except Exception as e:
        logger.error(f"Error getting codec for {file_path}: {e}")

    self_encoded = False
    if codec == "hevc":
        check = await check_self_encoded(file_path, fps=fps)
        self_encoded = check["self_encoded"]
    if codec in ("hevc", "av1") and not self_encoded:
        # AV1 and GPU encodes carry no x265 signature: look them up in the encode ledger
        self_encoded = await encode_ledger.lookup(file_path) is not None

    return codec, self_encoded
