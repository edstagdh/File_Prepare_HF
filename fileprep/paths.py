"""Well-known filesystem locations, resolved relative to the project root (never the CWD)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "Configs"
RESOURCES_DIR = PROJECT_ROOT / "Resources"
DOCS_DIR = PROJECT_ROOT / "docs"
WIKI_DIR = DOCS_DIR / "wiki"
LOGS_DIR = PROJECT_ROOT / "Logs"
DATA_DIR = PROJECT_ROOT / "data"  # runtime state (jobs, sessions) - git ignored
JOBS_DIR = DATA_DIR / "jobs"
VERSION_FILE = PROJECT_ROOT / "VERSION"

FACE_DETECTION_MODEL = RESOURCES_DIR / "face_detection_yunet_2026may.onnx"
PERFORMER_IMAGES_FILE = RESOURCES_DIR / "Performers_Images.json"
BBCODE_IMAGES_FILE = RESOURCES_DIR / "BBCode_Images.json"
PIXHOST_MANAGE_LOG = RESOURCES_DIR / "pixhost_manage_links.jsonl"


def resource(name: str) -> Path:
    """Path of a file inside the Resources folder (fonts, templates, json maps)."""
    return RESOURCES_DIR / name


def ffmpeg_filter_path(path: Path) -> str:
    """Path usable inside an ffmpeg filter option (e.g. drawtext fontfile).

    A Windows drive colon or backslashes break the filter parser, so the path is made relative to the
    working directory (the project root - run.py changes into it), e.g. 'Resources/Gotham_Medium.otf',
    which is the form the 13.x code used. Only when that is impossible (another drive) an absolute path with
    forward slashes and an escaped drive colon is returned."""
    import os
    try:
        return Path(os.path.relpath(path)).as_posix()
    except ValueError:
        return Path(path).as_posix().replace(":", "\\:")


def ensure_runtime_dirs() -> None:
    for directory in (CONFIG_DIR, LOGS_DIR, DATA_DIR, JOBS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
