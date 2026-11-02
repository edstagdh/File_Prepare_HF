"""
Undo the processing of one file after its tracker upload was cancelled or failed.

Asks the user first. Undo = delete the files this run created for the video (cover, thumbnails, previews,
MediaInfo, template, tags, image host result files, torrent), delete the images this run uploaded to Pixhost
(via their delete links) and list the ones that must be deleted by hand (Hamster, ImgBB, imgbox), then move the
video back into the working folder under its original name and remove the sub folder when it is empty.

Not undone: the video's new metadata and a re-encode (the re-encoded video is the one moved back).
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone

from loguru import logger

from fileprep import runtime
from fileprep.config.models import JobConfig
from fileprep.pipeline.plan import FilePlan, FileResult

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".m4v", ".wmv")
MANUAL_DELETE_SITES = {"hamster": "the Hamster website (your album)", "imgbb": "ImgBB (your album)", "imgbox": "imgbox (your gallery)"}


async def offer_undo(job: JobConfig, fp: FilePlan, result: FileResult, *, directory: str, output_directory: str,
                     base_name: str, video_path: str, reason: str) -> bool:
    """Ask whether to undo ``fp`` and do it. Returns True when the file was undone."""
    in_sub_folder = os.path.normcase(os.path.abspath(output_directory)) != os.path.normcase(os.path.abspath(directory))
    where = f" and move the video out of '{os.path.basename(output_directory)}'" if in_sub_folder else ""
    answer = await runtime.prompt(
        f"{reason} for {fp.new_file_name}.\n\nUndo this file? 'undo' deletes the files created for it (cover, thumbnails, "
        f"previews, template, torrent...) and its uploaded images where possible (Pixhost; Hamster / ImgBB / imgbox images "
        f"are listed for you to delete){where}, and renames the video back to {fp.source_name}. Its new metadata and a "
        f"re-encode stay. 'keep' leaves everything as it is.",
        title="Undo this file?", choices=["undo", "keep"])
    if answer.strip().lower() not in ("undo", "u", "yes", "y"):
        logger.info(f"Kept the processed files of {fp.new_file_name}.")
        return False
    await undo_file(job, fp, result, directory=directory, output_directory=output_directory, base_name=base_name,
                    video_path=video_path)
    result.status, result.error, result.completed_steps = "failed", f"{reason} - processing undone", []
    return True


async def undo_file(job: JobConfig, fp: FilePlan, result: FileResult, *, directory: str, output_directory: str,
                    base_name: str, video_path: str) -> None:
    await _delete_uploaded_images(output_directory, base_name, result.started_at)
    removed = _delete_created_files(job, fp, directory, output_directory, base_name)
    restored = _restore_video(video_path, directory, fp.source_name)
    if restored:
        result.final_path = restored
    if os.path.normcase(os.path.abspath(output_directory)) != os.path.normcase(os.path.abspath(directory)) \
            and os.path.isdir(output_directory):
        left = os.listdir(output_directory)
        if left:
            logger.warning(f"Folder {output_directory} kept - it still contains: {', '.join(sorted(left)[:10])}")
        else:
            os.rmdir(output_directory)
            logger.info(f"Removed the empty folder {output_directory}")
    logger.success(f"Undone: {removed} created file(s) deleted"
                   + (f", video moved back as {os.path.basename(restored)}" if restored else "") + ".")


# ------------------------------------------------------------------ images
def _host_entries(output_directory: str, base_name: str) -> dict[str, list[dict]]:
    from fileprep.uploaders.service import HOSTS, result_file
    entries: dict[str, list[dict]] = {}
    for host in HOSTS:
        path = result_file(output_directory, base_name, host)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Cannot read {path}: {exc}")
            continue
        for label, items in data.items():
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    entries.setdefault(host, []).append({**item, "label": label})
    return entries


async def _delete_uploaded_images(output_directory: str, base_name: str, started_at) -> None:
    from fileprep.uploaders.pixhost import pixhost_delete_image, pixhost_pending_uploads
    from fileprep.uploaders.service import HOST_INFO
    entries = _host_entries(output_directory, base_name)

    # Pixhost: only uploads of this run (by time) are deleted - older ones may be used in an earlier post
    since = datetime.fromtimestamp(started_at or time.time() - 86400, timezone.utc).isoformat(timespec="seconds")
    folder = os.path.normcase(os.path.abspath(output_directory))
    for record in pixhost_pending_uploads():
        local = os.path.normcase(os.path.dirname(os.path.abspath(record.get("local_file") or "")))
        if local == folder and str(record.get("key", "")).startswith(f"{base_name} - ") and record.get("timestamp", "") >= since:
            logger.info(f"Deleting from Pixhost: {record.get('key')}")
            if not await pixhost_delete_image(record["manage_url"]):
                logger.warning(f"Delete it later in Tools > Pixhost delete links: {record['manage_url']}")

    for host, items in entries.items():
        if host == "pixhost":
            continue
        urls = [item.get(HOST_INFO[host].url_key) or item.get("image_url") or item.get("url") for item in items]
        urls = [u for u in urls if u]
        if urls:
            logger.warning(f"{host}: these images cannot be deleted automatically - delete them on "
                           f"{MANUAL_DELETE_SITES.get(host, host)}:\n" + "\n".join(f"  {u}" for u in urls))
            notify = runtime.current().notify
            if notify:
                await notify(f"Delete manually on {host}:\n" + "\n".join(urls))


# ------------------------------------------------------------------ files
def _delete_created_files(job: JobConfig, fp: FilePlan, directory: str, output_directory: str, base_name: str) -> int:
    from fileprep.uploaders.service import HOSTS
    candidates = {o.path for o in fp.outputs if not o.exists}  # outputs that existed before are kept
    helper_suffixes = list(job.tracker.torrent.excluded_suffixes) + [f"_{h}.txt" for h in HOSTS]
    candidates |= {os.path.join(output_directory, f"{base_name}{s}") for s in helper_suffixes if s.startswith(("_", "."))}
    candidates.add(os.path.join(directory, f"{os.path.basename(output_directory.rstrip(os.sep))}.torrent"))
    removed = 0
    for path in sorted(candidates):
        if path.lower().endswith(VIDEO_EXTENSIONS) or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed += 1
            logger.debug(f"Deleted {path}")
        except OSError as exc:
            logger.warning(f"Could not delete {path}: {exc}")
    return removed


def _restore_video(video_path: str, directory: str, original_name: str) -> str | None:
    if not video_path or not os.path.isfile(video_path):
        logger.warning(f"Video not found, nothing to move back: {video_path}")
        return None
    target = os.path.join(directory, original_name)
    if os.path.exists(target) and os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(video_path)):
        target = os.path.join(directory, os.path.basename(video_path))  # original name taken: keep the new one
        if os.path.exists(target) and os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(video_path)):
            logger.warning(f"Cannot move the video back: {target} already exists. It stays at {video_path}")
            return None
    if os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(video_path)):
        shutil.move(video_path, target)
    old = os.path.join(directory, f"{os.path.splitext(os.path.basename(video_path))[0]}_old.mp4")
    if os.path.isfile(old):
        logger.info(f"The original (before re-encoding) is still in {old}.")
    return target
