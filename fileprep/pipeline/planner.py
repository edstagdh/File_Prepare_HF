"""
Preview / dry-run: resolve every file's metadata and target names without touching any file, and detect
everything that would make the run fail or overwrite data.

The resulting :class:`JobPlan` is stored with the job; the runner re-uses its matches, so questions
answered during the preview (scene selection, approvals, manual input) are not asked again.
"""

import os
import time
from pathlib import Path

from loguru import logger

from fileprep import runtime
from fileprep.config.models import JobConfig
from fileprep.config.store import store
from fileprep.media.encode import is_video_hevc_or_av1
from fileprep.pipeline import naming
from fileprep.pipeline.manual_input import get_selected_filename
from fileprep.pipeline.matching import resolve_match
from fileprep.pipeline.plan import FilePlan, JobPlan, PlanIssue, PlannedOutput, config_hash, inputs_hash
from fileprep.pipeline.validation import validate_job_config
from fileprep.system.ffmpeg import check_ffmpeg
from fileprep.system.gpu import detect as detect_gpu
from fileprep.tpdb import TpdbConnectionError
from fileprep.utils.text import clean_filename, is_valid_filename, is_valid_filename_format


def list_input_files(directory: str, ignored: list[str]) -> list[str]:
    """.mp4 files directly inside ``directory`` (legacy rules: skip *_old.mp4, *_temp.mp4 and ignored names)."""
    if not os.path.isdir(directory):
        return []
    names = [entry.name for entry in Path(directory).iterdir()
             if entry.is_file() and entry.suffix.lower() == ".mp4"
             and not entry.name.lower().endswith(("_old.mp4", "_temp.mp4")) and entry.name not in ignored]
    return sorted(names, key=str.lower)


async def plan_strict_renames(directory: str, names: list[str], strip_words: list[str]) -> tuple[list[tuple[str, str]], list[PlanIssue]]:
    """Strict mode pre-processing (clean names) computed without renaming anything."""
    renames, issues = [], []
    for name in names:
        if " " in name:
            issues.append(PlanIssue(level="error", code="E12_SPACES", file=name, message="File name contains spaces (strict mode)."))
            continue
        new_name = await clean_filename(name, strip_words, 1)
        if new_name != name:
            renames.append((name, new_name))
        if not await is_valid_filename_format(new_name):
            issues.append(PlanIssue(level="error", code="E14_FORMAT", file=name,
                                    message=f"'{new_name}' does not match STUDIO.YY.MM.DD.PERFORMER (strict mode)."))
    return renames, issues


def _outputs(job: JobConfig, out_dir: str, base: str, suffix: str) -> list[PlannedOutput]:
    p = job.processing
    stem = naming.folder_name(base, suffix)  # cover/thumbnail names include the video type suffix
    fmt = p.outputs.image_format
    items = []

    def add(kind, file_name, note=""):
        path = os.path.join(out_dir, file_name)
        items.append(PlannedOutput(kind=kind, path=path, exists=os.path.exists(path), note=note))

    if p.outputs.cover_image and p.matching.matching_mode != "full_manual":
        add("cover", f"{stem}.{fmt}", f"existing: {p.outputs.cover_regeneration_mode}")
    if p.outputs.thumbnails_sheet:
        add("thumbnails", f"{stem}_{job.thumbnails.output_suffix}.{fmt}", f"existing: {job.thumbnails.regeneration_mode}")
    if p.outputs.video_preview:
        vp = job.video_preview
        preview_base = f"{stem}_black_bars" if vp.add_black_bars else stem
        preview_dir = vp.custom_output_directory or out_dir
        for enabled, tail in ((vp.create_webp_preview, "_preview.webp"), (vp.create_avif_preview, "_preview.avif"),
                              (vp.create_webm_preview, "_preview.webm"),
                              (vp.create_gif_preview, "_preview.gif"), (vp.create_webp_sheet, "_preview_sheet.webp"),
                              (vp.create_avif_sheet, "_preview_sheet.avif"),
                              (vp.create_webm_sheet, "_preview_sheet.webm"), (vp.create_gif_sheet, "_preview_sheet.gif")):
            if enabled:
                path = os.path.join(preview_dir, preview_base + tail)
                items.append(PlannedOutput(kind="preview", path=path, exists=os.path.exists(path),
                                           note="overwritten" if vp.overwrite_existing else "asks when it exists"))
    if p.outputs.mediainfo_file:
        add("mediainfo", f"{stem}_mediainfo.txt", "regenerated")
    if p.outputs.upload_template:
        add("template", f"{stem}_template.txt", "regenerated")
        add("tags", f"{stem}_tags.txt", "regenerated")
    return items


