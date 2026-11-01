"""
Migration of pre-14.0 configuration files to the new structure.

Old file                         -> new file
Configs/Config.json              -> Configs/processing.json
Configs/Config_Thumbnails.json   -> Configs/thumbnails.json
Configs/Config_Video_Preview.json-> Configs/video_preview.json
Configs/Config_Tracker.json      -> Configs/tracker.json
creds.secret (project root)      -> Configs/credentials.secret

The full key mapping is documented in docs/wiki/configuration.md. Old files are never modified or deleted.
"""

from typing import Any

from fileprep.config.models import (Credentials, ProcessingSettings, ThumbnailSettings, TrackerConfig,
                                    VideoPreviewSettings, TITLE_DATE_FORMATS)

LEGACY_FILES = {
    "processing": "Configs/Config.json",
    "thumbnails": "Configs/Config_Thumbnails.json",
    "video_preview": "Configs/Config_Video_Preview.json",
    "tracker": "Configs/Config_Tracker.json",
    "credentials": "creds.secret",
}

_DATE_CODES = dict(zip("12345678", TITLE_DATE_FORMATS.keys()))
_HOST_ORDER = ("imgbb", "imgbox", "hamster", "pixhost")  # legacy template URL priority


def _pick(old: dict, mapping: dict[str, str]) -> dict:
    """Copy ``old[legacy_key]`` to ``new_key`` for every key present."""
    return {new: old[legacy] for legacy, new in mapping.items() if legacy in old}


def migrate_processing(old: dict, legacy_credentials: dict | None = None) -> ProcessingSettings:
    def hosts(asset: str) -> list[str]:
        return [h for h in _HOST_ORDER if old.get(f"{h}_upload_{asset}")]

    api_mode = "jav" if old.get("jav_only_api_mode") else "movies" if old.get("movies_only_api_mode") else "scenes"
    data: dict[str, Any] = {
        "input": {"working_directory": old.get("working_path", ""),
                  **_pick(old, {"ignore_list": "ignored_filenames", "bad_words": "filename_strip_words"})},
        "matching": {"api_mode": api_mode,
                     **_pick(old, {"movies_scenes_mode": "movie_entry_type", "scene_matching_mode": "matching_mode",
                                   "tpdb_try_match_oshash_before_parse": "match_by_oshash_first",
                                   "force_re_match_using_existing_tpdb_uuid": "rematch_using_embedded_uuid",
                                   "manual_mode": "manual_performer_entry", "add_collection": "add_to_collection",
                                   "tpdb_scenes_url": "scenes_url", "tpdb_jav_url": "jav_url",
                                   "tpdb_movies_url": "movies_url", "tpdb_performer_url": "performers_url"})},
        "review": _pick(old, {"performers_approval": "approve_performers", "date_approval": "approve_date",
                              "title_approval": "approve_title", "warn_length_match": "warn_on_duration_mismatch",
                              "add_timestamps_markers": "add_chapter_markers"}),
        "naming": {**_pick(old, {"filename_mode": "filename_mode", "title_ignore_strings": "title_strip_strings",
                                 "filename_ignore_part_x": "ignore_part_number", "filename_ignore_res": "ignore_resolution_tag",
                                 "filename_ignore_performer_ID": "ignore_performer_id",
                                 "manual_mode_ask_suffix": "ask_manual_suffix", "create_sub_folder": "create_sub_folder"}),
                   "title_date_format": _DATE_CODES.get(str(old.get("title_date_mode", "1")), "YYYY-Month-DD")},
        "encoding": _pick(old, {"re_encode_hevc": "re_encode", "re_encode_hevc_CRF": "crf", "warn_crf_match": "warn_on_crf_match",
                                "re_encode_downscale": "downscale_to_1080p", "limit_cpu_usage": "limit_cpu_usage",
                                "remove_existing_chapters": "remove_existing_chapters", "keep_original_file": "keep_original_file",
                                "keep_only_one_audio_track": "keep_single_audio_track"}),
        "metadata": _pick(old, {"set_exclusive": "set_exclusive", "exclusive_string": "exclusive_tag"}),
        "outputs": _pick(old, {"create_cover_image": "cover_image", "cover_regeneration_mode": "cover_regeneration_mode",
                               "create_thumbnails": "thumbnails_sheet", "create_video_preview": "video_preview",
                               "create_mediainfo": "mediainfo_file", "create_face_portrait_pic": "performer_portraits",
                               "create_template_file": "upload_template", "image_output_format": "image_format",
                               "template_name": "template_file", "font_full_name": "font_file"}),
        "portraits": _pick(old, {"target_size_width": "width", "target_size_height": "height", "zoom_factor": "zoom_factor",
                                 "face_score_threshold": "face_score_threshold", "blur_kernel_size": "blur_kernel_size",
                                 "posters_limit": "posters_limit", "performer_image_output_format": "image_format"}),
        "image_hosts": {"mode": "all",  # legacy behaviour: upload to every enabled host
                        "cover_hosts": hosts("cover"), "thumbnails_hosts": hosts("thumbnails"),
                        "preview_hosts": [h for h in hosts("previews") if h != "imgbox"],
                        **_pick(old, {"imgbb_upload_headless_mode": "imgbb_headless"})},
        "tracker": _pick(old, {"upload_to_tracker": "enabled", "tracker_mode": "trackers", "upload_mode": "upload_mode",
                               "remove_extra_files_after_tracker_upload": "remove_extra_files_after_upload"}),
        "notifications": {"enabled": bool(old.get("use_notifier", False)), "notifier": "telegram"},
    }
    if legacy_credentials and legacy_credentials.get("hamster_site_url"):
        data["outputs"]["template_icon_base_url"] = legacy_credentials["hamster_site_url"]
    for key in ("image_format", "cover_regeneration_mode"):
        if isinstance(data["outputs"].get(key), str):
            data["outputs"][key] = data["outputs"][key].lower() if key == "image_format" else data["outputs"][key]
    if isinstance(data["portraits"].get("image_format"), str):
        data["portraits"]["image_format"] = data["portraits"]["image_format"].lower()
    return ProcessingSettings.model_validate(data)


