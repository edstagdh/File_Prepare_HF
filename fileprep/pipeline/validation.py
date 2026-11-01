"""
Job-level validation of a settings snapshot (the former ``exit(NN)`` checks of main.py, plus new ones).

Returns :class:`PlanIssue` objects: ``error`` blocks the job, ``warning`` is shown but allowed.
Legacy exit codes are kept in the issue code for reference (docs/exit_codes.json).
"""

import os

from fileprep import paths
from fileprep.config.models import Credentials, JobConfig
from fileprep.pipeline.plan import PlanIssue
from fileprep.system.environment import python_version, python_version_supported
from fileprep.system.ffmpeg import FfmpegReport
from fileprep.uploaders.service import HOST_INFO


def _issue(level, code, message) -> PlanIssue:
    return PlanIssue(level=level, code=code, message=message)


def validate_job_config(job: JobConfig, credentials: Credentials, ffmpeg: FfmpegReport,
                        python_min: list[int], python_max: list[int]) -> list[PlanIssue]:
    p = job.processing
    issues: list[PlanIssue] = []
    error = lambda code, msg: issues.append(_issue("error", code, msg))  # noqa: E731
    warn = lambda code, msg: issues.append(_issue("warning", code, msg))  # noqa: E731
    info = lambda code, msg: issues.append(_issue("info", code, msg))  # noqa: E731
    full_manual = p.matching.matching_mode == "full_manual"

    # ---------------------------------------------------------- environment
    if not python_version_supported(python_min, python_max):
        error("E36_PYTHON", f"Python {python_version()} is outside the supported range {python_min} - {python_max}.")
    if not ffmpeg.ffmpeg.ok or not ffmpeg.ffprobe.ok:
        error("E33_FFMPEG", "ffmpeg/ffprobe missing or too old - see Tools > ffmpeg.")
    e = p.encoding
    if e.re_encode and e.device == "cpu" and ffmpeg.ffmpeg.found:
        if e.codec == "hevc" and not ffmpeg.encoders.get("libx265"):
            error("FFMPEG_NO_X265", "HEVC CPU re-encoding is enabled but this ffmpeg build has no libx265 encoder.")
        if e.codec == "av1" and not (ffmpeg.encoders.get("libsvtav1") or ffmpeg.encoders.get("libaom-av1")):
            error("FFMPEG_NO_AV1", "AV1 CPU re-encoding is enabled but this ffmpeg build has neither libsvtav1 nor libaom-av1.")
        if e.codec == "av1" and not ffmpeg.encoders.get("libsvtav1") and ffmpeg.encoders.get("libaom-av1"):
            issues.append(_issue("warning", "AV1_LIBAOM", "libsvtav1 is missing - AV1 uses libaom-av1, which is very slow."))
    if p.outputs.video_preview and ffmpeg.ffmpeg.found:
        vp = job.video_preview
        if (vp.create_webp_preview or vp.create_webp_sheet) and not ffmpeg.encoders.get("libwebp"):
            error("FFMPEG_NO_WEBP", "WebP previews are enabled but this ffmpeg build has no libwebp encoder.")
        if (vp.create_avif_preview or vp.create_avif_sheet) and not (ffmpeg.encoders.get("libsvtav1") or ffmpeg.encoders.get("libaom-av1")):
            error("FFMPEG_NO_AVIF", "AVIF previews are enabled but this ffmpeg build has neither libsvtav1 nor libaom-av1.")
        if (vp.create_webm_preview or vp.create_webm_sheet) and not ffmpeg.encoders.get("libvpx-vp9"):
            error("FFMPEG_NO_VP9", "WebM previews are enabled but this ffmpeg build has no libvpx-vp9 encoder.")

    # ---------------------------------------------------------- input / matching
    directory = p.input.working_directory
    if not directory or not os.path.isdir(directory):
        error("E27_DIRECTORY", f"Working directory does not exist or is not accessible: {directory or '(empty)'}")
    if not full_manual and not credentials.tpdb.api_token:
        error("TPDB_TOKEN", "TPDB API token is missing (Settings > Credentials).")
    if full_manual and (p.outputs.cover_image or p.outputs.performer_portraits):
        info("FULL_MANUAL", "Full manual mode: cover image and performer portraits are disabled.")

    # ---------------------------------------------------------- naming
    from fileprep.pipeline.naming import clean_suffix
    try:
        clean_suffix(p.naming.extra_suffix)
    except ValueError as exc:
        error("BAD_SUFFIX", f"Suffix for all files: {exc}")

    # ---------------------------------------------------------- metadata / outputs
    if p.metadata.set_exclusive and not p.metadata.exclusive_tag.strip():
        error("E49_EXCLUSIVE", "Exclusive tagging is enabled but the exclusive tag value is empty.")
    if p.outputs.performer_portraits and not full_manual and not paths.FACE_DETECTION_MODEL.is_file():
        error("E50_FACE_MODEL", f"Face detection model not found: {paths.FACE_DETECTION_MODEL}")
    if p.outputs.performer_portraits and not paths.PERFORMER_IMAGES_FILE.is_file():
        warn("PERFORMER_IMAGES_FILE", "Resources/Performers_Images.json is missing - pick performer images on the job page "
                                      "(this creates it) or sync it in Tools > Resource sync.")
    for font in {p.outputs.font_file, job.thumbnails.font_file, job.video_preview.font_file}:
        if font and not paths.resource(font).is_file():
            warn("FONT_MISSING", f"Font file not found in Resources: {font} (a default font will be used where possible).")
    if p.outputs.upload_template:
        template = paths.resource(p.outputs.template_file)
        if not p.outputs.template_file or not template.is_file():
            error("E35_TEMPLATE", f"Template file not found: {template}")
        if not p.outputs.mediainfo_file:
            error("E37_TEMPLATE_MEDIAINFO", "The upload template needs the MediaInfo file output enabled.")
        if not paths.BBCODE_IMAGES_FILE.is_file():
            error("TEMPLATE_ICONS", "Resources/BBCode_Images.json is missing.")
    if p.outputs.thumbnails_sheet and job.thumbnails.thumbnail_count % job.thumbnails.columns:
        error("THUMBS_LAYOUT", "Thumbnails: the number of thumbnails must be divisible by the number of columns.")
    if p.outputs.video_preview:
        issues.extend(_validate_preview(job))

    # ---------------------------------------------------------- image hosts
    asset_enabled = {"cover": p.outputs.cover_image, "thumbnails": p.outputs.thumbnails_sheet, "previews": p.outputs.video_preview}
    host_lists = {"cover": p.image_hosts.cover_hosts, "thumbnails": p.image_hosts.thumbnails_hosts, "previews": p.image_hosts.preview_hosts}
    image_ext = "." + p.outputs.image_format
    for asset, hosts in host_lists.items():
        if not hosts or not asset_enabled[asset]:
            continue
        usable = []
        for host in hosts:
            missing = [f for f in HOST_INFO[host].needs if not getattr(credentials.image_hosts, f)]
            if missing:
                (warn if p.image_hosts.mode == "fallback" and len(hosts) > 1 else error)(
                    "HOST_CREDENTIALS", f"{host}: missing credentials ({', '.join(missing)}) for {asset} uploads.")
                continue
            if asset != "previews" and image_ext not in HOST_INFO[host].extensions:
                warn("HOST_FORMAT", f"{host} does not accept {p.outputs.image_format} images - skipped for {asset}.")
                continue
            if asset == "previews" and host == "imgbox":
                warn("HOST_FORMAT", "imgbox does not accept animated previews - skipped.")
                continue
            if asset == "previews" and (job.video_preview.create_avif_preview or job.video_preview.create_avif_sheet) \
                    and ".avif" not in HOST_INFO[host].extensions:
                warn("HOST_FORMAT", f"{host} does not accept AVIF - the AVIF previews are not uploaded there.")
            usable.append(host)
        if not usable:
            error("HOST_NONE_USABLE", f"No usable image host for {asset} uploads ({', '.join(hosts)}).")

    # ---------------------------------------------------------- tracker
    if p.tracker.enabled:
        issues.extend(_validate_tracker(job, credentials))

    # ---------------------------------------------------------- notifications
    if p.notifications.enabled and p.notifications.notifier == "telegram":
        if not credentials.notifiers.telegram_bot_token or not credentials.notifiers.telegram_chat_id:
            warn("NOTIFIER_CREDENTIALS", "Telegram notifications are enabled but the bot token / chat id is missing.")
    return issues


