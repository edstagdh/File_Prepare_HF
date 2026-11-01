"""File Prepare HF - media preparation pipeline with a web interface."""

from pathlib import Path

try:
    __version__ = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    __version__ = "unknown"