async def gpu_issues(job: JobConfig) -> list[PlanIssue]:
    """GPU checks (async detection, so not part of validate_job_config)."""
    e, hw = job.processing.encoding, store.app().hardware
    uses_gpu_encode = e.re_encode and e.device == "gpu"
    if hw.gpu == "off" and not uses_gpu_encode:
        return []
    report = await detect_gpu()
    backend = report.backend()
    issues = []
    if hw.gpu not in ("auto", "off") and not backend:
        issues.append(PlanIssue(level="warning", code="GPU_UNAVAILABLE",
                                message=f"GPU backend '{hw.gpu}' did not pass detection - thumbnails/previews use the CPU."))
    if uses_gpu_encode:
        encoder = backend.encoders.get(e.codec) if backend and hw.gpu != "off" else None
        if not encoder:
            issues.append(PlanIssue(level="warning" if e.fallback_to_cpu else "error", code="GPU_NO_ENCODER",
                                    message=f"No working GPU {e.codec.upper()} encoder on this machine (Tools > ffmpeg)"
                                            + (" - the CPU will be used." if e.fallback_to_cpu else ".")))
        else:
            issues.append(PlanIssue(level="info", code="GPU_ENCODER", message=f"Videos are encoded with {encoder}."))
    return issues


async def build_plan(job: JobConfig, active_directories: dict[str, str] | None = None) -> JobPlan:
    """Build the plan. ``active_directories`` maps working directories of other queued/running jobs to their id."""
    p = job.processing
    directory = p.input.working_directory
    app = store.app()
    report = await check_ffmpeg()
    plan = JobPlan(created_at=time.time(), working_directory=directory,
                   config_hash=config_hash(job.model_dump(mode="json")), inputs_hash="", encode_slot=p.encoding.device if p.encoding.re_encode else None)
    plan.issues.extend(validate_job_config(job, store.credentials(), report,
                                           app.environment.python_min_version, app.environment.python_max_version))
    plan.issues.extend(await gpu_issues(job))

    # Jobs only read the files directly inside their folder, so a parent and a child folder can run side by side;
    # only the same folder (or writing into a folder another job uses, checked below per file) conflicts.
    claimed = {_norm(d): other_id for d, other_id in (active_directories or {}).items()}
    if directory and _norm(directory) in claimed:
        plan.issues.append(PlanIssue(level="error", code="DIRECTORY_IN_USE",
                                     message=f"Job {claimed[_norm(directory)]} is already queued/running on this directory "
                                             f"(or writes its output there)."))
    if any(i.code == "E27_DIRECTORY" for i in plan.issues):
        return plan

    names = list_input_files(directory, p.input.ignored_filenames)
    plan.inputs_hash = inputs_hash(directory, names)
    if not names:
        plan.issues.append(PlanIssue(level="error", code="NO_FILES", message="No .mp4 files to process in the working directory."))
        return plan
    if any(i.level == "error" for i in plan.issues):
        logger.warning("Configuration errors found - files are listed but not matched. Fix the settings and preview again.")
        for name in names:
            st = os.stat(os.path.join(directory, name))
            plan.files.append(FilePlan(source_name=name, source_size=st.st_size, source_mtime=st.st_mtime, status="skipped"))
        return plan

    rename_map = {}
    if p.matching.matching_mode == "strict":
        plan.renames, rename_issues = await plan_strict_renames(directory, names, p.input.filename_strip_words)
        plan.issues.extend(rename_issues)
        rename_map = dict(plan.renames)

    notify = runtime.current().notify
    total = len(names)
    for index, original_name in enumerate(names, start=1):
        runtime.report_progress("Preview", (index - 1) * 100 / total, original_name)
        if runtime.stop_requested():
            plan.issues.append(PlanIssue(level="error", code="PREVIEW_STOPPED", message="Preview stopped before all files were checked."))
            break
        source_name = rename_map.get(original_name, original_name)
        logger.info(f"[{index}/{total}] Previewing {original_name}")
        st = os.stat(os.path.join(directory, original_name))
        file_plan = FilePlan(source_name=source_name, source_size=st.st_size, source_mtime=st.st_mtime)
        plan.files.append(file_plan)
        try:
            await _plan_file(job, directory, original_name, file_plan, notify)
        except TpdbConnectionError as exc:  # API down: stop the preview, every other file would fail too
            logger.error(f"TPDB connection failure - preview stopped: {exc}")
            plan.issues.append(PlanIssue(level="error", code="TPDB_CONNECTION",
                                         message=f"{exc} Preview stopped at {original_name}; fix the connection and preview again."))
            file_plan.status = "error"
            break
        except Exception as exc:  # one broken file must not abort the whole preview
            logger.exception(f"Preview failed for {original_name}")
            file_plan.issues.append(PlanIssue(level="error", code="PREVIEW_EXCEPTION", file=original_name, message=str(exc)))
        file_plan.status = ("error" if any(i.level == "error" for i in file_plan.issues)
                            else "skipped" if file_plan.match is None
        else "warning" if any(i.level == "warning" for i in file_plan.issues) else "ok")

    _detect_cross_file_conflicts(plan, directory)
    _detect_job_conflicts(plan, claimed)
    if p.outputs.performer_portraits or p.outputs.upload_template:
        from fileprep.media.portrait_picker import image_url, load_images, performer_key
        images = load_images()
        missing = sorted({performer_key(e[0]) for fp in plan.files if fp.match and isinstance(fp.match.performers, list)
                          for e in fp.match.performers if isinstance(e, (list, tuple)) and not image_url(performer_key(e[0]), images)})
        if missing:
            plan.issues.append(PlanIssue(level="info", code="PERFORMERS_WITHOUT_IMAGE",
                                         message=f"{len(missing)} performer(s) without an image ({', '.join(missing[:8])}"
                                                 f"{'...' if len(missing) > 8 else ''}) - pick them in 'Performer images' before running."))
    runtime.report_progress("Preview", 100, "done")
    return plan


