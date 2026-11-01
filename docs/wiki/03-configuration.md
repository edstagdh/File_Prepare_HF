# Configuration

All settings live in `Configs/` and are edited in **Settings** (admins); the **i** next to each setting explains it. Each job keeps a snapshot of the
processing, thumbnails, video preview and tracker settings; the job form shows the Thumbnails, Video preview
and Tracker tabs only when those features are enabled. Every save keeps a backup in `Configs/backups/`.

| File                 | Contents                                                                                                       |
|----------------------|----------------------------------------------------------------------------------------------------------------|
| `processing.json`    | Default job settings (input, matching, review, naming, encoding, outputs, image hosts, tracker, notifications) |
| `thumbnails.json`    | Thumbnail sheet layout                                                                                         |
| `video_preview.json` | Animated previews and preview sheets                                                                           |
| `tracker.json`       | Tracker browser, torrent options and the tracker sites (URLs, category, 2FA, form selectors per site)          |
| `app.json`           | Web server, authentication, job scheduling, resource sync, environment (ffmpeg paths)                          |
| `credentials.secret` | TPDB token, image host / tracker accounts, Telegram, GitHub token (keep private)                               |

## Video encoding (per job, Encoding tab)

| Setting                             | Meaning                                                                                                   |
|-------------------------------------|-----------------------------------------------------------------------------------------------------------|
| encoding.re_encode                  | Re-encode videos that are not already HEVC/AV1                                                            |
| encoding.codec                      | `hevc` or `av1`                                                                                           |
| encoding.device                     | `cpu` (libx265 / SVT-AV1, libaom-av1 fallback) or `gpu` (NVENC / Quick Sync / AMF / VAAPI / VideoToolbox) |
| encoding.crf / av1_crf / av1_preset | CPU quality: HEVC CRF 0-29, AV1 CRF 0-63 and SVT-AV1 preset 0-13                                          |
| encoding.gpu_quality / gpu_preset   | GPU constant quality (CQ/QP 1-51) and speed preset                                                        |
| encoding.fallback_to_cpu            | Use the CPU when no working GPU encoder exists or the GPU encode fails                                    |

Files that are already HEVC or AV1 are not re-encoded (whatever the target codec).

## Image formats (AVIF)

`outputs.image_format` and `portraits.image_format` accept `webp`, `avif`, `jpg`, `jpeg`, `png`, `bmp`.
`video_preview.create_avif_preview` / `create_avif_sheet` add animated AVIF versions of the preview and sheet
(`avif_fps`, `avif_crf` 10-63, lower = better). AVIF needs Pillow 11.3+ (images) and libsvtav1 or libaom-av1 in
ffmpeg (previews); only Pixhost accepts AVIF uploads. `tracker.torrent.excluded_suffixes` lists saved before 14.8
get `_preview.avif` / `_preview_sheet.avif` added automatically.

## Updates (Settings > Application)

`updates.channel` (stable = `master`, dev = `dev`, off), `updates.check_on_start`, `updates.repository`
(`owner/name`), `updates.stable_branch`, `updates.dev_branch` - see [Tools](05-tools-and-modules.md).

## Hardware acceleration (machine, Settings > Application)

`hardware.gpu` (auto / nvidia / intel / amd / apple / vaapi / off) selects the GPU backend; `decode_thumbnails`,
`decode_previews` and `encode_preview_segments` choose where it is used; `cpu_fallback` retries a failed GPU
thumbnail/preview command on the CPU; `vaapi_device` is the Linux render node.

## Renamed keys (13.x -> 14.0)

### processing.json (was Config.json)

