"""
Image host service: one entry point for every upload, with ordered fallback.

Each asset type (cover, thumbnails sheet, previews) has an ordered host list in the job settings
(``processing.image_hosts``). In ``fallback`` mode the hosts are tried in order until one succeeds; in
``all`` mode every listed host is used. Every host keeps writing its own ``<name>_<host>.txt`` result
file (JSON: ``{"<name> - <label>": [{...}]}``), and :func:`find_uploaded_url` reads them back in host
order, so the template and the tracker always use the best available URL.
"""

import json
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from loguru import logger

from fileprep import runtime

Asset = Literal["cover", "thumbnails", "previews"]
HOSTS = ("hamster", "pixhost", "imgbb", "imgbox")


@dataclass(frozen=True)
class HostInfo:
    name: str
    extensions: frozenset[str]
    url_key: str  # key holding the direct image URL in the result file
    needs: tuple[str, ...]  # credential fields (image_hosts section) required
    max_bytes: int = 0  # per-file size limit (0 = no known limit)


HOST_INFO = {
    "hamster": HostInfo("hamster", frozenset({".webp", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}), "image_url",
                        ("hamster_api_key", "hamster_album_id", "hamster_site_url")),
    "pixhost": HostInfo("pixhost", frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}), "image_url", (),
                        10 * 1024 * 1024),
    "imgbb": HostInfo("imgbb", frozenset({".webp", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}), "direct_link",
                      ("imgbb_username", "imgbb_password")),
    "imgbox": HostInfo("imgbox", frozenset({".jpg", ".jpeg", ".png"}), "image_url", ()),
}


def result_file(output_dir: str, base_name: str, host: str) -> str:
    return os.path.join(output_dir, f"{base_name}_{host}.txt")


def hosts_for(asset: Asset) -> list[str]:
    settings = runtime.config().processing.image_hosts
    return list({"cover": settings.cover_hosts, "thumbnails": settings.thumbnails_hosts,
                 "previews": settings.preview_hosts}[asset])


def missing_credentials(host: str) -> list[str]:
    from fileprep.config.store import get_credentials
    creds = get_credentials().image_hosts
    return [field for field in HOST_INFO[host].needs if not getattr(creds, field)]


def supports(host: str, file_path_or_ext: str) -> bool:
    ext = os.path.splitext(file_path_or_ext)[1].lower() if "." in os.path.basename(file_path_or_ext) else file_path_or_ext
    return ext in HOST_INFO[host].extensions


async def _upload(host: str, file_path: str, base_name: str, label: str) -> bool:
    uploader: Callable[..., Awaitable]
    if host == "hamster":
        from fileprep.uploaders.hamster import hamster_upload_single_image as uploader
        return bool(await uploader(file_path, base_name, label))
    if host == "pixhost":
        from fileprep.uploaders.pixhost import pixhost_upload_single_image as uploader
        return bool(await uploader(file_path, base_name, label))
    if host == "imgbox":
        from fileprep.uploaders.imgbox import imgbox_upload_single_image as uploader
        return bool(await uploader(file_path, base_name, label))
    if host == "imgbb":
        from fileprep.uploaders.imgbb import imgbb_upload_single_image as uploader
        headless = runtime.config().processing.image_hosts.imgbb_headless
        extension = os.path.splitext(file_path)[1].lstrip(".").lower()
        return bool(await uploader(file_path, base_name, headless, extension, label))
    raise ValueError(f"Unknown image host: {host}")


async def upload_asset(file_path: str, base_name: str, label: str, asset: Asset) -> list[str]:
    """Upload ``file_path`` according to the asset's host list. Returns the hosts that succeeded."""
    settings = runtime.config().processing.image_hosts
    hosts = hosts_for(asset)
    if not hosts:
        return []
    if not os.path.isfile(file_path):
        logger.warning(f"{label} was not created - nothing to upload")
        return []
    size = os.path.getsize(file_path)
    succeeded = []
    for index, host in enumerate(hosts):
        more_hosts = settings.mode == "fallback" and index < len(hosts) - 1
        next_note = " - trying the next host" if more_hosts else ""
        if not supports(host, file_path):
            logger.warning(f"{host} does not accept {os.path.splitext(file_path)[1]} files - {label} skipped there{next_note}")
            continue
        limit = HOST_INFO[host].max_bytes
        if limit and size > limit:
            logger.warning(f"{label} is {size / 1048576:.1f} MB, above {host}'s {limit // 1048576} MB limit - skipped there{next_note}")
            continue
        try:
            ok = await _upload(host, file_path, base_name, label)
        except Exception:
            logger.exception(f"{host} upload raised an error for {label}")
            ok = False
        if ok:
            logger.success(f"{label} uploaded to {host}")
            succeeded.append(host)
            if settings.mode == "fallback":
                break
        else:
            logger.warning(f"{label} upload to {host} failed{next_note}")
    if not succeeded:
        logger.error(f"{label} could not be uploaded ({'host: ' if len(hosts) == 1 else 'hosts tried: '}{', '.join(hosts)})")
    return succeeded


def find_uploaded_url(output_dir: str, base_name: str, label: str, hosts: list[str] | None = None) -> str | None:
    """Direct URL of an uploaded image, taken from the first host (in ``hosts`` order) that has one."""
    key = f"{base_name} - {label}"
    for host in hosts or HOSTS:
        path = result_file(output_dir, base_name, host)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Unreadable image host result file {path}: {exc}")
            continue
        entries = data.get(key)
        if isinstance(entries, list) and entries and entries[0].get(HOST_INFO[host].url_key):
            return entries[0][HOST_INFO[host].url_key]
    return None


def clear_result_files(output_dir: str, base_name: str) -> None:
    """Remove result files from a previous run (they are regenerated by this run's uploads)."""
    for host in HOSTS:
        path = result_file(output_dir, base_name, host)
        if os.path.exists(path):
            os.remove(path)


async def upload_preview_asset(file_path: str, base_name: str, label: str) -> None:
    """Hook used by the video preview generator (upload failures are logged, not fatal - legacy behaviour)."""
    await upload_asset(file_path, base_name, label, "previews")