def migrate_thumbnails(old: dict) -> ThumbnailSettings:
    return ThumbnailSettings.model_validate(_pick(old, {
        "num_thumbs": "thumbnail_count", "thumb_width": "thumbnail_width", "columns": "columns", "padding": "padding",
        "output_file_name_suffix": "output_suffix", "add_file_info": "include_file_info", "font_full_name": "font_file",
        "fit_thumbs_in_less_rows": "compact_vertical_layout", "regeneration_mode": "regeneration_mode",
        "alternate_layout": "alternate_layout", "use_first_frame": "include_first_frame"}))


def migrate_video_preview(old: dict) -> VideoPreviewSettings:
    data = _pick(old, {
        "CREATE_WEBP_PREVIEW": "create_webp_preview", "CREATE_WEBM_PREVIEW": "create_webm_preview",
        "CREATE_GIF_PREVIEW": "create_gif_preview", "CREATE_WEBP_PREVIEW_SHEET": "create_webp_sheet",
        "CREATE_WEBM_PREVIEW_SHEET": "create_webm_sheet", "CREATE_GIF_PREVIEW_SHEET": "create_gif_sheet",
        "KEEP_TEMP_FILES": "keep_temp_files", "ADD_BLACK_BARS": "add_black_bars", "WEBP_PREVIEW_FPS": "webp_fps",
        "GIF_PREVIEW_FPS": "gif_fps", "NUMBER_OF_SEGMENTS_GIF": "gif_segment_count", "TIMESTAMPS_MODE": "timestamps_mode",
        "GRID_WIDTH": "grid_columns", "NUM_OF_SEGMENTS": "segment_count", "SEGMENT_DURATION": "segment_duration",
        "OVERWRITE_EXISTING": "overwrite_existing", "PRINT_CUT_POINTS": "log_cut_points",
        "BLACKLISTED_CUT_POINTS": "blacklisted_cut_points", "EXCLUDED_FILES": "excluded_files",
        "CUSTOM_OUTPUT_PATH": "custom_output_directory", "CONFIRM_CUT_POINTS_REQUIRED": "confirm_cut_points",
        "LAST_CUT_POINT": "last_cut_point", "add_file_info": "include_file_info", "FONT_NAME_FULL": "font_file",
        "fit_thumbs_in_less_rows": "compact_vertical_layout", "transition_mode": "transition",
        "transition_duration": "transition_duration", "preview_quality_resolution": "resolution",
        "scene_threshold": "scene_change_threshold"})
    if isinstance(data.get("transition"), str):
        data["transition"] = data["transition"].lower()
    return VideoPreviewSettings.model_validate(data)


