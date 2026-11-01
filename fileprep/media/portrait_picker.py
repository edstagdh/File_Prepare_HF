"""
Performer image picker (before a run): find face candidates for performers that have no entry in
Resources/Performers_Images.json, upload the chosen image and store its URL in that file.

Candidates live in data/portraits/<job id>/<performer key>/:
* ``<slug>_<n>.webp``                 downloaded TPDB posters
* ``<slug>_<n>-face-<m>.<format>``     face crops (same detection as the run's portrait step)
* ``upload_<n>.<ext>``                 images uploaded by the user (+ their face crops)
The run's portrait step skips performers that already have a URL, and the template uses the URLs.
"""

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path

from loguru import logger

from fileprep import paths
from fileprep.config.models import JobConfig

PORTRAITS_DIR = paths.DATA_DIR / "portraits"
TPDB_LINKS_FILE = paths.DATA_DIR / "performer_tpdb_links.json"  # performer key -> TPDB performer UUID (set by the user)
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
IMAGE_EXTENSIONS = {".webp", ".avif", ".jpg", ".jpeg", ".png"}
_json_lock = threading.Lock()


def performer_key(name: str) -> str:
    """Name as stored in Performers_Images.json (alias in parentheses and special characters removed)."""
    cleaned = re.sub(r"\s*\([^)]*\)", "", name)
    return cleaned.translate(str.maketrans("", "", "!@#$%^&*()_+='")).strip()


def _folder(job_id: str, key: str) -> Path:
    safe = re.sub(r"[^\w.-]+", "_", key).strip("_") or "performer"
    return PORTRAITS_DIR / job_id / safe