async def _plan_file(job: JobConfig, directory: str, original_name: str, fp: FilePlan, notify) -> None:
    p = job.processing
    file_path = Path(directory) / original_name
    flags = naming.detect_flags(fp.source_name)
    fp.flags = flags.flags
    pre_suffix = naming.detect_pre_suffix(flags.clean_name, p.naming)

    match, issues = await resolve_match(file_path, flags, p, notify)
    fp.issues.extend(issues)
    if not match:
        return
    fp.match = match
    fp.flag_suffix = flags.suffix
    date = naming.format_scene_date(match.date, p.naming.title_date_format)

    candidates = await naming.build_name_candidates(match.site, date, match.performers, match.scene_title, match.tpdb_id,
                                                    pre_suffix, match.manual_suffix, p.input.filename_strip_words)
    if p.naming.filename_mode == "user_input":
        from fileprep.utils.text import sanitize_site_filename_part
        user_part = await get_selected_filename(p.input.filename_strip_words, p.naming.max_filename_length, notify)
        site_part = await sanitize_site_filename_part(match.site)
        fp.auto_base_name = f"{site_part}.{date.year}.{date.month}.{date.day}.{user_part}"
    else:
        fp.auto_base_name, warnings = naming.select_filename(candidates, p.naming.filename_mode, p.naming.max_filename_length)
        fp.issues.extend(PlanIssue(level="warning", code="NAME_FALLBACK", file=original_name, message=w) for w in warnings)
    await apply_names(job, directory, original_name, fp)