def migrate_tracker(old: dict) -> TrackerConfig:
    urls = old.get("trackers_urls", {})
    sites = {}
    for name, category in (("hf", "Pron"), ("emp", "Natural Tits")):
        sites[name] = {"login_url": urls.get(f"{name}_tracker_login_url", ""),
                       "upload_url": urls.get(f"{name}_tracker_upload_url", ""), "category": category}
    login = old.get("login_selectors", {})
    fields = {f.get("value"): {"by": f.get("by", "id"), "value": f.get("value", "")} for f in old.get("form_fields", [])}
    form = {"login_username": login.get("username"), "login_password": login.get("password"),
            "login_submit": login.get("submit"), "login_success": old.get("login_success"),
            "category_field": fields.get("category"), "title_field": fields.get("title"), "tags_field": fields.get("taginput"),
            "image_field": fields.get("image"), "description_field": fields.get("desc"),
            "torrent_field": old.get("torrent_field"), "submit_button": old.get("form_submit")}
    form = {k: v for k, v in form.items() if v}
    for site in sites.values():
        site["form"] = form
        site["two_factor_auth"] = bool(old.get("2FA", False))
    return TrackerConfig.model_validate({
        "browser": {"browser": old.get("browser", "firefox"), "firefox_driver_path": old.get("firefox_driver_path", ""),
                    "firefox_profile_path": old.get("firefox_profile_path", ""),
                    "chrome_driver_path": old.get("chromedriver_path", ""), "chrome_profile_path": old.get("chrome_profile_path", "")},
        "torrent": {"excluded_suffixes": old.get("list_suffixes_ignore")} if old.get("list_suffixes_ignore") else {},
        "sites": sites,
    })


def migrate_credentials(old: dict) -> Credentials:
    trackers_old = old.get("trackers", {})
    trackers = {}
    for name in ("hf", "emp"):
        trackers[name] = {"username": trackers_old.get(f"{name}_tracker_u", ""), "password": trackers_old.get(f"{name}_tracker_p", ""),
                          "announce_url": trackers_old.get(f"{name}_tracker_ann_url", "")}
    return Credentials.model_validate({
        "tpdb": {"api_token": old.get("api_auth", ""),
                 **_pick(old, {"api_scenes_url": "scenes_api_url", "api_jav_url": "jav_api_url", "api_movies_url": "movies_api_url",
                               "api_sites_url": "sites_api_url", "api_performer_url": "performers_api_url",
                               "api_collection_url": "collection_api_url"})},
        "image_hosts": _pick(old, {"imgbox_u": "imgbox_username", "imgbox_p": "imgbox_password", "imgbb_username": "imgbb_username",
                                   "imgbb_password": "imgbb_password", "imgbb_album_id": "imgbb_album_id",
                                   "hamster_site_url": "hamster_site_url", "hamster_api_key": "hamster_api_key",
                                   "hamster_album_id": "hamster_album_id"}),
        "trackers": trackers,
        "notifiers": _pick(old, {"tg_bot_token": "telegram_bot_token", "tg_chat_id": "telegram_chat_id"}),
    })
