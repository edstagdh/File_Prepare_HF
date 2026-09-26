"""
Top-level orchestration: given a parsed filename query string (or an
existing UUID/oshash), find the matching scene on the API, walk the user
through any required approvals, and return the metadata fields used to
build the final filename/template.
"""

import time
from datetime import datetime

from loguru import logger

from Utilities import load_credentials, remove_ignored_strings, calculate_oshash

from .api_client import send_request
from .performers import extract_female_performers, manual_performer_entry
from .scene_review import approve_scene_date, approve_scene_title, maybe_add_scene_number, review_duration_and_markers
from .scene_selection import filter_entries_by_date, select_entry
from .site_naming import normalize_site_name, resolve_site_owner
from .text_utils import convert_number_suffix_to_word, extract_scene_tags, remove_date_from_text

EMPTY_RESULT = (None,) * 14


async def query_api(
        query_string,
        scene_date,
        manual_mode,
        part_match,
        generate_hf_template,
        jav_api_mode,
        movies_api_mode,
        movies_scenes_mode,
        date_approval,
        title_approval,
        performers_approval,
        filename_ignore_performer_ID,
        send_notification,
        existing_tpdb_uuid,
        file,
        title_ignore_strings,
        warn_length_match,
        add_timestamps_markers,
        tpdb_try_match_oshash_before_parse,
        mode,
):
    """
    Resolve a file to a single scene match on the API and return its
    metadata, running the user through whichever approval steps are
    enabled along the way.

    Match resolution, in order of precedence:
    1. ``existing_tpdb_uuid`` - direct id lookup (skips date filtering).
    2. ``tpdb_try_match_oshash_before_parse`` - oshash lookup, falling back
       to a parse-based search (with the same fallbacks as below) if the
       hash doesn't match.
    3. Otherwise, a parse-based search using ``query_string``, with two
       fallback rewrites tried in mode 2: a "part.N" -> "part.word"
       conversion, then embedded-date removal.

    Once a single scene entry is chosen, this optionally reviews/fixes
    duration-mismatched markers, offers to append a scene number to the
    title (movies mode only), and runs date/title approval prompts before
    extracting performers and cleaning the title.

    Args:
        query_string: Free-text string to parse-search the API with.
        scene_date: Target date ("YYYY-MM-DD") used for date filtering
            (``mode == 2``) and as the fallback returned date.
        manual_mode: If True (and no other performer-extraction option
            applies), skip automatic extraction and prompt for performer
            names one at a time.
        part_match: If True, allow the ".part.N" -> ".part.word" fallback
            rewrite when the initial parse search finds nothing.
        generate_hf_template: If True, extract and return normalized scene
            tags.
        jav_api_mode: Selects the JAV credential/collection profile.
        movies_api_mode: Selects the Movies credential/collection profile.
        movies_scenes_mode: When ``movies_api_mode`` is True, "scene"
            enables the scene-number prompt and mode-2 performer selection.
        date_approval: If True, prompt the user to approve/replace the
            scene date.
        title_approval: If True, prompt the user to approve/replace the
            scene title.
        performers_approval: If True, use the full interactive performer
            edit loop (extraction mode 3) instead of a plain extraction.
        filename_ignore_performer_ID: Forwarded to
            ``extract_female_performers`` - strips "ID <digits>" tokens
            from filename-safe performer names.
        send_notification: Optional async push-notification callback used
            by every interactive prompt in this flow.
        existing_tpdb_uuid: If provided, look the scene up directly by id
            instead of searching.
        file: Local media file path/handle, used for oshash calculation and
            duration checks.
        title_ignore_strings: Strings stripped from the title when cleaning
            it / previewing edits.
        warn_length_match: If True, warn when the file's duration differs
            substantially from the scene's reported duration.
        add_timestamps_markers: If True, interactively fix/remove markers
            that fall outside the file's actual duration.
        tpdb_try_match_oshash_before_parse: If True, try an oshash lookup
            before falling back to parse-based search.
        mode: 1 for a plain parse search, 2 to additionally try the
            part-suffix and date-stripped fallback rewrites. Any other
            value (when not using the uuid/oshash paths) returns
            ``EMPTY_RESULT``.

    Returns:
        A 14-tuple: ``(clean_title, female_performers, image_url, slug,
        url, tpdb_image_url, site, site_owner, scene_description,
        scene_date, scene_tags, tpdb_uuid, _id, markers)``. Every field is
        None (``EMPTY_RESULT``) if no match could be resolved or an
        unexpected error occurred. ``female_performers`` is None if no
        performers were found, or the string "Invalid" if extraction
        turned up an unresolved "Unknown" performer.
    """
    max_retries = 3
    delay = 5

    try:
        if jav_api_mode:
            work_mode = 4
        elif movies_api_mode:
            work_mode = 9
        else:
            work_mode = 1
        api_auth, api_mode_url, api_sites_url = await load_credentials(mode=work_mode)

        if not api_mode_url or not api_auth:
            logger.error("API URL or auth token missing. Aborting API request.")
            return EMPTY_RESULT

        response_data, mode, need_chapters_data = await _resolve_initial_match(
            existing_tpdb_uuid,
            tpdb_try_match_oshash_before_parse,
            file,
            query_string,
            mode,
            part_match,
            api_mode_url,
            api_auth,
            max_retries,
            delay,
        )
        if response_data is None or not response_data.get("data"):
            return EMPTY_RESULT

        if mode in (1, 2):
            valid_entries = await filter_entries_by_date(response_data, scene_date, api_mode_url, send_notification, mode)
        else:
            item = response_data.get("data")
            if not item:
                return EMPTY_RESULT
            valid_entries = item if isinstance(item, list) else [item]

        if not valid_entries:
            logger.error(f"No matching entries for the provided date for string: {query_string}")
            return EMPTY_RESULT

        selected_entry = await _select_from_candidates(valid_entries, file, send_notification)
        if selected_entry is None:
            logger.error("No matching entries selected by user.")
            return EMPTY_RESULT

        tpdb_uuid = selected_entry.get("id")
        if need_chapters_data:
            scene_response_data = await send_request(api_mode_url, api_auth, tpdb_uuid, max_retries, delay, mode="id")
            if scene_response_data is None:
                logger.error("Failed to retrieve scene specific data.")
                return EMPTY_RESULT
        else:
            scene_response_data = response_data

        scene_data = scene_response_data.get("data")
        if isinstance(scene_data, list):
            scene_data = scene_data[0] if scene_data else None
        if not scene_data:
            return EMPTY_RESULT

        markers = scene_data.get("markers") or None
        _id = scene_data.get("_id")

        if warn_length_match or add_timestamps_markers:
            markers = await review_duration_and_markers(
                file, scene_data, markers, warn_length_match, add_timestamps_markers, send_notification
            )

        title = scene_data.get("title")

        if movies_api_mode and movies_scenes_mode == "scene":
            title = await maybe_add_scene_number(title, title_ignore_strings, send_notification, remove_ignored_strings)

        scene_date = scene_data.get("date")
        if date_approval:
            scene_date = await approve_scene_date(scene_date, send_notification)
        if title_approval:
            title = await approve_scene_title(title, title_ignore_strings, send_notification, remove_ignored_strings)

        clean_title = await remove_ignored_strings(title, title_ignore_strings)

        image_url = scene_data.get("image")
        tpdb_image_url = scene_data.get("background", {}).get("full")
        scene_description = scene_data.get("description")
        slug = scene_data.get("slug")
        url = scene_data.get("url")
        tpdb_uuid = scene_data.get("id")

        scene_tags = await extract_scene_tags(scene_data) if generate_hf_template else None

        site, site_parent = normalize_site_name(scene_data)
        site_owner = await resolve_site_owner(site, site_parent, api_sites_url, api_auth, max_retries, delay)

        female_performers = await _resolve_performers(
            scene_data,
            api_mode_url,
            filename_ignore_performer_ID,
            send_notification,
            manual_mode,
            movies_api_mode,
            movies_scenes_mode,
            performers_approval,
        )

        if not female_performers:
            return clean_title, None, image_url, slug, url, tpdb_image_url, site, site_owner, scene_description, scene_date, scene_tags, tpdb_uuid, _id, markers
        if "Unknown" in female_performers:
            return clean_title, "Invalid", image_url, slug, url, tpdb_image_url, site, site_owner, scene_description, scene_date, scene_tags, tpdb_uuid, _id, markers

        return clean_title, female_performers, image_url, slug, url, tpdb_image_url, site, site_owner, scene_description, scene_date, scene_tags, tpdb_uuid, _id, markers

    except Exception as e:
        logger.exception(f"An unexpected error occurred in get_data_from_api: {str(e)}")
        return EMPTY_RESULT


