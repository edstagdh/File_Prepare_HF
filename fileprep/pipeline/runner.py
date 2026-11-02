"""
Execute a previewed :class:`JobPlan` (formerly the processing loop of main.py).

For every planned file: rename, audio track check, metadata / re-encode, cover, thumbnails, previews,
mediainfo, template, move to its sub folder, tracker upload(s) and TPDB collection.
Stop requests are honoured between files; cancellation (task cancel) interrupts the current step.
"""

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger

from fileprep import __version__, paths, runtime
from fileprep.config.models import JobConfig
from fileprep.media.audio import check_multiple_audio_tracks, select_and_filter_audio_tracks
from fileprep.media.cover import cover_image_download_and_conversion
from fileprep.media.encode import get_video_codec, is_video_hevc_or_av1, re_encode_video
from fileprep.media.mediainfo import generate_mediainfo_file
from fileprep.media.metadata import (chapters_need_update, check_existing_Encoder_Library, check_existing_exclusive,
                                     get_existing_description, get_existing_title, get_existing_tpdb_uuid,
                                     has_unwanted_metadata, reset_all_metadata, update_metadata)
from fileprep.media.probe import get_video_fps, get_video_resolution_and_orientation
from fileprep.media.thumbnail_sheet import process_thumbnails
from fileprep.media.video_preview import process_video_preview
from fileprep.pipeline.matching import DESCRIPTION_NOT_FOUND
from fileprep.errors import UserCancelled
from fileprep.pipeline.plan import FilePlan, FileResult, JobPlan, inputs_hash
from fileprep.pipeline.rollback import offer_undo
from fileprep.pipeline.planner import list_input_files
from fileprep.template.bbcode import generate_template_video
from fileprep.tpdb import TpdbConnectionError, ensure_scene_collected
from fileprep.uploaders.service import clear_result_files, upload_asset
from fileprep.utils.files import rename_file


class PlanOutdated(Exception):
    """The input files changed since the preview."""


@dataclass
class RunHooks:
    """Callbacks from the job manager."""
    on_file_update: Callable[[FileResult], None] = lambda result: None
    submit_tracker_upload: Optional[Callable[..., Awaitable[bool]]] = None


@dataclass
class RunSummary:
    results: list[FileResult] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(r.status == "failed" for r in self.results)


def verify_plan_current(job: JobConfig, plan: JobPlan) -> None:
    directory = job.processing.input.working_directory
    names = list_input_files(directory, job.processing.input.ignored_filenames)
    if inputs_hash(directory, names) != plan.inputs_hash:
        raise PlanOutdated("The input files changed since the preview - run a new preview.")