def load_images() -> dict:
    try:
        return json.loads(paths.PERFORMER_IMAGES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def image_url(key: str, images: dict | None = None) -> str | None:
    images = load_images() if images is None else images
    lower = {k.lower(): v for k, v in images.items()}
    return lower.get(key.lower())


def save_url(key: str, url: str) -> None:
    """Add / replace one performer in Performers_Images.json (previous file kept in Resources/.backups)."""
    with _json_lock:
        images = load_images()
        path = paths.PERFORMER_IMAGES_FILE
        if path.is_file():
            backup_dir = paths.RESOURCES_DIR / ".backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}").write_bytes(path.read_bytes())
        for existing in [k for k in images if k.lower() == key.lower()]:
            del images[existing]
        images[key] = url
        images = dict(sorted(images.items(), key=lambda kv: kv[0].lower()))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(images, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    logger.info(f"Performers_Images.json: {key} -> {url}")


def load_links() -> dict:
    try:
        return json.loads(TPDB_LINKS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


async def link_tpdb(key: str, tpdb_uuid: str) -> dict:
    """Link a performer (e.g. entered manually, without TPDB id) to a TPDB performer UUID. Verified against the API."""
    from fileprep.config.store import get_credentials
    from fileprep.tpdb.api_client import send_request
    tpdb_uuid = tpdb_uuid.strip()
    if not UUID_RE.match(tpdb_uuid):
        raise ValueError("Not a TPDB performer UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx, shown on the performer's TPDB page).")
    creds = get_credentials().tpdb
    if not creds.api_token:
        raise ValueError("TPDB API token missing (Settings > Credentials).")
    data = await send_request(creds.performers_api_url, creds.api_token, tpdb_uuid, 2, 2)
    performer = (data or {}).get("data") or {}
    if not performer:
        raise ValueError("TPDB has no performer with this UUID.")
    with _json_lock:
        links = load_links()
        links[key] = tpdb_uuid.lower()
        TPDB_LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TPDB_LINKS_FILE.write_text(json.dumps(links, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(f"Performer {key} linked to TPDB performer {performer.get('name')} ({tpdb_uuid})")
    return {"tpdb_id": tpdb_uuid.lower(), "tpdb_name": performer.get("name")}


def clear_candidates(job_id: str, key: str) -> None:
    """Delete the downloaded posters / face crops / uploads of one performer (after an image was chosen)."""
    import shutil
    folder = _folder(job_id, key)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def performers_of(job) -> list[dict]:
    """Unique performers (with TPDB ids) of a job's preview and whether they already have an image."""
    images = load_images()
    links = {k.lower(): v for k, v in load_links().items()}
    seen: dict[str, dict] = {}
    for fp in (job.plan.files if job.plan else []):
        performers = fp.match.performers if fp.match else None
        if not isinstance(performers, list):
            continue
        for entry in performers:
            name = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
            tpdb_id = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else None
            key = performer_key(name)
            if key and key.lower() not in seen:
                linked = links.get(key.lower())
                seen[key.lower()] = {"name": name, "key": key, "tpdb_id": tpdb_id or linked, "linked": bool(linked and not tpdb_id),
                                     "url": image_url(key, images), "candidates": list_candidates(job.id, key)}
    return sorted(seen.values(), key=lambda p: (p["url"] is not None, p["key"].lower()))


def list_candidates(job_id: str, key: str) -> list[dict]:
    folder = _folder(job_id, key)
    if not folder.is_dir():
        return []
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name)
    return [{"file": p.name, "kind": "face" if "-face-" in p.name else "upload" if p.name.startswith("upload_") else "poster"}
            for p in files]


def candidate_path(job_id: str, key: str, file_name: str) -> Path:
    path = (_folder(job_id, key) / file_name).resolve()
    if path.parent != _folder(job_id, key).resolve() or not path.is_file():
        raise FileNotFoundError(file_name)
    return path


async def _detect(image: Path, folder: Path, key: str, config: JobConfig) -> None:
    from fileprep.media.portrait import process_detection
    pr = config.processing.portraits
    await process_detection(str(image), str(folder), pr.zoom_factor, (pr.width, pr.height), pr.blur_kernel_size, key, 18,
                            (255, 255, 255), 0.8, str(paths.FACE_DETECTION_MODEL), pr.image_format,
                            config.processing.outputs.font_file, pr.face_score_threshold)


def _run_in_thread(coro_factory):
    """CPU-heavy work (OpenCV / PIL) runs in a worker thread with its own loop, keeping the web server responsive."""
    return asyncio.to_thread(lambda: asyncio.run(coro_factory()))


async def generate_candidates(job_id: str, key: str, tpdb_id, config: JobConfig) -> list[dict]:
    from fileprep.media.portrait import download_poster_images
    from fileprep.tpdb.performers import get_performer_profile_picture
    if not tpdb_id:
        raise ValueError("No TPDB id for this performer - upload an image or paste a URL instead.")
    folder = _folder(job_id, key)
    folder.mkdir(parents=True, exist_ok=True)

    async def work():
        posters, slug = await get_performer_profile_picture(key, tpdb_id, config.processing.portraits.posters_limit)
        if not posters:
            raise ValueError("TPDB returned no posters for this performer.")
        files = await download_poster_images(posters, str(folder), slug or "poster", config.processing.portraits.posters_limit)
        if files and files != ["already downloaded"]:
            for file in files:
                await _detect(Path(file), folder, key, config)

    await _run_in_thread(work)
    return list_candidates(job_id, key)


async def add_upload(job_id: str, key: str, file_name: str, data: bytes, config: JobConfig) -> list[dict]:
    ext = Path(file_name).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError("Use a JPG, PNG, WebP or AVIF image.")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Image is larger than 20 MB.")
    folder = _folder(job_id, key)
    folder.mkdir(parents=True, exist_ok=True)
    index = len([p for p in folder.glob("upload_*")]) + 1
    target = folder / f"upload_{index}{'.jpg' if ext == '.jpeg' else ext}"
    target.write_bytes(data)
    try:
        from PIL import Image
        with Image.open(target) as image:
            image.verify()
    except Exception:
        target.unlink(missing_ok=True)
        raise ValueError("The file is not a valid image.")
    await _run_in_thread(lambda: _detect(target, folder, key, config))
    return list_candidates(job_id, key)


async def choose(job_id: str, key: str, file_name: str, config: JobConfig) -> str:
    """Upload the chosen candidate to the first working portrait host and store its URL."""
    from fileprep.uploaders.service import _upload, find_uploaded_url, supports
    path = candidate_path(job_id, key, file_name)
    hosts = config.processing.image_hosts.portrait_hosts
    if not hosts:
        raise ValueError("No portrait image host configured (Image hosts > Portrait hosts).")
    base = re.sub(r"[^\w.-]+", "_", key)
    for host in hosts:
        if not supports(host, str(path)):
            continue
        try:
            if await _upload(host, str(path), base, "portrait"):
                url = find_uploaded_url(str(path.parent), base, "portrait", [host])
                if url:
                    save_url(key, url)
                    clear_candidates(job_id, key)  # the other posters / faces are no longer needed
                    return url
        except Exception as exc:
            logger.warning(f"Portrait upload to {host} failed: {exc}")
    raise ValueError(f"Upload failed on every portrait host ({', '.join(hosts)}).")
