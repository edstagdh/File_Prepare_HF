"""
Job-side tracker upload: prepares everything asynchronously (video info, tags, template, preview URL,
torrent) and hands the browser part to the tracker's persistent session thread.
"""

import asyncio
import concurrent.futures
import os

from loguru import logger

from fileprep import runtime
from fileprep.config.models import JobConfig
from fileprep.config.store import get_credentials
from fileprep.errors import UserCancelled
from fileprep.runtime import Question
from fileprep.tracker.session import UploadRequest, delete_prefixed_files, registry, tracker_title
from fileprep.tracker.torrent import generate_torrent_process
from fileprep.uploaders.service import find_uploaded_url, hosts_for


async def upload_to_tracker(job: JobConfig, *, tracker_name: str, base_name: str, output_dir: str, title: str,
                            save_path: str, resolution, codec, self_encoded, is_last_tracker: bool) -> bool:
    account = get_credentials().trackers.get(tracker_name)
    if not account or not account.username or not account.password or not account.announce_url:
        logger.error(f"Tracker '{tracker_name}': credentials missing or invalid.")
        return False
    if codec is None or resolution is None:
        from fileprep.media.encode import get_video_codec
        from fileprep.media.probe import get_video_resolution_and_orientation
        video = os.path.join(output_dir, f"{base_name}.mp4")
        resolution, _ = await get_video_resolution_and_orientation(video)
        codec, self_encoded = await get_video_codec(video)

    with open(os.path.join(output_dir, f"{base_name}_tags.txt"), "r", encoding="utf-8") as handle:
        tags = handle.read()
    with open(os.path.join(output_dir, f"{base_name}_template.txt"), "r", encoding="utf-8") as handle:
        description = handle.read()
    image_url = find_uploaded_url(output_dir, base_name, "Preview WebP", hosts_for("previews"))
    if not image_url:
        logger.warning("No uploaded preview image URL found in the image host result files.")

    suffixes = job.tracker.torrent.excluded_suffixes
    torrent_file = await generate_torrent_process(output_dir, save_path, base_name, account.announce_url, suffixes,
                                                  job.tracker.torrent.piece_size_kib)
    if not torrent_file:
        logger.error("Torrent file generation failed.")
        return False

    loop = asyncio.get_running_loop()
    prompter = runtime.current().prompter

    job_id = runtime.current().job_id
    session = registry.get(tracker_name, job.tracker, account, job_id)

    def ask(text: str, choices: list[str]) -> str:  # called from the tracker thread
        question = Question(text=text, title=f"Tracker: {tracker_name}", choices=choices)
        pending = asyncio.run_coroutine_threadsafe(prompter.ask(question), loop)
        while True:  # the job may end (cancel) while we wait
            try:
                return pending.result(timeout=1)
            except concurrent.futures.TimeoutError:
                if session.closing.is_set() or loop.is_closed():
                    pending.cancel()
                    raise UserCancelled("The job ended while waiting for your answer")

    request = UploadRequest(job_id=job_id, title=tracker_title(title, codec, self_encoded, resolution),
                            tags=tags, description=description, image_url=image_url, torrent_file=torrent_file, ask=ask)
    try:
        ok = await asyncio.wrap_future(session.submit(request))
    except UserCancelled:
        raise  # handled by the runner (offers to undo the file)
    except Exception as exc:
        logger.error(f"Tracker '{tracker_name}' upload failed: {exc}")
        return False
    if ok and job.processing.tracker.remove_extra_files_after_upload and is_last_tracker:
        delete_prefixed_files(output_dir, suffixes, base_name)
    return ok