async def run_plan(job: JobConfig, plan: JobPlan, hooks: RunHooks, results: dict[str, FileResult]) -> RunSummary:
    """Process every runnable file of ``plan``. ``results`` (keyed by source name) carries state across restarts:
    files already marked success/skipped are not processed again."""
    p = job.processing
    directory = p.input.working_directory
    summary = RunSummary()

    # Strict mode pre-processing renames (only those not applied by a previous, interrupted run)
    for old_name, new_name in plan.renames:
        old_path, new_path = os.path.join(directory, old_name), os.path.join(directory, new_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            os.rename(old_path, new_path)
            logger.info(f"Renamed: {old_name} -> {new_name}")

    # Files without a TPDB match (preview warning E38_NO_MATCH) are skipped and reported
    unmatched = [fp.source_name for fp in plan.files if fp.match is None and fp.status == "skipped"]
    for name in unmatched:
        if name not in results:
            results[name] = FileResult(source_name=name, status="skipped", error="No TPDB match - skipped")
            hooks.on_file_update(results[name])
            logger.warning(f"Skipped (no TPDB match): {name}")
    if unmatched:
        notify = runtime.current().notify
        if notify:
            await notify(f"{len(unmatched)} file(s) skipped - no TPDB match: {', '.join(unmatched[:10])}")

    runnable = [fp for fp in plan.files if fp.match and fp.status != "error"]
    tracker_tasks: list[asyncio.Task] = []
    for index, fp in enumerate(runnable, start=1):
        result = results.setdefault(fp.source_name, FileResult(source_name=fp.source_name))
        summary.results.append(result)
        if result.status in ("success", "skipped"):
            continue
        if runtime.stop_requested():
            logger.warning("Stop requested - remaining files are left for the next start.")
            break
        logger.info("-" * 100)
        logger.info(f"Start file: {fp.source_name}, file {index} out of {len(runnable)}")
        runtime.report_progress("File", (index - 1) * 100 / len(runnable), fp.source_name)
        result.status, result.started_at, result.error = "running", time.time(), None
        hooks.on_file_update(result)
        try:
            upload_task = await process_file(job, fp, result, hooks)
            if upload_task:
                if p.tracker.wait_for_upload_before_next_file:
                    await upload_task
                else:
                    tracker_tasks.append(upload_task)
            if result.status == "running":
                result.status = "success"
        except asyncio.CancelledError:
            result.status, result.error = "failed", "cancelled"
            hooks.on_file_update(result)
            raise
        except TpdbConnectionError as exc:  # API down: stop the whole job, the remaining files stay resumable
            logger.error(f"TPDB connection failure - job stopped: {exc}")
            result.status, result.error, result.finished_at = "failed", str(exc), time.time()
            hooks.on_file_update(result)
            raise
        except Exception as exc:
            logger.exception(f"Processing failed for file: {fp.source_name}")
            result.status, result.error = "failed", str(exc)
        result.finished_at = time.time()
        hooks.on_file_update(result)
        logger.info(f"End file: {result.final_path or fp.source_name} ({result.status})")

    if tracker_tasks:
        logger.info(f"Waiting for {len(tracker_tasks)} tracker upload(s) to be completed in the browser...")
        await asyncio.gather(*tracker_tasks, return_exceptions=True)
    runtime.report_progress("File", 100, "done")
    return summary


async def process_file(job: JobConfig, fp: FilePlan, result: FileResult, hooks: RunHooks) -> Optional[asyncio.Task]:
    """Process one file. Returns the pending tracker upload task (if any)."""
    p = job.processing
    o, e = p.outputs, p.encoding
    directory = p.input.working_directory
    match = fp.match
    original_path = Path(directory) / fp.source_name
    if not original_path.exists():
        renamed = Path(directory) / fp.new_file_name
        if result.completed_steps and renamed.exists():  # resuming a file renamed by an earlier attempt
            logger.info(f"Resuming with the already renamed file: {renamed.name}")
            original_path = renamed
        else:
            raise FileNotFoundError(f"Source file not found: {original_path}")
    result.completed_steps = []

    def step(name: str) -> None:
        result.completed_steps.append(name)
        hooks.on_file_update(result)

    # ------------------------------------------------------------------ output folder
    output_directory = fp.output_directory or directory
    if p.naming.create_sub_folder:
        old_folder = os.path.join(directory, original_path.stem)
        if not os.path.isdir(output_directory):
            if os.path.isdir(old_folder) and old_folder != output_directory:
                os.rename(old_folder, output_directory)  # keep outputs of a previous run
                logger.success(f"Folder renamed to: '{output_directory}'")
            else:
                os.makedirs(output_directory, exist_ok=True)

    # ------------------------------------------------------------------ rename
    new_full_filename = fp.new_file_name
    new_file_full_path = os.path.join(directory, new_full_filename)
    if str(original_path) != new_file_full_path:
        ok, error = await rename_file(str(original_path), new_full_filename)
        if not ok:
            raise RuntimeError(f"Failed to rename '{fp.source_name}' to '{new_full_filename}': {error}")
        step("rename")
    result.final_path = new_file_full_path

    if e.keep_single_audio_track and await check_multiple_audio_tracks(new_file_full_path):
        if not await select_and_filter_audio_tracks(new_file_full_path, keep_old=True):
            raise RuntimeError("Failed to filter audio tracks")
        step("audio tracks")

    # ------------------------------------------------------------------ metadata / re-encode decision
    add_timestamps_markers = p.review.add_chapter_markers and bool(match.markers)
    re_encode = e.re_encode
    force_regen_thumbs = False
    existing_tpdb_uuid = await get_existing_tpdb_uuid(new_file_full_path) if p.matching.rematch_using_embedded_uuid else None
    description = f"TPDB URL: {fp.tpdb_url} | Scene URL: {match.scene_url}"
    changed_title = await get_existing_title(new_file_full_path) != fp.title
    contains_unwanted_metadata = await has_unwanted_metadata(new_file_full_path, p.metadata.set_exclusive, p.metadata.exclusive_tag)
    existing_description = await get_existing_description(new_file_full_path)
    check_encoder = await check_existing_Encoder_Library(new_file_full_path)
    chapters_changed = await chapters_need_update(new_file_full_path, add_timestamps_markers, match.markers)
    missing_exclusive = await check_existing_exclusive(new_file_full_path, p.metadata.exclusive_tag) if p.metadata.set_exclusive else None
    if not chapters_changed:
        add_timestamps_markers = False
    metadata_mismatch = bool(changed_title or existing_description != description or contains_unwanted_metadata
                             or chapters_changed or check_encoder or missing_exclusive)
    if p.matching.rematch_using_embedded_uuid and existing_tpdb_uuid != match.tpdb_uuid:
        metadata_mismatch = True
    if re_encode and await is_video_hevc_or_av1(new_file_full_path, e.warn_on_crf_match, e.crf):
        re_encode = False
    metadata_update_required = metadata_mismatch or re_encode

    if metadata_mismatch:
        logger.debug(f"File: {new_full_filename} - Metadata mismatch detected.")
        if contains_unwanted_metadata and not (add_timestamps_markers and bool(match.markers)):
            if not await reset_all_metadata(new_file_full_path):
                raise RuntimeError("Failed to strip unwanted metadata")
        force_regen_thumbs = True
    if re_encode:
        logger.debug(f"File: {new_full_filename} - Will re-encode and reapply metadata.")
        force_regen_thumbs = True

    new_filename_base_name, extension = os.path.splitext(new_full_filename)
    if not re_encode and o.upload_template:
        fps = await get_video_fps(new_file_full_path)
        resolution_template, is_vertical = await get_video_resolution_and_orientation(new_file_full_path)
        codec, self_encoded = await get_video_codec(new_file_full_path)
    else:
        fps = resolution_template = is_vertical = codec = self_encoded = None

    # ------------------------------------------------------------------ outputs
    full_manual = match.full_manual
    create_cover = o.cover_image and not full_manual
    fmt = o.image_format
    cover_file_path = os.path.join(output_directory, f"{new_filename_base_name}.{fmt}")
    thumbnails_file_path = os.path.join(output_directory, f"{new_filename_base_name}_{job.thumbnails.output_suffix}.{fmt}")
    hosts = p.image_hosts
    upload_cover = create_cover and bool(hosts.cover_hosts)
    upload_thumbnails = o.thumbnails_sheet and bool(hosts.thumbnails_hosts)
    upload_previews = o.video_preview and bool(hosts.preview_hosts)
    fill_img_urls = upload_cover or upload_thumbnails or upload_previews
    if fill_img_urls:
        clear_result_files(output_directory, new_filename_base_name)
    template_path = str(paths.resource(o.template_file))

    async def upload(path: str, label: str, asset: str) -> bool:
        await upload_asset(path, new_filename_base_name, label, asset)
        return True  # legacy behaviour: an upload failure does not fail the file

    steps = [
        (f"{e.codec.upper()} re-encode ({e.device.upper()})", re_encode, re_encode_video,
         [new_full_filename, directory, is_vertical, e]),
        ("metadata", metadata_update_required, update_metadata,
         [new_file_full_path, fp.title, description, match.tpdb_uuid, p.matching.matching_mode, add_timestamps_markers,
          match.markers, p.metadata.set_exclusive, p.metadata.exclusive_tag]),
        ("cover", create_cover, cover_image_download_and_conversion,
         [match.image_url, match.tpdb_image_url, new_full_filename, fp.source_name, directory, fmt,
          p.naming.create_sub_folder, output_directory, o.cover_regeneration_mode]),
        ("cover upload", upload_cover, upload, [cover_file_path, "cover", "cover"]),
        ("thumbnails", o.thumbnails_sheet, process_thumbnails,
         [new_full_filename, directory, fp.source_name, output_directory, fmt, is_vertical, p.naming.create_sub_folder,
          force_regen_thumbs, job.thumbnails]),
        ("thumbnails upload", upload_thumbnails, upload, [thumbnails_file_path, "thumbnails", "thumbnails"]),
        ("video preview", o.video_preview, process_video_preview,
         [new_file_full_path, output_directory, new_filename_base_name, upload_previews, job.video_preview]),
        ("mediainfo", o.mediainfo_file, generate_mediainfo_file, [new_file_full_path, output_directory]),
        ("template", o.upload_template, generate_template_video,
         [fp.title, match.scene_title, fp.studio_info, fp.scene_pretty_date, match.description, match.performers, fps,
          resolution_template, is_vertical, codec, extension, output_directory, new_file_full_path, new_filename_base_name,
          template_path, __version__, match.tags, fp.studio_tags, fmt, fill_img_urls, fp.suffix, fp.tpdb_url or ""]),
    ]
    for name, enabled, func, args in steps:
        if not enabled:
            continue
        runtime.report_progress(name, None, new_full_filename)
        if not await func(*args):
            raise RuntimeError(f"Step failed: {name}")
        step(name)

    # ------------------------------------------------------------------ move into sub folder
    if p.naming.create_sub_folder:
        if not os.path.exists(new_file_full_path):
            raise FileNotFoundError(f"Source file {new_file_full_path} does not exist.")
        shutil.move(new_file_full_path, output_directory)
        new_file_full_path = os.path.join(output_directory, new_full_filename)
        result.final_path = new_file_full_path
        logger.success(f"File moved to {output_directory}")
        step("moved to folder")

    if match.description == DESCRIPTION_NOT_FOUND and o.upload_template:
        notify = runtime.current().notify
        if notify:
            await notify("Warning - Scene description not found")
        logger.warning("Scene description not found, please update manually in template")

    # ------------------------------------------------------------------ tracker + collection
    async def finish() -> bool:
        if p.tracker.enabled:
            for i, tracker_name in enumerate(p.tracker.trackers):
                is_last = i == len(p.tracker.trackers) - 1
                if hooks.submit_tracker_upload is None:
                    raise RuntimeError("Tracker uploads are not available in this mode")
                try:
                    ok = await hooks.submit_tracker_upload(
                        tracker_name=tracker_name, base_name=new_filename_base_name, output_dir=output_directory,
                        title=fp.title, save_path=directory, resolution=resolution_template, codec=codec,
                        self_encoded=self_encoded, is_last_tracker=is_last)
                    reason = None if ok else f"The upload to {tracker_name} failed"
                except UserCancelled:
                    ok, reason = False, f"The upload to {tracker_name} was cancelled"
                if not ok:
                    result.status, result.error = "failed", reason[0].lower() + reason[1:]
                    hooks.on_file_update(result)
                    if i == 0:  # nothing was posted yet: offer to undo the file
                        try:
                            await offer_undo(job, fp, result, directory=directory, output_directory=output_directory,
                                             base_name=new_filename_base_name, video_path=new_file_full_path, reason=reason)
                        except Exception as exc:
                            logger.error(f"Undo of {fp.new_file_name} failed: {exc}")
                        hooks.on_file_update(result)
                    return False
                step(f"tracker: {tracker_name}")
        if p.matching.add_to_collection and match.tpdb_id and not full_manual:
            await ensure_scene_collected(match.tpdb_id, p.matching.api_mode == "jav", p.matching.api_mode == "movies")
            step("TPDB collection")
        return True

    if p.tracker.enabled:
        return asyncio.create_task(finish())
    await finish()
    return None
