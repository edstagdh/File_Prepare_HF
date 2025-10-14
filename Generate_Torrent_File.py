import os
import asyncio
from pathlib import Path
from loguru import logger
from torf import Torrent

# --- CONFIGURATION ---
ARCHIVE_EXTENSIONS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.tgz'}
TORRENT_EXTENSION = '.torrent'


def build_exclude_globs(base_name: str, suffixes: list) -> list[str]:
    globs = []

    # base_name + suffixes (recursive)
    if base_name and suffixes:
        for suffix in suffixes:
            globs.append(f"**/{base_name}{suffix}")

    # archive and torrent extensions (recursive)
    for ext in ARCHIVE_EXTENSIONS.union({TORRENT_EXTENSION}):
        globs.append(f"**/*{ext}")  # recursive match in all subfolders

    return globs


async def create_torrent_file(
    content_path: str,
    save_path: str,
    base_name: str,
    suffixes: list,
    tracker_private_ann_url: str,
    torrent_prefix_name: str,
    debug_mode: bool
):
    logger.info(f"Starting torrent creation for content: {content_path}")

    content_root_path = os.path.abspath(content_path)
    if not os.path.exists(content_root_path):
        raise FileNotFoundError(f"Content path does not exist: {content_root_path}")
    if not os.path.isdir(content_root_path):
        raise ValueError(f"Content path must be a directory: {content_root_path}")

    # Build exclude_globs
    exclude_globs = build_exclude_globs(base_name, suffixes)
    if debug_mode:
        logger.debug(f"Exclude globs for Torf: {exclude_globs}")

    torrent_name = f"{os.path.basename(content_root_path)}"
    torrent_file_path = os.path.join(save_path, f"{torrent_prefix_name}{torrent_name}.torrent")
    Path(save_path).mkdir(parents=True, exist_ok=True)

    # Torf is blocking, so run in a thread
    def _create_torrent_blocking():
        t = Torrent(
            path=content_root_path,
            trackers=[tracker_private_ann_url],
            private=True,
            piece_size=256 * 1024,  # 256 KB pieces
            exclude_globs=exclude_globs
        )
        # Override the torrent name (what clients see)
        t.name = torrent_name

        t.generate()
        t.write(torrent_file_path)
        return torrent_file_path

    try:
        torrent_file = await asyncio.to_thread(_create_torrent_blocking)
        logger.success(f"Torrent created at: {torrent_file}")
        if debug_mode:
            logger.debug(f"Final torrent size: {os.path.getsize(torrent_file)} bytes")
        return torrent_file
    except Exception as e:
        logger.exception(f"Failed to create torrent: {e}")
        raise


async def generate_torrent_process(files_path, save_path, base_name, p_ann_url, torrent_prefix_name, list_suffixes_ignore):
    user_debug_flag = False  # Toggle debug logs

    logger.info("Starting main torrent processing routine.")
    try:
        torrent_file = await create_torrent_file(
            content_path=files_path,
            save_path=save_path,
            base_name=base_name,
            suffixes=list_suffixes_ignore,
            tracker_private_ann_url=p_ann_url,
            torrent_prefix_name=torrent_prefix_name,
            debug_mode=user_debug_flag
        )
        logger.success(f"Processing complete. File: {torrent_file}")
        return torrent_file
    except Exception:
        logger.exception("Torrent creation failed due to an unhandled error.")
        return None
