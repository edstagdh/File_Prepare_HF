"""
Typed configuration models.

Every settings file in ``Configs/`` is described by one top-level model below. Each nested model is a
*section*; the web UI renders one tab per section, straight from these definitions (labels, help text,
choices and visibility rules come from ``json_schema_extra``), so adding a field here is enough to make
it editable in the UI.

Legacy (pre-14.0) key names are mapped in :mod:`fileprep.config.legacy`.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ImageHost = Literal["hamster", "pixhost", "imgbb", "imgbox"]
RegenerationMode = Literal["user input", "force regenerate", "force keep"]
ImageFormat = Literal["webp", "avif", "jpg", "jpeg", "png", "bmp"]  # avif: Pixhost only (not Hamster / imgbox)

TITLE_DATE_FORMATS = {
    "YYYY-Month-DD": "2025-January-01",
    "YY-Month-DD": "25-January-01",
    "YYYY-MM-DD": "2025-01-01",
    "YY-MM-DD": "25-01-01",
    "YYYY.Month.DD": "2025.January.01",
    "YY.Month.DD": "25.January.01",
    "YYYY.MM.DD": "2025.01.01",
    "YY.MM.DD": "25.01.01",
}
TitleDateFormat = Literal["YYYY-Month-DD", "YY-Month-DD", "YYYY-MM-DD", "YY-MM-DD",
"YYYY.Month.DD", "YY.Month.DD", "YYYY.MM.DD", "YY.MM.DD"]


def setting(default: Any = ..., label: str = "", help: str = "", **ui: Any) -> Any:
    """Field with UI metadata. ``ui`` keys: choices_help, visible_if, multiline, secret, min, max, step."""
    extra = {"label": label, **ui}
    kwargs: dict[str, Any] = {"description": help, "json_schema_extra": extra}
    if default in (list, dict) or (callable(default) and not isinstance(default, type)):
        kwargs["default_factory"] = default
    else:
        kwargs["default"] = default
    for key, attr in (("min", "ge"), ("max", "le")):
        if key in ui:
            kwargs[attr] = ui[key]
    return Field(**kwargs)


class Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


def section(model: type, label: str, help: str = "", visible_if: str | None = None, icon: str = "") -> Any:
    extra = {"label": label, "section": True, "icon": icon}
    if visible_if:
        extra["visible_if"] = visible_if
    return Field(default_factory=model, description=help, json_schema_extra=extra)


# ---------------------------------------------------------------------------------------------------
# Processing (Configs/processing.json) - per job settings
# ---------------------------------------------------------------------------------------------------
class InputSettings(Section):
    working_directory: str = setting("", "Working directory",
                                     "Folder containing the .mp4 files to process (sub-folders are not scanned).")
    ignored_filenames: list[str] = setting(list, "Ignored file names", "Exact file names to skip (one per line).")
    filename_strip_words: list[str] = setting(lambda: [".Hardcore"], "Words stripped from names",
                                              "Removed from input file names (strict mode) and from generated titles.")
    excluded_folder: str = setting("_excluded", "Moved-out files folder",
                                   "Where 'Move out' puts files removed from a job (relative to the working directory, or absolute).")


class MatchingSettings(Section):
    matching_mode: Literal["strict", "free_string_parse", "full_manual"] = setting(
        "free_string_parse", "Matching mode", "How files are matched to TPDB entries.",
        choices_help={"strict": "File name must follow STUDIO.YY.MM.DD.PERFORMER format.",
                      "free_string_parse": "File name is sent to the TPDB parser as free text.",
                      "full_manual": "No TPDB lookup; all metadata is entered manually (cover/portraits disabled)."})
    api_mode: Literal["scenes", "jav", "movies"] = setting("scenes", "TPDB database", "Which TPDB catalogue is searched.")
    movie_entry_type: Literal["scene", "full"] = setting(
        "scene", "Movie file type", "Only for the movies database.",
        choices_help={"scene": "File is a single scene of a movie", "full": "File is the full movie"},
        visible_if="matching.api_mode == movies")
    match_by_oshash_first: bool = setting(False, "Try OSHash match first", "Look the file up by its OSHash before parsing the name.")
    rematch_using_embedded_uuid: bool = setting(True, "Re-match using embedded TPDB id",
                                                "Use the TPDB uuid stored in the file metadata by a previous run.")
    manual_performer_entry: bool = setting(False, "Manual performer entry (strict mode)",
                                           "Strict mode only: always type performer names instead of extracting them.")
    add_to_collection: bool = setting(True, "Add to TPDB collection", "Add each processed scene to your TPDB collection.")
    uuid_overrides: dict[str, str] = setting(dict, "TPDB UUID per file",
                                             "File name -> TPDB scene UUID found by a manual search; the file is looked up "
                                             "directly by this UUID. Set from the preview with 'Set TPDB UUID'.")
    scenes_url: str = setting("https://theporndb.net/scenes/", "TPDB scenes URL")
    jav_url: str = setting("https://theporndb.net/jav/", "TPDB JAV URL")
    movies_url: str = setting("https://theporndb.net/movies/", "TPDB movies URL")
    performers_url: str = setting("https://theporndb.net/performers/", "TPDB performers URL")


class ReviewSettings(Section):
    approve_performers: bool = setting(False, "Review performers", "Interactively edit the performer list of every match.")
    approve_date: bool = setting(False, "Review date", "Confirm or replace the scene date of every match.")
    approve_title: bool = setting(False, "Review title", "Confirm or replace the scene title of every match.")
    warn_on_duration_mismatch: bool = setting(True, "Warn on duration mismatch",
                                              "Warn when the file length differs from the TPDB scene length.")
    add_chapter_markers: bool = setting(False, "Add chapter markers", "Write TPDB scene markers as MP4 chapters.")


class NamingSettings(Section):
    filename_mode: Literal["names", "title", "both", "tpdb_id", "user_input"] = setting(
        "names", "File name mode", "What follows STUDIO.YY.MM.DD in the new file name.",
        choices_help={"names": "Performer names (falls back to title, then TPDB id when too long)",
                      "title": "Scene title (falls back to names, then TPDB id)",
                      "both": "Names and title (falls back to names, title, TPDB id)",
                      "tpdb_id": "Numeric TPDB id",
                      "user_input": "Asked for every file"})
    title_date_format: TitleDateFormat = setting("YYYY-Month-DD", "Title date format",
                                                 "Date format used in the generated video title.",
                                                 choices_help=TITLE_DATE_FORMATS)
    title_strip_strings: list[str] = setting(list, "Strings removed from titles", "One per line.")
    ignore_part_number: bool = setting(False, "Ignore 'Part N' in file name",
                                       "Do not carry a detected 'Part N' token into the new name.")
    ignore_resolution_tag: bool = setting(True, "Ignore resolution tag",
                                          "Do not carry a detected 480p/720p/1080p/1440p/2160p token into the new name.")
    ignore_performer_id: bool = setting(True, "Ignore performer ID suffix",
                                        "Strip 'ID <digits>' disambiguation tokens from performer names in file names.")
    ask_manual_suffix: bool = setting(False, "Ask for a name suffix (full manual)", "Full manual mode only.",
                                      visible_if="matching.matching_mode == full_manual")
    extra_suffix: str = setting("", "Suffix for all files",
                                "Added after every new file name (and video title), e.g. VR2Normal -> Studio.25.01.01.Jane.Doe.VR2Normal.mp4. "
                                "Combined with a suffix detected from the file name. Can be changed after the preview without previewing again.")
    filename_overrides: dict[str, str] = setting(dict, "Custom file names",
                                                 "Original file name -> new name (without extension), set from the preview "
                                                 "with 'Rename'. Used as-is: the suffix is not added again.", hidden=True)
    filename_mode_overrides: dict[str, Literal["names", "title", "both", "tpdb_id"]] = setting(
        dict, "File name mode per file", "Original file name -> file name mode for that file, set from the preview "
                                         "('Name' list). Overrides the job's file name mode.", hidden=True)
    create_sub_folder: bool = setting(False, "Create a sub folder per file",
                                      "Move every file and its outputs into its own folder (required for tracker uploads).")
    max_filename_length: int = setting(200, "Maximum file name length", min=40, max=250)
    max_full_path_length: int = setting(240, "Maximum full path length", "Safety margin below Windows MAX_PATH.", min=60, max=4096)


class EncodingSettings(Section):
    re_encode: bool = setting(True, "Re-encode the video",
                              "Only one CPU-encode job and one GPU-encode job run at a time (Settings > Application > Jobs).")
    codec: Literal["hevc", "av1"] = setting("hevc", "Codec", visible_if="encoding.re_encode",
                                            choices_help={"hevc": "HEVC / H.265", "av1": "AV1 (smaller files, slower on CPU)"})
    device: Literal["cpu", "gpu"] = setting(
        "cpu", "Encoder device", "GPU backend and detection: Settings > Application > Hardware acceleration / Tools.",
        visible_if="encoding.re_encode",
        choices_help={"cpu": "CPU (libx265 / SVT-AV1) - best quality per size", "gpu": "GPU (NVENC / QSV / AMF / VAAPI / VideoToolbox) - much faster"})
    crf: int = setting(24, "HEVC CRF (CPU)", "x265 constant rate factor (0-29, lower is better quality).", min=0, max=29,
                       visible_if="encoding.codec == hevc")
    av1_crf: int = setting(30, "AV1 CRF (CPU)", "SVT-AV1 / libaom CRF (0-63, lower is better quality).", min=0, max=63,
                           visible_if="encoding.codec == av1")
    av1_preset: int = setting(6, "AV1 preset (CPU)", "SVT-AV1 preset 0-13 (lower = slower, better).", min=0, max=13,
                              visible_if="encoding.codec == av1")
    gpu_quality: int = setting(26, "GPU quality (CQ/QP)", "1-51, lower is better quality.", min=1, max=51,
                               visible_if="encoding.device == gpu")
    gpu_preset: Literal["fast", "balanced", "quality"] = setting("balanced", "GPU preset", visible_if="encoding.device == gpu")
    fallback_to_cpu: bool = setting(False, "Fall back to CPU when the GPU encoder fails",
                                    "Off: the file fails instead (the preview reports a missing GPU encoder).",
                                    visible_if="encoding.device == gpu")
    warn_on_crf_match: bool = setting(True, "Warn when CRF already matches", visible_if="encoding.re_encode")
    downscale_to_1080p: bool = setting(True, "Downscale above 1080p", visible_if="encoding.re_encode")
    limit_cpu_usage: bool = setting(True, "Limit encoder CPU usage", visible_if="encoding.device == cpu")
    cpu_usage_percent: int = setting(70, "CPU usage limit (%)", "Share of CPU cores given to the CPU encoder when limited.",
                                     min=10, max=100, visible_if="encoding.limit_cpu_usage")
    remove_existing_chapters: bool = setting(False, "Remove existing chapters", visible_if="encoding.re_encode")
    keep_original_file: bool = setting(True, "Keep original file (as *_old.mp4)", visible_if="encoding.re_encode")
    keep_single_audio_track: bool = setting(True, "Keep a single audio track",
                                            "Ask which audio track(s) to keep when a file has several.")

    @model_validator(mode="before")
    @classmethod
    def _legacy_flag(cls, data):
        # 14.0-14.2 configs and saved jobs used "re_encode_hevc"
        if isinstance(data, dict) and "re_encode_hevc" in data and "re_encode" not in data:
            data = {**data, "re_encode": data["re_encode_hevc"]}
        return data


class MetadataSettings(Section):
    set_exclusive: bool = setting(False, "Tag as exclusive", "Write an 'exclusive' tag into the file metadata.")
    exclusive_tag: str = setting("", "Exclusive tag value", visible_if="metadata.set_exclusive")


class OutputSettings(Section):
    cover_image: bool = setting(False, "Cover image", "Download the TPDB cover image.")
    cover_regeneration_mode: RegenerationMode = setting("force keep", "When a cover already exists",
                                                        visible_if="outputs.cover_image")
    thumbnails_sheet: bool = setting(False, "Thumbnails sheet", "Static contact sheet (see the Thumbnails tab).")
    video_preview: bool = setting(False, "Video preview", "Animated previews (see the Video preview tab).")
    mediainfo_file: bool = setting(False, "MediaInfo file", "Required by the upload template.")
    performer_portraits: bool = setting(False, "Performer images",
                                        "Show the 'Performer images' panel on the job page to pick a face crop / image for every performer "
                                        "missing from Performers_Images.json before the run. The run itself never creates portraits.")
    upload_template: bool = setting(False, "Upload template (BBCode)", "Builds <name>_template.txt and <name>_tags.txt.")
    image_format: ImageFormat = setting("webp", "Image format", "Format of the cover and thumbnails sheet. AVIF is accepted by Pixhost only (not Hamster, imgbb or imgbox).")
    template_file: str = setting("HF_Template.txt", "Template file", "File inside Resources/.", visible_if="outputs.upload_template")
    template_icon_base_url: str = setting("https://hamsterimg.net", "Template icon base URL",
                                          "Prefix for the icon paths in Resources/BBCode_Images.json.",
                                          visible_if="outputs.upload_template")
    font_file: str = setting("Gotham_Medium.otf", "Font file", "Font inside Resources/ used for text overlays.")


class PortraitSettings(Section):
    width: int = setting(195, "Width (px)", min=32, max=2000)
    height: int = setting(230, "Height (px)", min=32, max=2000)
    zoom_factor: float = setting(2.5, "Zoom factor", "How far the crop extends around the detected face.", min=1.0, max=10.0, step=0.1)
    face_score_threshold: float = setting(0.75, "Face score threshold", min=0.1, max=1.0, step=0.05)
    blur_kernel_size: int = setting(3, "Mask blur kernel size", "Odd number.", min=1, max=99)
    posters_limit: int = setting(5, "Posters per performer", min=1, max=50)
    image_format: ImageFormat = setting("webp", "Image format")


class ImageHostSettings(Section):
    mode: Literal["fallback", "all"] = setting(
        "fallback", "Upload mode", "How the host lists below are used.",
        choices_help={"fallback": "Try hosts in order and stop at the first success (recommended).",
                      "all": "Upload to every listed host."})
    cover_hosts: list[ImageHost] = setting(list, "Cover hosts", "Ordered list; first = preferred.", ordered=True,
                                           visible_if="outputs.cover_image")
    thumbnails_hosts: list[ImageHost] = setting(list, "Thumbnails sheet hosts", "Ordered list; first = preferred.",
                                                ordered=True, visible_if="outputs.thumbnails_sheet")
    preview_hosts: list[ImageHost] = setting(list, "Preview hosts", "Animated previews (imgbox does not support them).",
                                             ordered=True, visible_if="outputs.video_preview")
    portrait_hosts: list[ImageHost] = setting(lambda: ["hamster", "pixhost"], "Portrait hosts",
                                              "Hosts for performer images picked on the job page (first working host wins).",
                                              ordered=True)
    imgbb_headless: bool = setting(True, "Run the ImgBB browser headless")


class TrackerUploadSettings(Section):
    enabled: bool = setting(False, "Upload to tracker",
                            "Requires: sub folders, upload template, and a preview image host.")
    trackers: list[str] = setting(lambda: ["emp"], "Trackers", "Uploaded in this order. Sites are defined in Settings > Tracker sites.",
                                  ordered=True, choices_from="tracker_sites")
    upload_mode: Literal["single"] = setting("single", "Release mode", "One torrent per processed file (its sub folder).")
    remove_extra_files_after_upload: bool = setting(True, "Remove helper files after upload",
                                                    "Delete files matching the ignore suffixes after the last tracker.")
    wait_for_upload_before_next_file: bool = setting(
        False, "Wait for each upload before the next file",
        "Off: the next file is processed while you finish the tracker form in the browser.")


class NotificationSettings(Section):
    enabled: bool = setting(False, "Send notifications", "Push a message when input is needed or a job finishes.")
    notifier: Literal["telegram"] = setting("telegram", "Notifier", visible_if="notifications.enabled")


class ProcessingSettings(BaseModel):
    """Configs/processing.json - everything a processing job needs (snapshotted per job)."""
    model_config = ConfigDict(extra="ignore")

    input: InputSettings = section(InputSettings, "Input", icon="folder")
    matching: MatchingSettings = section(MatchingSettings, "Matching", icon="search")
    review: ReviewSettings = section(ReviewSettings, "Review", icon="check")
    naming: NamingSettings = section(NamingSettings, "Naming", icon="tag")
    encoding: EncodingSettings = section(EncodingSettings, "Encoding", icon="cpu")
    metadata: MetadataSettings = section(MetadataSettings, "Metadata", icon="info")
    outputs: OutputSettings = section(OutputSettings, "Outputs", icon="image")
    portraits: PortraitSettings = section(PortraitSettings, "Portraits", visible_if="outputs.performer_portraits", icon="user")
    image_hosts: ImageHostSettings = section(ImageHostSettings, "Image hosts", icon="cloud")
    tracker: TrackerUploadSettings = section(TrackerUploadSettings, "Tracker upload", icon="upload")
    notifications: NotificationSettings = section(NotificationSettings, "Notifications", icon="bell")


# ---------------------------------------------------------------------------------------------------
# Thumbnails sheet (Configs/thumbnails.json)
# ---------------------------------------------------------------------------------------------------
class ThumbnailSettings(Section):
    thumbnail_count: int = setting(16, "Thumbnails", "Must be divisible by the number of columns.", min=2, max=100)
    thumbnail_width: int = setting(470, "Thumbnail width (px)", min=50, max=4000)
    columns: int = setting(4, "Columns", min=1, max=12)
    padding: int = setting(2, "Padding (px)", min=0, max=100)
    output_suffix: str = setting("thumbnails", "Output file suffix", "<name>_<suffix>.<format>")
    include_file_info: bool = setting(True, "File info header", "Adds a MediaInfo summary above the sheet.")
    compact_vertical_layout: bool = setting(True, "Compact layout for vertical videos",
                                            "Doubles the columns and halves the width for vertical videos.")
    alternate_layout: bool = setting(False, "Alternate layout")
    include_first_frame: bool = setting(False, "Include the first frame")
    regeneration_mode: RegenerationMode = setting("user input", "When a sheet already exists")
    font_file: str = setting("Gotham_Medium.otf", "Font file")


# ---------------------------------------------------------------------------------------------------
# Video preview (Configs/video_preview.json)
# ---------------------------------------------------------------------------------------------------
TRANSITIONS = ["fade", "fadeblack", "wipeleft", "wiperight", "slideup", "slidedown", "circleopen", "pixelize"]


class VideoPreviewSettings(Section):
    create_webp_preview: bool = setting(True, "WebP preview")
    create_webm_preview: bool = setting(False, "WebM preview")
    create_gif_preview: bool = setting(False, "GIF preview")
    create_webp_sheet: bool = setting(True, "WebP preview sheet", "Required by the upload template.")
    create_avif_preview: bool = setting(False, "AVIF preview")
    create_avif_sheet: bool = setting(False, "AVIF preview sheet")
    create_webm_sheet: bool = setting(False, "WebM preview sheet")
    create_gif_sheet: bool = setting(False, "GIF preview sheet")
    resolution: Literal["480p", "720p", "1080p"] = setting("720p", "Preview resolution",
                                                           "Segments are scaled to 854x480, 1280x720 or 1920x1080 (vertical: 480x854...); "
                                                           "smaller sources keep their own size.")
    segment_count: int = setting(16, "Segments", "Grid of 3: 9-30 and divisible by 3. Grid of 4: 12-28 and divisible by 4.",
                                 min=2, max=60)
    segment_duration: float = setting(1.5, "Segment duration (s)", min=0.2, max=10, step=0.1)
    grid_columns: Literal[3, 4] = setting(4, "Sheet grid columns")
    gif_segment_count: int = setting(8, "GIF segments", "Must not exceed the number of segments.", min=1, max=60)
    webp_fps: int = setting(24, "WebP fps", min=1, max=60)
    webp_compression_level: int = setting(4, "WebP effort (0-6)", min=0, max=6)
    avif_fps: int = setting(24, "AVIF fps", min=1, max=60,
                            visible_if="create_avif_preview || create_avif_sheet")
    avif_crf: int = setting(35, "AVIF quality (CRF)", "AV1 CRF of the AVIF previews: lower = better quality and bigger files.",
                            min=10, max=63, visible_if="create_avif_preview || create_avif_sheet")
    gif_fps: int = setting(15, "GIF fps", min=1, max=60)
    timestamps_mode: Literal[1, 2, 3] = setting(
        2, "Timestamp overlay", choices_help={"1": "Preview and sheet", "2": "Sheet only", "3": "None"})
    transition: Literal["fade", "fadeblack", "wipeleft", "wiperight", "slideup", "slidedown", "circleopen", "pixelize"] = \
        setting("fade", "Transition")
    transition_duration: float = setting(0.25, "Transition duration (s)", min=0, max=2, step=0.05)
    scene_change_threshold: float = setting(0.2, "Scene change threshold",
                                            "Cut points on a scene change are regenerated.", min=0, max=1, step=0.05)
    add_black_bars: bool = setting(False, "Add black bars")
    include_file_info: bool = setting(False, "File info header")
    compact_vertical_layout: bool = setting(True, "Compact layout for vertical videos")
    overwrite_existing: bool = setting(False, "Overwrite existing previews without asking")
    confirm_cut_points: bool = setting(False, "Confirm cut points", "Ask before using each generated set of cut points.")
    log_cut_points: bool = setting(False, "Log cut points")
    last_cut_point: float = setting(0, "Last cut point (s)", "0 = automatic (97.5-99% of the video).", min=0)
    blacklisted_cut_points: list[float] = setting(list, "Blacklisted cut points (fractions 0-1)", "One per line.")
    excluded_files: list[str] = setting(list, "Excluded files", "File names that never get previews.")
    custom_output_directory: str = setting("", "Custom output directory", "Empty = next to the video.")
    keep_temp_files: bool = setting(False, "Keep temporary files")
    font_file: str = setting("Gotham_Medium.otf", "Font file")


# ---------------------------------------------------------------------------------------------------
# Tracker site automation (Configs/tracker.json)
# ---------------------------------------------------------------------------------------------------
class Selector(BaseModel):
    by: Literal["id", "name", "css", "xpath", "class", "tag", "link"] = "id"
    value: str = ""


class BrowserSettings(Section):
    browser: Literal["firefox", "chrome"] = setting("firefox", "Browser")
    firefox_driver_path: str = setting("", "geckodriver path", "Empty = let Selenium Manager resolve it.", visible_if="browser.browser == firefox")
    firefox_profile_path: str = setting("", "Firefox profile path", visible_if="browser.browser == firefox")
    chrome_driver_path: str = setting("", "chromedriver path", "Empty = let Selenium Manager resolve it.", visible_if="browser.browser == chrome")
    chrome_profile_path: str = setting("", "Chrome profile path", visible_if="browser.browser == chrome")
    page_timeout_seconds: int = setting(15, "Element wait timeout (s)", min=3, max=300)
    session_idle_close_minutes: int = setting(30, "Close idle browser after (min)",
                                              "Each job's browser stays logged in between its files and closes when the job ends, or after this idle time.", min=1, max=1440)


class TrackerFormSettings(BaseModel):
    """Selectors of one site's login and upload pages."""
    model_config = ConfigDict(extra="ignore")
    login_username: Selector = Field(default_factory=lambda: Selector(by="name", value="username"))
    login_password: Selector = Field(default_factory=lambda: Selector(by="name", value="password"))
    login_submit: Selector = Field(default_factory=lambda: Selector(by="id", value="login_button"))
    login_success: Selector = Field(default_factory=lambda: Selector(by="css", value="ul#userinfo_username a.username"))
    category_field: Selector = Field(default_factory=lambda: Selector(by="id", value="category"))
    title_field: Selector = Field(default_factory=lambda: Selector(by="id", value="title"))
    tags_field: Selector = Field(default_factory=lambda: Selector(by="id", value="taginput"))
    image_field: Selector = Field(default_factory=lambda: Selector(by="id", value="image"))
    description_field: Selector = Field(default_factory=lambda: Selector(by="id", value="desc"))
    torrent_field: Selector = Field(default_factory=lambda: Selector(by="id", value="file"))
    dupe_check_button: Selector = Field(default_factory=lambda: Selector(by="name", value="checkonly"))
    dupe_message: Selector = Field(default_factory=lambda: Selector(by="id", value="messagebar"))
    dupe_message_alt: Selector = Field(default_factory=lambda: Selector(by="css", value="div.box.pad.shadow.center.rowa"))
    dupe_found_text: str = "possible dupes"
    dupe_none_text: str = "no exact size dupes"
    submit_button: Selector = Field(default_factory=lambda: Selector(by="id", value="post"))


