"""
Ledger of videos encoded by this application (data/encoded_videos.jsonl).

``check_self_encoded`` recognises this pipeline's x265 CPU encodes by their embedded x265 settings, but AV1
and GPU encoders leave no such signature. Each successful re-encode is therefore recorded with a hash of the
first video packets (stable across the later MP4 metadata rewrites) so it is still known as self-encoded.
"""

import json
import threading
import time
from typing import Optional

from fileprep import paths
from fileprep.utils.process import run_command

LEDGER = paths.DATA_DIR / "encoded_videos.jsonl"
_lock = threading.Lock()


async def video_fingerprint(file_path: str) -> Optional[str]:
    stdout, _, code = await run_command(["ffmpeg", "-v", "error", "-i", file_path, "-map", "0:v:0", "-c", "copy",
                                         "-frames:v", "240", "-f", "hash", "-hash", "sha256", "-"])
    line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    return line.split("=", 1)[1] if code == 0 and "=" in line else None


async def record(file_path: str, info: dict) -> None:
    fingerprint = await video_fingerprint(file_path)
    if not fingerprint:
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(LEDGER, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"fingerprint": fingerprint, "time": time.time(), **info}) + "\n")


async def lookup(file_path: str) -> Optional[dict]:
    if not LEDGER.is_file():
        return None
    fingerprint = await video_fingerprint(file_path)
    if not fingerprint:
        return None
    with _lock, open(LEDGER, encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("fingerprint") == fingerprint:
                return entry
    return None