# issue codes that depend on the final name - recomputed whenever the names change
NAME_ISSUE_CODES = {"PATH_TOO_LONG", "TARGET_EXISTS", "FOLDER_EXISTS", "FOLDER_RENAME", "OUTPUT_EXISTS_ASK", "ALREADY_HEVC",
                    "DUPLICATE_TARGET", "TARGET_IS_INPUT", "CUSTOM_NAME", "BAD_NAME"}


async def apply_names(job: JobConfig, directory: str, original_name: str, fp: FilePlan) -> None:
    """Final name, title, folder, outputs and steps of a matched file: TPDB name (``auto_base_name``) + suffixes,
    or the user's custom name. No TPDB access, so it can be re-applied after the preview."""
    p = job.processing
    match = fp.match
    file_path = Path(directory) / original_name
    fp.issues = [i for i in fp.issues if i.code not in NAME_ISSUE_CODES]
    date = naming.format_scene_date(match.date, p.naming.title_date_format)
    fp.scene_pretty_date = date.pretty
    try:
        extra = naming.clean_suffix(p.naming.extra_suffix)
    except ValueError:
        extra = ""  # reported by validate_job_config
    fp.suffix = naming.combine_suffixes(fp.flag_suffix, extra)
    custom = (p.naming.filename_overrides.get(original_name) or p.naming.filename_overrides.get(fp.source_name) or "").strip()
    fp.custom_name = bool(custom)
    if custom:
        fp.new_base_name = custom
        fp.new_file_name = custom + file_path.suffix
        fp.issues.append(PlanIssue(level="info", code="CUSTOM_NAME", file=original_name, message="Custom file name set in the preview."))
        if not is_valid_filename(custom) or len(custom) > p.naming.max_filename_length:
            fp.issues.append(PlanIssue(level="error", code="BAD_NAME", file=original_name,
                                       message=f"Custom name has invalid characters or is longer than {p.naming.max_filename_length}."))
    else:
        fp.new_base_name = fp.auto_base_name
        fp.new_file_name = naming.full_file_name(fp.new_base_name, fp.suffix, file_path.suffix)

    target = os.path.join(directory, fp.new_file_name)
    if len(target) > p.naming.max_full_path_length:
        fp.issues.append(PlanIssue(level="error", code="PATH_TOO_LONG", file=original_name,
                                   message=f"Target path is {len(target)} characters (limit {p.naming.max_full_path_length}): {target}"))
    if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(str(file_path)) \
            and target.lower() != str(file_path).lower():
        fp.issues.append(PlanIssue(level="error", code="TARGET_EXISTS", file=original_name,
                                   message=f"A different file already uses the target name: {fp.new_file_name}"))

    fp.title, fp.studio_info, fp.studio_tags, has_performers = await naming.build_title(
        match.site, match.studio, date, match.scene_title, match.performers, fp.suffix)
    if match.slug:
        m = p.matching
        base_url = {"jav": m.jav_url, "movies": m.movies_url}.get(m.api_mode, m.scenes_url)
        fp.tpdb_url = f"{base_url}{match.slug}"

    # names of the outputs / folder: <base>.<suffix> (a custom name already is the complete name)
    out_base, out_suffix = (fp.new_base_name, "") if custom else (fp.new_base_name, fp.suffix)
    if p.naming.create_sub_folder:
        fp.output_directory = os.path.join(directory, naming.folder_name(out_base, out_suffix))
        old_folder = os.path.join(directory, Path(original_name).stem)
        if os.path.isdir(fp.output_directory):
            fp.issues.append(PlanIssue(level="info", code="FOLDER_EXISTS", file=original_name,
                                       message=f"Folder already exists and will be reused: {os.path.basename(fp.output_directory)}"))
        elif os.path.isdir(old_folder) and old_folder != fp.output_directory:
            fp.issues.append(PlanIssue(level="info", code="FOLDER_RENAME", file=original_name,
                                       message=f"Existing folder '{Path(original_name).stem}' will be renamed."))
    else:
        fp.output_directory = directory
    fp.outputs = _outputs(job, fp.output_directory, out_base, out_suffix)
    for out in fp.outputs:
        if out.exists and "user input" in out.note:
            fp.issues.append(PlanIssue(level="info", code="OUTPUT_EXISTS_ASK", file=original_name,
                                       message=f"{os.path.basename(out.path)} exists - you will be asked whether to keep it."))

    # Steps that will run (re-encode is decided here from the current codec)
    steps = ["rename"] if fp.new_file_name != original_name else []
    if p.encoding.keep_single_audio_track:
        steps.append("audio track check")
    if p.encoding.re_encode:
        already = await is_video_hevc_or_av1(str(file_path), p.encoding.warn_on_crf_match, p.encoding.crf)
        steps.append("metadata" if already else f"{p.encoding.codec.upper()} re-encode ({p.encoding.device.upper()})")
        if already:
            fp.issues.append(PlanIssue(level="info", code="ALREADY_HEVC", file=original_name,
                                       message="Already HEVC/AV1 - re-encoding will be skipped."))
    steps.append("metadata check")
    steps.extend(o.kind for o in fp.outputs if o.kind not in ("tags",))
    if p.tracker.enabled:
        steps.extend(f"tracker: {t}" for t in p.tracker.trackers)
    if p.matching.add_to_collection and match.tpdb_id and not match.full_manual:
        steps.append("TPDB collection")
    fp.steps = list(dict.fromkeys(steps))