async def _resolve_initial_match(
        existing_tpdb_uuid,
        tpdb_try_match_oshash_before_parse,
        file,
        query_string,
        mode,
        part_match,
        api_mode_url,
        api_auth,
        max_retries,
        delay,
):
    """
    Run whichever initial-match strategy applies (direct id, oshash, or
    parse search with fallbacks) and return the raw response plus the
    (possibly overridden) mode and whether full chapter data still needs
    to be fetched.

    Returns:
        A ``(response_data, mode, need_chapters_data)`` tuple.
        ``response_data`` is None if no strategy produced a usable result.
    """
    if existing_tpdb_uuid:
        logger.debug(f"Matching mode - Existing TPDB_UUID: {existing_tpdb_uuid}")
        response_data = await send_request(api_mode_url, api_auth, existing_tpdb_uuid, max_retries, delay, mode="id")
        return response_data, 0, False

    if tpdb_try_match_oshash_before_parse:
        file_oshash = await calculate_oshash(file)
        response_data = await send_request(api_mode_url, api_auth, file_oshash, max_retries, delay, mode="oshash")

        if response_data is not None and response_data.get("data"):
            return response_data, 0, False  # oshash succeeded, skip filter_entries_by_date

        logger.warning("OSHASH match failed, falling back to parse mode.")
        response_data = await _parse_search_with_fallbacks(query_string, mode, part_match, api_mode_url, api_auth, max_retries, delay)
        return response_data, mode, True

    response_data = await _parse_search_with_fallbacks(query_string, mode, part_match, api_mode_url, api_auth, max_retries, delay)
    return response_data, mode, True