def _validate_preview(job: JobConfig) -> list[PlanIssue]:
    vp = job.video_preview
    out = []
    if vp.gif_segment_count > vp.segment_count:
        out.append(_issue("error", "PREVIEW_GIF_SEGMENTS", "Video preview: GIF segments exceed the number of segments."))
    if vp.create_webp_sheet or vp.create_avif_sheet or vp.create_gif_sheet or vp.create_webm_sheet:
        limits = {3: (9, 30), 4: (12, 28)}[vp.grid_columns]
        if vp.segment_count % vp.grid_columns or not limits[0] <= vp.segment_count <= limits[1]:
            out.append(_issue("error", "PREVIEW_GRID",
                              f"Video preview sheet: with {vp.grid_columns} columns the segments must be divisible by "
                              f"{vp.grid_columns} and between {limits[0]} and {limits[1]}."))
    return out


def _validate_tracker(job: JobConfig, credentials: Credentials) -> list[PlanIssue]:
    p, t = job.processing, job.tracker
    out = []
    err = lambda code, msg: out.append(_issue("error", code, msg))  # noqa: E731
    if not p.naming.create_sub_folder:
        err("E40_TRACKER_SUBFOLDER", "Tracker upload needs 'Create a sub folder per file' (single release mode).")
    if not p.outputs.upload_template:
        err("E42_TRACKER_TEMPLATE", "Tracker upload needs the upload template output.")
    if not (p.outputs.video_preview and job.video_preview.create_webp_preview and p.image_hosts.preview_hosts):
        err("E43_TRACKER_PREVIEW_HOST", "Tracker upload needs a WebP preview uploaded to at least one preview image host.")
    if not t.torrent.excluded_suffixes:
        err("TORRENT_SUFFIXES", "Torrent excluded suffixes must not be empty.")
    if any(s.lower() == ".mp4" for s in t.torrent.excluded_suffixes):
        err("TORRENT_SUFFIXES_MP4", "Torrent excluded suffixes must not contain '.mp4'.")
    for name in p.tracker.trackers:
        site = t.sites.get(name)
        account = credentials.trackers.get(name)
        if not site or not site.upload_url or "SITE." in site.upload_url:
            err("TRACKER_URL", f"Tracker '{name}': upload/login URLs are not configured (Settings > Tracker).")
        if not account or not account.username or not account.password or not account.announce_url:
            err("TRACKER_CREDENTIALS", f"Tracker '{name}': username, password or announce URL missing in credentials.")
    b = t.browser
    for label, path in (("geckodriver", b.firefox_driver_path if b.browser == "firefox" else ""),
                        ("chromedriver", b.chrome_driver_path if b.browser == "chrome" else ""),
                        ("browser profile", b.firefox_profile_path if b.browser == "firefox" else b.chrome_profile_path)):
        if path and not os.path.exists(path):
            err("TRACKER_BROWSER_PATH", f"Tracker browser: {label} path does not exist: {path}")
    return out
