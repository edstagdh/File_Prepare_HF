"""Basic video stream probing helpers (duration, fps, resolution, orientation)."""
import cv2
import json
from loguru import logger
from fileprep.utils.process import run_command


async def get_video_duration(filepath):
    """Returns duration of the video in seconds using OpenCV, rounded to 1 decimal place."""
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        logger.error(f"Failed to open video file: {filepath}")
        return 0.0, 0.0

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0:
        logger.error(f"Invalid FPS value for file: {filepath}")
        return 0.0, 0.0

    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()

    if fps == 0:
        logger.error(f"Invalid FPS value for file: {filepath}")
        return 0.0, 0.0

    duration = frame_count / fps  # float division
    duration = round(duration, 1)  # round to 1 decimal place
    return duration, fps


async def get_video_fps(video_path: str) -> float:
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise IOError(f"Failed to open video file: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return round(fps)


async def get_video_resolution_and_orientation(video_path: str) -> tuple[str, bool]:
    """
    Returns (resolution_label, is_vertical)
    resolution_label = "2160p", "1440p", "1080p", "720p", or "<height>p"
    is_vertical = True if displayed height > width (rotation corrected)
    """

    # ffprobe JSON query including optional rotation
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,rotation",
        "-of", "json",
        video_path
    ]

    stdout, stderr, code = await run_command(cmd)

    if code != 0:
        raise IOError(f"ffprobe failed: {stderr or stdout}")

    import json
    info = json.loads(stdout)

    stream = info["streams"][0]

    width = int(stream.get("width"))
    height = int(stream.get("height"))

    # rotation is optional — if missing, assume 0°
    rotation = int(stream.get("rotation", 0)) % 180

    # Correct for rotation (90°/270° means displayed width/height are swapped)
    if rotation == 90:
        width, height = height, width

    # Determine orientation
    is_vertical = height > width

    # Resolution is always based on **height**
    if height >= 2160:
        resolution = "2160p"
    elif height >= 1440:
        resolution = "1440p"
    elif height >= 1080:
        resolution = "1080p"
    elif height >= 720:
        resolution = "720p"
    elif height <= 719:
        resolution = "SD"
    else:
        resolution = f"{height}p"

    return resolution, is_vertical


async def get_video_resolution(file_path):
    """Get the video resolution and bitrate using ffprobe."""
    cmd = (
        f'ffprobe -v error -select_streams v:0 '
        f'-show_entries stream=width,height,bit_rate '
        f'-of json "{file_path}"'
    )
    stdout, stderr, code = await run_command(cmd)
    if code != 0:
        logger.error(f"Failed to get resolution/bitrate for {file_path}. Error: {stderr}")
        return None, None, None

    try:
        data = json.loads(stdout)
        stream = data["streams"][0]
        width = stream.get("width")
        height = stream.get("height")
        bitrate = stream.get("bit_rate", 0)
        if bitrate is None:
            bitrate = 0
        return width, height, int(bitrate)
    except Exception as e:
        logger.exception(f"Error parsing resolution/bitrate for {file_path}: {e}")
        return None, None, None


async def get_frame_rate(file_path):
    """Return the average frame rate (float) of the video stream, or None."""
    command = (
        f'ffprobe -v error -select_streams v:0 '
        f'-show_entries stream=avg_frame_rate '
        f'-of default=noprint_wrappers=1:nokey=1 "{file_path}"'
    )
    try:
        stdout, stderr, code = await run_command(command)
        raw = stdout.strip()
        if '/' in raw:
            num, den = raw.split('/')
            num, den = float(num), float(den)
            return num / den if den else None
        return float(raw) if raw else None
    except Exception as e:
        logger.error(f"Error getting frame rate for {file_path}: {e}")
        return None