| Old key                                                                     | New key                                                                                                     |
|-----------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| working_path                                                                | input.working_directory                                                                                     |
| ignore_list                                                                 | input.ignored_filenames                                                                                     |
| bad_words                                                                   | input.filename_strip_words                                                                                  |
| scene_matching_mode                                                         | matching.matching_mode                                                                                      |
| jav_only_api_mode / movies_only_api_mode                                    | matching.api_mode (scenes / jav / movies)                                                                   |
| movies_scenes_mode                                                          | matching.movie_entry_type                                                                                   |
| tpdb_try_match_oshash_before_parse                                          | matching.match_by_oshash_first                                                                              |
| force_re_match_using_existing_tpdb_uuid                                     | matching.rematch_using_embedded_uuid                                                                        |
| manual_mode                                                                 | matching.manual_performer_entry                                                                             |
| add_collection                                                              | matching.add_to_collection                                                                                  |
| tpdb_scenes_url / tpdb_jav_url / tpdb_movies_url / tpdb_performer_url       | matching.scenes_url / jav_url / movies_url / performers_url                                                 |
| performers_approval / date_approval / title_approval                        | review.approve_performers / approve_date / approve_title                                                    |
| warn_length_match                                                           | review.warn_on_duration_mismatch                                                                            |
| add_timestamps_markers                                                      | review.add_chapter_markers                                                                                  |
| filename_mode                                                               | naming.filename_mode                                                                                        |
| title_date_mode ("1"-"8")                                                   | naming.title_date_format ("YYYY-Month-DD" ...)                                                              |
| title_ignore_strings                                                        | naming.title_strip_strings                                                                                  |
| filename_ignore_part_x / filename_ignore_res / filename_ignore_performer_ID | naming.ignore_part_number / ignore_resolution_tag / ignore_performer_id                                     |
| manual_mode_ask_suffix                                                      | naming.ask_manual_suffix                                                                                    |
| create_sub_folder                                                           | naming.create_sub_folder                                                                                    |
| (new)                                                                       | naming.extra_suffix (suffix for all files) / naming.filename_overrides (custom names, set from the preview) |
| (hard coded 200 / 240)                                                      | naming.max_filename_length / max_full_path_length                                                           |
| re_encode_hevc                                                              | encoding.re_encode (14.0-14.2 `encoding.re_encode_hevc` is read automatically)                              |
| re_encode_hevc_CRF                                                          | encoding.crf (HEVC on CPU)                                                                                  |
| warn_crf_match                                                              | encoding.warn_on_crf_match                                                                                  |
| re_encode_downscale                                                         | encoding.downscale_to_1080p                                                                                 |
| limit_cpu_usage (+ hard coded 70%)                                          | encoding.limit_cpu_usage / encoding.cpu_usage_percent                                                       |
| remove_existing_chapters / keep_original_file                               | encoding.remove_existing_chapters / keep_original_file                                                      |
| keep_only_one_audio_track                                                   | encoding.keep_single_audio_track                                                                            |
| set_exclusive / exclusive_string                                            | metadata.set_exclusive / exclusive_tag                                                                      |
| create_cover_image / create_thumbnails / create_video_preview               | outputs.cover_image / thumbnails_sheet / video_preview                                                      |
| create_mediainfo / create_face_portrait_pic / create_template_file          | outputs.mediainfo_file / performer_portraits / upload_template                                              |
| image_output_format / cover_regeneration_mode                               | outputs.image_format / cover_regeneration_mode                                                              |
| template_name / font_full_name                                              | outputs.template_file / font_file                                                                           |
| hamster_site_url (credentials, icon prefix)                                 | outputs.template_icon_base_url                                                                              |
| target_size_width / target_size_height                                      | portraits.width / height                                                                                    |
| zoom_factor / face_score_threshold / blur_kernel_size / posters_limit       | portraits.* (same names)                                                                                    |
| performer_image_output_format                                               | portraits.image_format                                                                                      |
| {host}_upload_cover / _thumbnails / _previews                               | image_hosts.cover_hosts / thumbnails_hosts / preview_hosts (ordered lists)                                  |
| imgbb_upload_headless_mode                                                  | image_hosts.imgbb_headless                                                                                  |
| upload_to_tracker / tracker_mode / upload_mode                              | tracker.enabled / trackers / upload_mode                                                                    |
| remove_extra_files_after_tracker_upload                                     | tracker.remove_extra_files_after_upload                                                                     |
| use_notifier / notifier_name                                                | notifications.enabled / notifier                                                                            |
| python_min/max_version_supported                                            | app.json environment.python_min_version / python_max_version (not migrated; default 3.10 - 3.14)            |
| custom_name_format_mode, *_available lists                                  | removed (unused / now part of the field choices)                                                            |

### thumbnails.json (was Config_Thumbnails.json)

num_thumbs -> thumbnail_count, thumb_width -> thumbnail_width, output_file_name_suffix -> output_suffix,
add_file_info -> include_file_info, font_full_name -> font_file, fit_thumbs_in_less_rows -> compact_vertical_layout,
use_first_frame -> include_first_frame; columns, padding, alternate_layout, regeneration_mode unchanged.

### video_preview.json (was Config_Video_Preview.json)

CREATE_WEBP_PREVIEW / WEBM / GIF -> create_webp_preview / create_webm_preview / create_gif_preview,
CREATE_*_PREVIEW_SHEET -> create_webp_sheet / create_webm_sheet / create_gif_sheet, WEBP_PREVIEW_FPS -> webp_fps,
GIF_PREVIEW_FPS -> gif_fps, NUMBER_OF_SEGMENTS_GIF -> gif_segment_count, NUM_OF_SEGMENTS -> segment_count,
SEGMENT_DURATION -> segment_duration, GRID_WIDTH -> grid_columns, TIMESTAMPS_MODE -> timestamps_mode
(1 preview+sheet, 2 sheet only, 3 none), OVERWRITE_EXISTING -> overwrite_existing, PRINT_CUT_POINTS -> log_cut_points,
BLACKLISTED_CUT_POINTS -> blacklisted_cut_points, EXCLUDED_FILES -> excluded_files, CUSTOM_OUTPUT_PATH ->
custom_output_directory, CONFIRM_CUT_POINTS_REQUIRED -> confirm_cut_points, LAST_CUT_POINT -> last_cut_point,
KEEP_TEMP_FILES -> keep_temp_files, ADD_BLACK_BARS -> add_black_bars, add_file_info -> include_file_info,
FONT_NAME_FULL -> font_file, fit_thumbs_in_less_rows -> compact_vertical_layout, transition_mode -> transition,
preview_quality_resolution -> resolution, scene_threshold -> scene_change_threshold.

### tracker.json (was Config_Tracker.json)

browser, firefox_driver_path, firefox_profile_path, chromedriver_path (-> chrome_driver_path), chrome_profile_path and
2FA (-> two_factor_auth) moved to `browser`; list_suffixes_ignore -> torrent.excluded_suffixes; trackers_urls ->
`sites.<tracker>.login_url / upload_url` with a configurable `category` (was hard coded); login_selectors,
login_success, form_fields, torrent_field, form_submit -> `form.*` selectors. Driver paths may now be empty
(Selenium Manager finds the driver).

### credentials.secret (was creds.secret)

api_auth -> tpdb.api_token, api_*_url -> tpdb.*_api_url, imgbox_u/_p -> image_hosts.imgbox_username/password,
imgbb_* and hamster_* -> image_hosts.*, trackers.{t}_tracker_u/_p/_ann_url -> trackers.{t}.username/password/announce_url,
tg_bot_token / tg_chat_id -> notifiers.telegram_bot_token / telegram_chat_id, new github.token.