async def _parse_search_with_fallbacks(query_string, mode, part_match, api_mode_url, api_auth, max_retries, delay):
    """
    Run a parse-based search, and for ``mode == 2``, try the part-suffix
    and date-stripped rewrites in turn if the previous attempt found
    nothing. For any other mode, returns None (matching the original
    "invalid mode" short-circuit).
    """
    if mode == 1:
        return await send_request(api_mode_url, api_auth, query_string, max_retries, delay, mode="parse")

    if mode != 2:
        return None

    response_data = await send_request(api_mode_url, api_auth, query_string, max_retries, delay, mode="parse")
    if response_data is not None and response_data.get("data"):
        return response_data

    query_string_fallback = await convert_number_suffix_to_word(query_string)
    if query_string_fallback != query_string and part_match:
        response_data = await send_request(api_mode_url, api_auth, query_string_fallback, max_retries, delay, mode="parse")
        if response_data is not None and response_data.get("data"):
            return response_data

    string_advanced_parse_fallback = await remove_date_from_text(query_string)
    return await send_request(api_mode_url, api_auth, string_advanced_parse_fallback, max_retries, delay, mode="parse")


async def _select_from_candidates(valid_entries, file, send_notification):
    """
    Pick a single entry out of ``valid_entries``, logging file info first
    when there's more than one candidate to choose from.
    """
    if len(valid_entries) <= 1:
        return valid_entries[0] if valid_entries else None

    from Media_Processing import get_video_duration, get_existing_title  # local import to avoid a dependency cycle

    duration, _ = await get_video_duration(file)
    existing_title = await get_existing_title(file)
    try:
        hours, remainder = divmod(int(duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        timestamp_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception as e:
        logger.error(f"Error formatting duration: {e}")
        timestamp_str = "N/A"

    file_info = f"Filename: {file.name} | Duration: {timestamp_str}"
    if existing_title:
        file_info += f" | {existing_title}"
    logger.debug(file_info)

    return await select_entry(valid_entries, send_notification)


async def _resolve_performers(
        scene_data,
        api_mode_url,
        filename_ignore_performer_ID,
        send_notification,
        manual_mode,
        movies_api_mode,
        movies_scenes_mode,
        performers_approval,
):
    """
    Pick the right performer-extraction strategy based on the current mode
    flags: mode-2 extraction for movie scenes, mode-3 (full edit) when
    approval is required, mode-1 for plain extraction, or fully manual
    entry as the last resort.
    """
    if movies_api_mode and movies_scenes_mode == "scene":
        return await extract_female_performers(scene_data, api_mode_url, filename_ignore_performer_ID, send_notification, mode=2)
    if performers_approval:
        return await extract_female_performers(scene_data, api_mode_url, filename_ignore_performer_ID, send_notification, mode=3)
    if not manual_mode:
        return await extract_female_performers(scene_data, api_mode_url, filename_ignore_performer_ID, send_notification, mode=1)
    return await manual_performer_entry(send_notification)