class TrackerSite(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login_url: str = ""
    upload_url: str = ""
    category: str = ""
    two_factor_auth: bool = False
    form: TrackerFormSettings = Field(default_factory=TrackerFormSettings)


def _default_sites() -> dict[str, TrackerSite]:
    return {"hf": TrackerSite(login_url="https://www.SITE.org/login", upload_url="https://www.SITE.org/upload.php", category="Pron"),
            "emp": TrackerSite(login_url="https://www.SITE.is/login", upload_url="https://www.SITE.is/upload.php", category="Natural Tits")}


DEFAULT_IGNORE_SUFFIXES = ["_preview.webp", "_preview.avif", "_preview.webm", "_preview.gif", "_preview_sheet.webp",
                           "_preview_sheet.avif", "_preview_sheet.webm", "_preview_sheet.gif", "_hamster.txt", "_imgbb.txt", "_imgbox.txt", "_pixhost.txt",
                           "_template.txt", "_tags.txt", "_mediainfo.txt"]


class TorrentSettings(Section):
    excluded_suffixes: list[str] = setting(lambda: list(DEFAULT_IGNORE_SUFFIXES), "Excluded suffixes",
                                           "<name><suffix> files left out of the torrent (and removed after upload).")
    piece_size_kib: int = setting(256, "Piece size (KiB)", min=16, max=16384)

    @model_validator(mode="after")
    def _add_avif_suffixes(self):
        # lists saved before 14.8 exclude the WebP previews but not the matching AVIF ones
        for webp in ("_preview.webp", "_preview_sheet.webp"):
            avif = webp.replace(".webp", ".avif")
            if webp in self.excluded_suffixes and avif not in self.excluded_suffixes:
                self.excluded_suffixes.insert(self.excluded_suffixes.index(webp) + 1, avif)
        return self


class TrackerConfig(BaseModel):
    """Configs/tracker.json"""
    model_config = ConfigDict(extra="ignore")
    browser: BrowserSettings = section(BrowserSettings, "Tracker browser", visible_if="tracker.enabled", icon="globe")
    torrent: TorrentSettings = section(TorrentSettings, "Torrent", visible_if="tracker.enabled", icon="file")
    sites: dict[str, TrackerSite] = Field(default_factory=_default_sites,
                                          json_schema_extra={"label": "Tracker sites", "addable": True, "key_label": "site name"})

    @model_validator(mode="before")
    @classmethod
    def _per_site_selectors(cls, data):
        # 14.0-14.3 kept one global "form" and browser.two_factor_auth: copy them into every site
        if not isinstance(data, dict):
            return data
        data = dict(data)
        form = data.pop("form", None)
        two_fa = (data.get("browser") or {}).get("two_factor_auth")
        sites = data.get("sites")
        if isinstance(sites, dict) and (form is not None or two_fa is not None):
            data["sites"] = {name: {**({"form": form} if form is not None else {}),
                                    **({"two_factor_auth": two_fa} if two_fa is not None else {}), **site}
                             for name, site in sites.items()}
        return data


# ---------------------------------------------------------------------------------------------------
# Application (Configs/app.json) - web server, auth, job scheduling, resource sync
# ---------------------------------------------------------------------------------------------------
class ServerSettings(Section):
    host: str = setting("127.0.0.1", "Bind address", "Use 0.0.0.0 to allow other machines (put HTTPS in front).")
    port: int = setting(8765, "Port", min=1, max=65535)
    base_path: str = setting("", "Base path", "When served behind a reverse proxy sub path, e.g. /fileprep.")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = setting("INFO", "Console log level")
    open_browser: bool = setting(True, "Open the browser when main.py starts",
                                 "Off: only the address is printed in the console (same as main.py --no-browser).")
    browser_command: str = setting("", "Browser command",
                                   "Empty = the system default browser. To pick a browser or Firefox profile, e.g. "
                                   "\"C:\\Program Files\\Mozilla Firefox\\firefox.exe\" -P Work -new-tab {url}  ({url} is replaced).",
                                   visible_if="server.open_browser")


class AuthUser(BaseModel):
    username: str
    password_hash: str
    role: Literal["admin", "operator", "viewer"] = "admin"


class AuthSettings(Section):
    session_secret: str = setting("", "Session secret", "Generated automatically on first start.", secret=True)
    session_hours: int = setting(12, "Session lifetime (hours)", min=1, max=720)
    secure_cookies: bool = setting(False, "Secure cookies (HTTPS only)")
    max_failed_logins: int = setting(5, "Failed logins before lockout", min=1, max=100)
    lockout_minutes: int = setting(15, "Lockout duration (min)", min=1, max=1440)
    users: list[AuthUser] = Field(default_factory=list, json_schema_extra={"label": "Users", "hidden": True})


class JobSettings(Section):
    max_concurrent_jobs: int = setting(3, "Max concurrent jobs", "Encoding jobs are additionally limited below.", min=1, max=16)
    max_cpu_encode_jobs: int = setting(1, "Running CPU-encode jobs", "A CPU encode uses ~70% of the CPU - keep this at 1.", min=1, max=8)
    max_gpu_encode_jobs: int = setting(1, "Running GPU-encode jobs", "Consumer GPUs limit parallel encode sessions.", min=1, max=8)
    require_preview: bool = setting(True, "Require a clean preview before running",
                                    "A job can only be queued after a preview without blocking issues.")
    keep_finished_jobs_days: int = setting(30, "Keep finished jobs (days)", min=1, max=3650)
    prompt_context_lines: int = setting(40, "Log lines shown with a question", min=5, max=500)
    notify_on_finish: bool = setting(True, "Notify when a job finishes", "Uses the job's notifier if enabled.")


class ResourceSyncEntry(BaseModel):
    name: str
    url: str = Field(..., description="Raw GitHub URL (raw.githubusercontent.com / gist raw) of the file.")
    target: str = Field(..., description="Path relative to Resources/.")
    enabled: bool = True
    merge: Literal["replace", "merge_json"] = "replace"


class ResourceSyncSettings(Section):
    auto_sync_on_start: bool = setting(False, "Sync on start")
    github_token_from_credentials: bool = setting(True, "Use GitHub token from credentials", "Needed for private repos/gists.")
    entries: list[ResourceSyncEntry] = Field(default_factory=list, json_schema_extra={"label": "Synced resources", "hidden": True})


class EnvironmentSettings(Section):
    python_min_version: list[int] = setting(lambda: [3, 10], "Minimum Python version")
    python_max_version: list[int] = setting(lambda: [3, 14], "Maximum Python version")
    ffmpeg_path: str = setting("ffmpeg", "ffmpeg executable")
    ffprobe_path: str = setting("ffprobe", "ffprobe executable")
    ffmpeg_min_version: str = setting("4.0.0", "Minimum ffmpeg version")
    ffmpeg_min_build_date: str = setting("2024-09-01", "Minimum ffmpeg build date", "Accepted when either version or date is recent enough.")


class HardwareSettings(Section):
    gpu: Literal["auto", "nvidia", "intel", "amd", "apple", "vaapi", "off"] = setting(
        "auto", "GPU backend", "Auto picks the first backend that passes detection (Tools > ffmpeg).",
        choices_help={"auto": "Detect", "nvidia": "CUDA decode / NVENC", "intel": "Quick Sync", "amd": "D3D11VA+AMF (Windows) / VAAPI (Linux)",
                      "apple": "VideoToolbox", "vaapi": "VAAPI (Linux)", "off": "CPU only"})
    decode_thumbnails: bool = setting(True, "GPU decoding for thumbnails")
    decode_previews: bool = setting(True, "GPU decoding for preview segments")
    encode_preview_segments: bool = setting(True, "GPU encoding of preview segments", "Intermediate H.264 clips only.")
    cpu_fallback: bool = setting(True, "Retry thumbnails / previews on the CPU when the GPU fails")
    vaapi_device: str = setting("/dev/dri/renderD128", "VAAPI device (Linux)")


class UpdateSettings(Section):
    channel: Literal["stable", "dev", "off"] = setting(
        "stable", "Update channel", "Which line of releases this installation follows.",
        choices_help={"stable": "Stable releases (stable branch)", "dev": "Development builds (dev branch) - newest, less tested",
                      "off": "Do not check for updates"})
    check_on_start: bool = setting(True, "Check on start", "Check once when the server starts (and every few hours while it runs).",
                                   visible_if="updates.channel != off")
    repository: str = setting("edstagdh/File_Prepare_HF", "GitHub repository", "owner/name of the repository to compare against.")
    stable_branch: str = setting("master", "Stable branch")
    dev_branch: str = setting("dev", "Development branch")


class AppConfig(BaseModel):
    """Configs/app.json"""
    model_config = ConfigDict(extra="ignore")
    server: ServerSettings = section(ServerSettings, "Web server", icon="server")
    auth: AuthSettings = section(AuthSettings, "Authentication", icon="lock")
    jobs: JobSettings = section(JobSettings, "Jobs", icon="list")
    resource_sync: ResourceSyncSettings = section(ResourceSyncSettings, "Resource sync", icon="refresh")
    environment: EnvironmentSettings = section(EnvironmentSettings, "Environment", icon="terminal")
    hardware: HardwareSettings = section(HardwareSettings, "Hardware acceleration", icon="cpu")
    updates: UpdateSettings = section(UpdateSettings, "Updates", icon="refresh")


# ---------------------------------------------------------------------------------------------------
# Credentials (Configs/credentials.secret)
# ---------------------------------------------------------------------------------------------------
class TpdbCredentials(Section):
    api_token: str = setting("", "API token", secret=True)
    scenes_api_url: str = setting("https://api.theporndb.net/scenes", "Scenes API URL")
    jav_api_url: str = setting("https://api.theporndb.net/jav", "JAV API URL")
    movies_api_url: str = setting("https://api.theporndb.net/movies", "Movies API URL")
    sites_api_url: str = setting("https://api.theporndb.net/sites/", "Sites API URL")
    performers_api_url: str = setting("https://api.theporndb.net/performers/", "Performers API URL")
    collection_api_url: str = setting("https://api.theporndb.net/user/collection", "Collection API URL")


class ImageHostCredentials(Section):
    imgbox_username: str = setting("", "imgbox username")
    imgbox_password: str = setting("", "imgbox password", secret=True)
    imgbb_username: str = setting("", "ImgBB username")
    imgbb_password: str = setting("", "ImgBB password", secret=True)
    imgbb_album_id: str = setting("", "ImgBB album id")
    hamster_site_url: str = setting("https://hamsterimg.net", "Hamster site URL")
    hamster_api_key: str = setting("", "Hamster API key", secret=True)
    hamster_album_id: str = setting("", "Hamster album id")


class TrackerAccount(BaseModel):
    username: str = ""
    password: str = ""
    announce_url: str = ""


class NotifierCredentials(Section):
    telegram_bot_token: str = setting("", "Telegram bot token", secret=True)
    telegram_chat_id: str = setting("", "Telegram chat id")


class GithubCredentials(Section):
    token: str = setting("", "GitHub token", "Read-only token for private resource repos.", secret=True)


class Credentials(BaseModel):
    """Configs/credentials.secret"""
    model_config = ConfigDict(extra="ignore")
    tpdb: TpdbCredentials = section(TpdbCredentials, "TPDB", icon="key")
    image_hosts: ImageHostCredentials = section(ImageHostCredentials, "Image hosts", icon="key")
    trackers: dict[str, TrackerAccount] = Field(default_factory=lambda: {"hf": TrackerAccount(), "emp": TrackerAccount()},
                                                json_schema_extra={"label": "Tracker accounts", "secret_values": True})
    notifiers: NotifierCredentials = section(NotifierCredentials, "Notifiers", icon="key")
    github: GithubCredentials = section(GithubCredentials, "GitHub", icon="key")


# ---------------------------------------------------------------------------------------------------
# Job bundle: everything snapshotted when a job is created
# ---------------------------------------------------------------------------------------------------
class JobConfig(BaseModel):
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    thumbnails: ThumbnailSettings = Field(default_factory=ThumbnailSettings)
    video_preview: VideoPreviewSettings = Field(default_factory=VideoPreviewSettings)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
