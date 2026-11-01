"""
Sync resource files (performer image map, BBCode icon map, templates...) from GitHub.

Entries live in ``Configs/app.json`` > ``resource_sync.entries``. A ``github.com/.../blob/...`` link is
converted to its raw URL. JSON files are validated before they replace the local copy, the previous
version is kept in ``Resources/.backups`` and ``merge_json`` mode merges the remote map into the local one
(remote values win, local-only keys are kept).
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger

from fileprep import paths
from fileprep.config.models import ResourceSyncEntry

ALLOWED_HOSTS = {"raw.githubusercontent.com", "gist.githubusercontent.com", "github.com", "api.github.com"}
STATE_FILE = paths.DATA_DIR / "resource_sync.json"
BACKUP_DIR = paths.RESOURCES_DIR / ".backups"


class SyncError(Exception):
    pass


def normalize_url(url: str) -> str:
    url = url.strip()
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url


def validate_entry(entry: ResourceSyncEntry) -> Path:
    url = normalize_url(entry.url)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise SyncError(f"Only https GitHub URLs are allowed ({', '.join(sorted(ALLOWED_HOSTS))})")
    target = (paths.RESOURCES_DIR / entry.target).resolve()
    if paths.RESOURCES_DIR.resolve() not in target.parents:
        raise SyncError("Target must be inside the Resources folder")
    return target


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _headers(token: str | None) -> dict:
    headers = {"User-Agent": "File_Prepare_HF resource sync"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


async def fetch(entry: ResourceSyncEntry, token: str | None) -> bytes:
    url = normalize_url(entry.url)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=_headers(token))
    if response.status_code != 200:
        raise SyncError(f"HTTP {response.status_code} from {url}")
    return response.content


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def check_entry(entry: ResourceSyncEntry, token: str | None) -> dict:
    """Compare remote and local content without writing anything."""
    target = validate_entry(entry)
    remote = await fetch(entry, token)
    local = target.read_bytes() if target.is_file() else None
    if local is None:
        status = "missing locally"
    elif _sha(local) == _sha(remote):
        status = "up to date"
    else:
        status = "update available"
    return {"name": entry.name, "status": status, "remote_bytes": len(remote), "local_bytes": len(local) if local else 0}


async def sync_entry(entry: ResourceSyncEntry, token: str | None) -> dict:
    target = validate_entry(entry)
    remote = await fetch(entry, token)
    content = remote
    if target.suffix.lower() == ".json":
        try:
            remote_data = json.loads(remote.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SyncError(f"Remote file is not valid JSON: {exc}")
        if entry.merge == "merge_json" and target.is_file():
            try:
                local_data = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                local_data = {}
            if isinstance(local_data, dict) and isinstance(remote_data, dict):
                merged = {**local_data, **remote_data}  # local order kept, new keys appended
                content = target.read_bytes() if merged == local_data else \
                    (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    changed = not target.is_file() or _sha(target.read_bytes()) != _sha(content)
    if changed:
        if target.is_file():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            (BACKUP_DIR / f"{target.name}.{time.strftime('%Y%m%d-%H%M%S')}").write_bytes(target.read_bytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)
        logger.success(f"Resource '{entry.name}' updated: {target.name}")
    state = _state()
    state[entry.name] = {"synced_at": time.time(), "sha256": _sha(content), "changed": changed, "error": None}
    _save_state(state)
    return {"name": entry.name, "status": "updated" if changed else "up to date", "bytes": len(content)}


async def sync_all(entries: list[ResourceSyncEntry], token: str | None) -> list[dict]:
    results = []
    for entry in entries:
        if not entry.enabled:
            continue
        try:
            results.append(await sync_entry(entry, token))
        except Exception as exc:
            logger.error(f"Resource sync '{entry.name}' failed: {exc}")
            state = _state()
            state.setdefault(entry.name, {})["error"] = str(exc)
            _save_state(state)
            results.append({"name": entry.name, "status": "error", "error": str(exc)})
    return results


def sync_state() -> dict:
    return _state()