async def reapply_names(job: JobConfig, plan: JobPlan) -> JobPlan:
    """Re-apply suffix / custom names to an existing preview (no TPDB lookups, no questions)."""
    directory = job.processing.input.working_directory
    originals = {new: old for old, new in plan.renames}
    for fp in plan.files:
        if not fp.match:
            continue
        if not fp.auto_base_name:  # preview made by 14.6 or older
            fp.auto_base_name, fp.flag_suffix = fp.new_base_name, fp.suffix
        await apply_names(job, directory, originals.get(fp.source_name, fp.source_name), fp)
    _detect_cross_file_conflicts(plan, directory)
    for fp in plan.files:
        if fp.match:
            fp.status = ("error" if any(i.level == "error" for i in fp.issues)
                         else "warning" if any(i.level == "warning" for i in fp.issues) else "ok")
    plan.config_hash = config_hash(job.model_dump(mode="json"))
    return plan


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _detect_job_conflicts(plan: JobPlan, claimed: dict[str, str]) -> None:
    """A file whose output (sub) folder is the working folder of another queued/running job (or one of its
    output folders) would mix the two jobs' files."""
    for fp in plan.files:
        if fp.output_directory and _norm(fp.output_directory) in claimed and _norm(fp.output_directory) != _norm(plan.working_directory):
            fp.issues.append(PlanIssue(level="error", code="OUTPUT_DIRECTORY_IN_USE", file=fp.source_name,
                                       message=f"Output folder {fp.output_directory} is used by job {claimed[_norm(fp.output_directory)]} "
                                               f"- rename the file or wait for that job."))
            fp.status = "error"


def _detect_cross_file_conflicts(plan: JobPlan, directory: str) -> None:
    by_target: dict[str, list[FilePlan]] = {}
    for fp in plan.files:
        if fp.new_file_name:
            by_target.setdefault(fp.new_file_name.lower(), []).append(fp)
    for group in by_target.values():
        if len(group) > 1:
            names = ", ".join(f.source_name for f in group)
            for fp in group:
                fp.issues.append(PlanIssue(level="error", code="DUPLICATE_TARGET", file=fp.source_name,
                                           message=f"{len(group)} files would be named {fp.new_file_name}: {names}"))
                fp.status = "error"
    sources = {fp.source_name.lower() for fp in plan.files}
    for fp in plan.files:
        # renaming onto the current name of another input file of this job
        if fp.new_file_name and fp.new_file_name.lower() in sources and fp.new_file_name.lower() != fp.source_name.lower():
            fp.issues.append(PlanIssue(level="error", code="TARGET_IS_INPUT", file=fp.source_name,
                                       message=f"Target name {fp.new_file_name} is another input file of this job."))
            fp.status = "error"
