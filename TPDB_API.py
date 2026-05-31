import asyncio
import json
import os
import re
import requests
from num2words import num2words
import time
from loguru import logger
from datetime import datetime
from typing import Optional
from Utilities import load_credentials, remove_ignored_strings, calculate_oshash


async def query_api(query_string, scene_date, manual_mode, part_match, generate_hf_template, jav_api_mode, movies_api_mode, movies_scenes_mode,
                    filename_ignore_performer_ID, send_notification, existing_tpdb_uuid, file, title_ignore_strings, warn_length_match, add_timestamps_markers,
                    tpdb_try_match_oshash_before_parse, mode):
    max_retries = 3
    delay = 5
    EMPTY_RESULT = (None,) * 14

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

        if existing_tpdb_uuid:
            logger.debug(f"Matching mode - Existing TPDB_UUID: {existing_tpdb_uuid}")
            response_data = await send_request(api_mode_url, api_auth, existing_tpdb_uuid, max_retries, delay, mode='id')
            mode = 0
            need_chapters_data = False
        elif tpdb_try_match_oshash_before_parse:
            file_oshash = await calculate_oshash(file)
            response_data = await send_request(api_mode_url, api_auth, file_oshash, max_retries, delay, mode='oshash')
            need_chapters_data = False

            if response_data is None or not response_data.get('data'):
                logger.warning("OSHASH match failed, falling back to parse mode.")
                need_chapters_data = True
                if mode == 1:
                    response_data = await send_request(api_mode_url, api_auth, query_string, max_retries, delay, mode='parse')
                elif mode == 2:
                    response_data = await send_request(api_mode_url, api_auth, query_string, max_retries, delay, mode='parse')
                    if response_data is None or not response_data.get('data'):
                        query_string_fallback = await convert_number_suffix_to_word(query_string)
                        if query_string_fallback != query_string and part_match:
                            response_data = await send_request(api_mode_url, api_auth, query_string_fallback, max_retries, delay, mode='parse')
                        elif response_data is None or not response_data.get('data'):
                            string_advanced_parse_fallback = await remove_date_from_text(query_string)
                            response_data = await send_request(api_mode_url, api_auth, string_advanced_parse_fallback, max_retries, delay, mode='parse')
                else:
                    return EMPTY_RESULT
            else:
                mode = 0  # oshash succeeded, skip filter_entries_by_date
        else:
            need_chapters_data = True
            if mode == 1:
                response_data = await send_request(api_mode_url, api_auth, query_string, max_retries, delay, mode='parse')
            elif mode == 2:
                response_data = await send_request(api_mode_url, api_auth, query_string, max_retries, delay, mode='parse')
                if response_data is None or not response_data.get('data'):
                    query_string_fallback = await convert_number_suffix_to_word(query_string)
                    if query_string_fallback != query_string and part_match:
                        response_data = await send_request(api_mode_url, api_auth, query_string_fallback, max_retries, delay, mode='parse')
                    elif response_data is None or not response_data.get('data'):
                        string_advanced_parse_fallback = await remove_date_from_text(query_string)
                        response_data = await send_request(api_mode_url, api_auth, string_advanced_parse_fallback, max_retries, delay, mode='parse')
            else:
                return EMPTY_RESULT

        if response_data is None or not response_data.get('data'):
            return EMPTY_RESULT
        if mode in [1,2]:
            valid_entries = await filter_entries_by_date(response_data, scene_date, api_mode_url, send_notification, mode)
        else:
            item = response_data.get("data")
            valid_entries = []
            if item:
                if isinstance(item, list):
                    valid_entries.extend(item)
                else:
                    valid_entries.append(item)
            else:
                return EMPTY_RESULT

        if not valid_entries:
            logger.error(f"No matching entries for the provided date for string: {query_string}")
            return EMPTY_RESULT

        if len(valid_entries) > 1:
            # Duration from file
            from Media_Processing import get_video_duration, get_existing_title
            duration, _ = await get_video_duration(file)
            existing_title = await get_existing_title(file)
            # Duration formatting
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

            selected_entry = await select_entry(valid_entries, send_notification)
        else:
            selected_entry = valid_entries[0]
        if selected_entry is None:
            logger.error("No matching entries selected by user.")
            return EMPTY_RESULT
        else:
            tpdb_uuid = selected_entry.get('id')
            if need_chapters_data:
                scene_response_data = await send_request(api_mode_url, api_auth, tpdb_uuid, max_retries, delay, mode='id')
                if scene_response_data is None:
                    logger.error("Failed to retrieve scene specific data.")
                    return EMPTY_RESULT
            else:
                scene_response_data = response_data
        # Safely extract fields from scene_data
        scene_data = scene_response_data.get('data')
        if isinstance(scene_data, list):
            scene_data = scene_data[0] if scene_data else None
        if not scene_data:
            return EMPTY_RESULT
        markers = scene_data.get("markers") or None

        _id = scene_data.get("_id")
        if warn_length_match or add_timestamps_markers:
            from Media_Processing import get_video_duration

            duration, _ = await get_video_duration(file)
            scene_duration = scene_data.get("duration")
            temp_duration = time.strftime('%H:%M:%S', time.gmtime(duration)) if duration is not None else "N/A"
            formatted_duration = f"{temp_duration} ({scene_duration})"

            if duration is not None and scene_duration is not None and formatted_duration != "N/A":
                delta = int(abs(duration - scene_duration))

                if delta > 20:
                    logger.warning(
                        "Duration mismatch detected: file={} | scene={} | delta={}s ({}) | file_path={}",
                        duration,
                        scene_duration,
                        delta,
                        formatted_duration,
                        file,
                    )

            if duration is not None and markers:

                fixed_markers = []

                for marker in markers:
                    start = marker.get("start_time")
                    end = marker.get("end_time")
                    temp_start = time.strftime('%H:%M:%S', time.gmtime(start)) if start is not None else "N/A"
                    formatted_start = f"{temp_start} ({start})"
                    temp_end = time.strftime('%H:%M:%S', time.gmtime(end)) if end is not None else "N/A"
                    formatted_end = f"{temp_end} ({end})"

                    invalid = False
                    if start is not None and start > duration:
                        invalid = True
                    if end is not None and end > duration:
                        invalid = True

                    if not invalid:
                        fixed_markers.append(marker)
                        continue

                    # Show numbers + timestamp
                    logger.warning(
                        "Invalid marker detected: id={} | start={} ({}) | end={} ({}) | video_duration={} ({}) | file={}",
                        marker.get("id"),
                        start,
                        formatted_start if start is not None else "N/A",
                        end,
                        formatted_end if end is not None else "N/A",
                        duration,
                        formatted_duration,
                        file
                    )

                    while True:
                        if send_notification:
                            result = await send_notification("User input required - Select Entry to Keep")
                            if not result:
                                logger.warning("Notifier failed to send user input request.")
                        await asyncio.sleep(0.5)
                        choice = input(
                            f"Marker '{marker.get('title')}' exceeds duration, would you like to (F)ix / (R)emove marker: \n"
                        ).strip().lower()

                        if choice == "f":
                            # Fix start_time
                            if start is not None and start > duration:
                                while True:
                                    logger.warning(
                                        "Marker '{}' start_time ({}) exceeds duration ({}).",
                                        marker.get("title"), formatted_start, formatted_duration
                                    )
                                    await asyncio.sleep(0.5)
                                    new_start_str = input("Enter corrected start_time in HH:MM:SS\n").strip()
                                    try:
                                        # Parse HH:MM:SS to seconds
                                        h, m, s = map(int, new_start_str.split(":"))
                                        new_start = h * 3600 + m * 60 + s
                                        if new_start > duration:
                                            logger.error("start_time still exceeds video duration.")
                                            continue
                                        marker["start_time"] = new_start
                                        break
                                    except (ValueError, TypeError):
                                        logger.error("Invalid timestamp. Format must be HH:MM:SS.")

                            # Fix end_time
                            if end is not None and end > duration:
                                while True:
                                    logger.warning(
                                        "Marker '{}' end_time ({}) exceeds duration ({}).",
                                        marker.get("title"), formatted_end, formatted_duration
                                    )
                                    await asyncio.sleep(0.5)
                                    new_end_str = input("Enter corrected end_time in HH:MM:SS\n").strip()
                                    try:
                                        # Parse HH:MM:SS to seconds
                                        h, m, s = map(int, new_end_str.split(":"))
                                        new_end = h * 3600 + m * 60 + s
                                        if new_end > duration:
                                            logger.error("end_time still exceeds video duration.")
                                            continue
                                        marker["end_time"] = new_end
                                        break
                                    except (ValueError, TypeError):
                                        logger.error("Invalid timestamp. Format must be HH:MM:SS.")

                            fixed_markers.append(marker)
                            break

                        elif choice == "r":
                            logger.info("Removed marker id={}", marker.get("id"))
                            break

                        else:
                            logger.error("Invalid choice. Use F/R/S.")

                markers = fixed_markers or None

        # "Clean" the title
        title = scene_data.get('title')

        # Check scene number addition to title
        if movies_api_mode and movies_scenes_mode == "scene":
            while True:
                if send_notification:
                    result = await send_notification("User input required - Add Scene Number")
                    if not result:
                        logger.warning("Notifier failed to send user input request.")
                await asyncio.sleep(0.5)

                logger.info(
                    f"This is the current title, would you like to add a scene number to it? e.g. ' - Scene 1'\n"
                    f"The following strings will be removed from the title afterwards: {title_ignore_strings}\n"
                    f"Current title: {title}\n"
                    f"Enter a scene number, or press Enter to skip:"
                )
                raw = input("> ").strip()

                # User chose to skip
                if raw == "":
                    logger.info("Skipping scene number addition.")
                    break

                # Preview the outcome
                preview_title = f"{title} {raw}"
                preview_clean = await remove_ignored_strings(preview_title, title_ignore_strings)

                logger.info(
                    f"\nPreview of resulting title:\n"
                    f"  {preview_clean}\n"
                    f"\n  [a] Approve  [r] Restart  [s] Skip (don't add scene number)"
                )

                if send_notification:
                    result = await send_notification("User input required - Confirm Scene Number")
                    if not result:
                        logger.warning("Notifier failed to send user input request.")
                await asyncio.sleep(0.5)

                confirm = input("> ").strip().lower()

                if confirm in ("a", "approve"):
                    title = preview_title
                    logger.info(f"Scene number accepted. Title set to: {preview_title}")
                    break
                elif confirm in ("s", "skip"):
                    logger.info("Skipping scene number addition.")
                    break
                elif confirm in ("r", "restart"):
                    logger.info("Restarting scene number input...")
                    continue
                else:
                    logger.warning("Unrecognised input, restarting scene number input...")
                    continue

        clean_title = await remove_ignored_strings(title, title_ignore_strings)

        image_url = scene_data.get('image')
        tpdb_image_url = scene_data.get("background", {}).get("full")
        scene_description = scene_data.get('description')
        scene_date = scene_data.get('date')
        slug = scene_data.get('slug')
        url = scene_data.get('url')
        tpdb_uuid = scene_data.get('id')

        if generate_hf_template:
            scene_tags = await extract_scene_tags(scene_data)
        else:
            scene_tags = None
        site = scene_data.get("site", {}).get("name")
        if "onlyfans" in site.lower() and "fansdb" in site.lower():
            site = site.replace("FansDB: ", "")
            site = site.replace(" (onlyfans)", "")
            site = "OnlyFans-" + site
        if "manyvids" in site.lower() and "fansdb" in site.lower():
            site = site.replace("FansDB: ", "")
            site = site.replace(" (manyvids)", "")
            site = "Manyvids-" + site
        if "fansly" in site.lower() and "fansdb" in site.lower():
            site = site.replace("FansDB: ", "")
            site = site.replace(" (fansly)", "")
            site = "Fansly-" + site

        site_parent = scene_data.get("site", {}).get("parent")
        if site_parent and site_parent.get("name", None) == 'ManyVids' and not "Manyvids:" in site:
            site = "Manyvids: " + site

        if site_parent:
            site_parent_uuid = site_parent.get("uuid")
            site_owner = await fetch_api_site_data(api_sites_url, api_auth, site_parent_uuid, max_retries, delay)
        else:
            site_owner = site
        if any(x in site.lower() for x in ["onlyfans", "manyvids", "fansly"]) and site_owner and site_owner.lower() in site.lower():
            site_owner = None
        if movies_api_mode and movies_scenes_mode == "scene":
            female_performers = await extract_female_performers(scene_data, api_mode_url, filename_ignore_performer_ID, send_notification, mode=2)
        elif not manual_mode:
            female_performers = await extract_female_performers(scene_data, api_mode_url, filename_ignore_performer_ID, send_notification, mode=1)
        else:
            await asyncio.sleep(0.5)
            female_performers = []
            while True:
                logger.info("Enter Performers Manually")
                if send_notification:
                    result = await send_notification("User input required - Manual Performer Entry")
                    if not result:
                        logger.warning("Notifier failed to send user input request.")
                await asyncio.sleep(0.5)
                user_input = input("Enter a value (or type 'exit' to stop): ")
                if user_input.lower() == 'exit':
                    break
                female_performers.append((user_input, ""))
        if not female_performers:
            return clean_title, None, image_url, slug, url, tpdb_image_url, site, site_owner, scene_description, scene_date, scene_tags, tpdb_uuid, _id, markers
        elif "Unknown" in female_performers:
            return clean_title, "Invalid", image_url, slug, url, tpdb_image_url, site, site_owner, scene_description, scene_date, scene_tags, tpdb_uuid, _id, markers

        # logger.debug(f"matched result: {tpdb_uuid} - {site} - {scene_date} - {clean_title} - {female_performers}")
        return clean_title, female_performers, image_url, slug, url, tpdb_image_url, site, site_owner, scene_description, scene_date, scene_tags, tpdb_uuid, _id, markers

    except Exception as e:
        logger.exception(f"An unexpected error occurred in get_data_from_api: {str(e)}")
        return EMPTY_RESULT


async def send_request(api_url, api_auth, query_string, max_retries, delay, mode="parse"):
    if "performers" in api_url:
        url = f"{api_url}{query_string}"
    else:
        if mode == "id":
            url = f"{api_url}/{query_string}"
        elif mode == "oshash":
            url = f"{api_url}/hash/{query_string}?type=OSHASH"
        else:
            url = f"{api_url}?orderBy=recently_released&parse={query_string}&per_page=40&page=1"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_auth}"
    }

    # logger.debug(f"Sending request to API: {url}")

    while True:  # allows user-triggered retry cycles
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=headers)

                # Retry only on 5xx
                if 500 <= response.status_code < 600:
                    raise requests.HTTPError(
                        f"Server error {response.status_code}",
                        response=response
                    )

                response.raise_for_status()
                response_data = response.json()

                if attempt > 1:
                    logger.info("Retry successful!")

                if "data" not in response_data:
                    return None

                # oshash returns a single object, normalize to a list
                if mode == "oshash" and isinstance(response_data["data"], dict):
                    response_data["data"] = [response_data["data"]]

                return response_data

            except requests.HTTPError as e:
                status = e.response.status_code if e.response else "N/A"

                logger.error(
                    f"Attempt {attempt}/{max_retries} failed "
                    f"(HTTP {status}): "
                    f"{str(e).replace(api_url, '**REDACTED**')}"
                )

                if attempt < max_retries:
                    logger.warning(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error("Maximum retries reached.")

            except requests.RequestException as e:
                # Non-5xx errors → don't retry automatically
                logger.error(
                    f"Non-retryable error: "
                    f"{str(e).replace(api_url, '**REDACTED**')}"
                )
                return None

        # If we reach here → max retries exhausted
        await asyncio.sleep(0.5)
        user_input = input("Retry again? (y/n): ").strip().lower()

        if user_input == "y":
            logger.info("User chose to retry again.")
            continue
        else:
            logger.warning("User chose to continue without retrying.")
            return None


async def select_entry(valid_entries, send_notification):
    advanced_mode = False
    if len(valid_entries) > 1:
        logger.warning("More than 1 scene returned in results. Please select the one to keep (or choose 0 to select nothing):")
        base_url = "https://theporndb.net/scenes/"
        for index, item in enumerate(valid_entries, start=1):
            duration = item['duration']
            formatted_duration = (
                time.strftime('%H:%M:%S', time.gmtime(duration))
                if duration is not None else None
            )
            performers = ", ".join([p.get('name', 'Unknown') for p in item.get('performers', [])])
            try:
                if advanced_mode:
                    logger.info(
                        f"{index}. UUID: {item['id']} | Studio: {item['site']['name']} | Title: {item['title']} | Date: {item['date'].replace('-', '.')} | Duration: {formatted_duration} | Performers: {performers}"
                        f"\n{item['url']} | {base_url}{item['slug']}")
                else:
                    logger.info(
                        f"{index}. Studio: {item['site']['name']} | Title: {item['title']} | Date: {item['date'].replace('-', '.')} | Duration: {formatted_duration} | Performers: {performers}"
                        f"\n{item['url']} | {base_url}{item['slug']}")
            except KeyError:
                logger.warning(f"{index}. (No title available)")

        logger.info("0. None of the results are good")
        await asyncio.sleep(0.5)
        if send_notification:
            result = await send_notification("User input required - Select Entry to Keep")
            if not result:
                logger.warning("Notifier failed to send user input request.")
            await asyncio.sleep(0.5)
        while True:
            try:
                choice = int(input(f"Enter the number of the result to keep (0-{len(valid_entries)}): \n"))
                if 0 <= choice <= len(valid_entries):
                    break
                else:
                    logger.error(f"Please enter a number between 0 and {len(valid_entries)}.")
            except ValueError:
                logger.error("Invalid input. Please enter a number.")

        if choice == 0:
            logger.info("You chose to select nothing.")
            return None

        chosen_entry = valid_entries[choice - 1]
        logger.success(f"You selected: {chosen_entry.get('title', '(No title available)')}")
        return chosen_entry
    elif len(valid_entries) == 1:
        logger.info("Only one entry found. Automatically keeping it.")
        return valid_entries[0]
    else:
        logger.warning("No valid entries found.")
        return None


async def fetch_api_site_data(api_url, api_auth, site_parent, max_retries, delay, debug=False):
    """
    Fetch the top-level parent of a site from an API
    :param api_url: Base URL for the API
    :param api_auth: Authorization token for the API
    :param site_parent: Initial parent ID to start the traversal
    :param max_retries: Maximum number of retry attempts
    :param delay: Delay between retries (in seconds)
    :param debug: Enable detailed logging for debugging
    :return: Name of the top-level parent or None if the request fails
    """
    url = f"{api_url}{site_parent}"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_auth}"
    }

    for attempt in range(max_retries):
        try:
            # Fetch data for the current site
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            response_data = response.json()

            if 'data' in response_data:
                if attempt > 0:
                    logger.info("Retry successful!")

                # Start traversing the parent hierarchy
                while site_parent:
                    if debug:
                        logger.info(f"Fetching data for site: {site_parent}")
                        logger.debug(json.dumps(response_data, indent=4))

                    # Get current site's name (this could be the top-level parent if no parent is found)
                    top_parent = response_data['data'].get("name", "Unknown")

                    # If there's no parent, we've reached the top-level parent
                    next_parent = response_data['data'].get("parent", None)

                    if next_parent is None:  # Top-level parent reached
                        # logger.info(f"Top-level parent found: {top_parent}")
                        return top_parent

                    # Move to the next parent
                    site_parent = response_data['data']['parent']['uuid']
                    url = f"{api_url}{site_parent}"
                    response = requests.get(url, headers=headers)
                    response.raise_for_status()
                    response_data = response.json()

            # If the response data does not contain 'data', it's an error case
            logger.error("Data key not found in the response.")
            return None

        except requests.RequestException as e:
            logger.error(f"Attempt {attempt + 1} failed: {str(e).replace(api_url, '**REDACTED**')}")
            if attempt < max_retries - 1:
                logger.warning(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
                return None
            else:
                logger.error("Maximum retries reached. Request failed.")
                return None
    return None


async def get_user_input_performers(selected_entry, api_mode_url, send_notification):
    """
    Asks the user for a yes/no response.
    If 'yes', prompts for text input and returns it.
    If 'no', returns None.
    Continues prompting until a valid response is given.
    """
    scene_title = selected_entry.get('title', '(No title available)')
    scene_slug = selected_entry.get('slug', '(No scene available)')
    scene_url = f"{api_mode_url}{scene_slug}"
    temp_performers = []
    while True:
        try:
            if send_notification:
                result = await send_notification("User input required - Manual Performer Entry")
                if not result:
                    logger.warning("Notifier failed to send user input request.")
                await asyncio.sleep(0.5)
            logger.info(f'User input required - Manual Performer Entry for scene: "{scene_title}" | {scene_url}')
            await asyncio.sleep(0.5)
            response = input("Do you want to provide Manual Performers? (yes/no): ").strip().lower()
            if response in ("yes", "y"):
                while True:
                    temp_performers = []  # reset the list at the start of the loop
                    while True:
                        await asyncio.sleep(0.5)
                        name = input("Enter Performer (leave blank to finish, type 'restart' to start over): ").strip()

                        if name.lower() == "restart":
                            print("Restarting performer entry...")
                            break  # break inner loop to start over

                        if not name:
                            break  # finish entering names

                        temp_performers.append(name)

                    # If the inner loop finished normally (not via 'restart'), return the list
                    if temp_performers and name.lower() != "restart":
                        return temp_performers

            elif response in ("no", "n"):
                return None
            else:
                logger.warning("Invalid input. Please enter 'yes' or 'no'.")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            return None


async def filter_entries_by_date(response_data, scene_date, api_mode_url, send_notification, mode):
    try:
        valid_entries = []
        unmatched_entries = []
        if mode == 1:
            for item in response_data['data']:
                valid_entries.append(item)
        elif mode == 2:
            scene_date = datetime.strptime(scene_date, '%Y-%m-%d')
            for item in response_data['data']:
                slug = item.get('slug', '').lower()
                full_scene_url = f"{api_mode_url}{slug}"
                title = item.get('title', '').lower()
                item_date = datetime.strptime(item.get('date', ''), '%Y-%m-%d')  # Assuming item date is also 'YYYY-MM-DD'
                # Check if title contains 'interview'
                if "interview" in title:
                    await asyncio.sleep(0.5)
                    if send_notification:
                        result = await send_notification(f"User input required - Filter Entries by Date")
                        if not result:
                            logger.warning("Notifier failed to send user input request.")
                    await asyncio.sleep(0.5)
                    user_input = input(f"The scene title '{item.get('title')}' contains 'interview'. Do you want to exclude it from processing? (y/n): ").strip().lower()
                    if user_input in ["y", "yes"]:
                        logger.info(f"Ignoring scene: {item.get('title')}")
                        continue
                    else:
                        logger.info(f"Including scene: {item.get('title')}")

                # Exact date match
                if item_date == scene_date:
                    valid_entries.append(item)
                # Date range check (within ±1 to ±7 days)
                elif abs((item_date - scene_date).days) in range(1, 7):
                    await asyncio.sleep(0.5)
                    if send_notification:
                        result = await send_notification("User input required - Filter Entries by Date")
                        if not result:
                            logger.warning("Notifier failed to send user input request.")
                    await asyncio.sleep(0.5)
                    user_input = input(f"The scene '{item.get('title')}' has a date that is {abs((item_date - scene_date).days)} day(s) away from the target date. Do you want to "
                                       f"include it in the results? (y/n): ").strip().lower()
                    if user_input in ["y", "yes"]:
                        valid_entries.append(item)
                        logger.warning(f"Scene '{item.get('title')}' has a date that is {abs((item_date - scene_date).days)} day(s) away from the target date and was added.")
                    else:
                        logger.info(f"Scene '{item.get('title')}' was not included due to date difference.")
                else:
                    unmatched_entries.append((item.get('title'), full_scene_url, item, item.get('date')))
        # If still no valid entries, show unmatched ones for manual selection
            if not valid_entries and unmatched_entries:
                logger.warning("No entries matched the exact or close date range, but the following scenes were found with some confidence to be matched:")
                for idx, (title, url, _, scene_date) in enumerate(unmatched_entries, 1):
                    logger.info(f"{idx}. {title} — {scene_date} — {url}")
                await asyncio.sleep(0.5)
                if send_notification:
                    result = await send_notification("User input required - Manual Selection of Entries")
                    if not result:
                        logger.warning("Notifier failed to send user input request.")
                await asyncio.sleep(0.5)
                user_input = input("Enter the number of the entry you'd like to select (or press Enter to skip): ").strip()
                if user_input.isdigit():
                    selection_index = int(user_input) - 1
                    if 0 <= selection_index < len(unmatched_entries):
                        valid_entries.append(unmatched_entries[selection_index][2])
                        logger.info(f"Manually selected entry: {unmatched_entries[selection_index][0]}")
                    else:
                        logger.warning("Invalid selection index. No entry added.")
                else:
                    logger.info("No entry selected manually.")

        return valid_entries if valid_entries else None

    except Exception as e:
        logger.error(f"Error filtering entries by date: {str(e)}")
        return None


async def extract_female_performers(selected_entry, api_mode_url, filename_ignore_performer_ID, send_notification, mode=1):

    def clean_name(name: str) -> str:
        # Remove any word that starts with ID followed by optional space and digits
        cleaned_name = re.sub(r"\bID\s*\d+\b", "", name, flags=re.IGNORECASE).strip()
        # Remove extra spaces that may remain after deletion
        cleaned_name = re.sub(r"\s{2,}", " ", cleaned_name)
        return cleaned_name

    try:
        female_performers = []
        for performer in selected_entry.get("performers", []):  # Access the 'performers' list directly
            if (
                    performer.get("parent") and
                    performer["parent"].get("extras") and
                    (performer["parent"]["extras"].get("gender") == "Female" or performer["parent"]["extras"].get("gender") == "Transgender Female")
            ):
                if performer.get("name") == performer["parent"].get("name"):
                    # no alias used
                    if filename_ignore_performer_ID:
                        performer_name = clean_name(performer.get("name", "Unknown"))
                        if performer_name != "":
                            female_performers.append((performer_name, performer["parent"].get("id", "")))
                        else:
                            female_performers.append(("Invalid Name", performer["parent"].get("id", "")))
                    else:
                        female_performers.append((performer.get("name", "Unknown"), performer["parent"].get("id", "")))
                else:
                    # alias used
                    alias = clean_name(performer.get("name", ""))
                    if alias == "":
                        logger.warning("No valid alias found for this performer.")
                        p_name = f"{performer['parent'].get('name', 'Unknown')}"
                    else:
                        p_name = f"{performer['parent'].get('name', 'Unknown')}({alias})"
                    female_performers.append((p_name, performer["parent"].get("id", "")))
            elif (
                    performer.get("parent") and
                    performer["parent"].get("extras") and
                    performer["parent"]["extras"].get("gender") is None
            ):
                # Ask the user for input
                if send_notification:
                    result = await send_notification("User input required - Approve Performer Gender")
                    if not result:
                        logger.warning("Notifier failed to send user input request.")
                await asyncio.sleep(0.5)
                user_input = input(f"Treat performer '{performer.get('name', 'Unknown')}' as Female? (yes/no): ").strip().lower()

                if user_input in ("yes", "y"):
                    if filename_ignore_performer_ID:
                        performer_name = clean_name(performer.get("name", "Unknown"))
                        if performer_name != "":
                            female_performers.append((performer_name, performer["parent"].get("id", "")))
                        else:
                            female_performers.append(("Invalid Name", performer["parent"].get("id", "")))
                    else:
                        female_performers.append((performer.get("name", "Unknown"), performer["parent"].get("id", "")))

        female_performers.sort()

        # Mode 2: ask user which extracted performers are relevant for this scene
        if mode == 2 and female_performers:
            if send_notification:
                result = await send_notification("User input required - Select Relevant Performers")
                if not result:
                    logger.warning("Notifier failed to send user input request.")
            await asyncio.sleep(0.5)

            logger.info("Extracted performers for this scene:")
            for i, (name, pid) in enumerate(female_performers, start=1):
                logger.info(f"  {i}. {name}")

            logger.info("Enter the numbers of the performers to KEEP, separated by commas (e.g. 1,3) or 'all' to keep everyone:")
            raw = input("> ").strip().lower()

            if raw != "all":
                try:
                    selected_indices = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
                    female_performers = [
                        p for i, p in enumerate(female_performers, start=1)
                        if i in selected_indices
                    ]
                except ValueError:
                    logger.warning("Invalid input for performer selection, keeping all performers.")

        if female_performers:
            return female_performers
        else:
            user_entries = await get_user_input_performers(selected_entry, api_mode_url, send_notification)
            if user_entries:
                female_performers.extend([(name, "") for name in user_entries])
            if not female_performers or len(female_performers) < 1:
                return None
            else:
                return female_performers
    except Exception as e:
        logger.error(f"Error extracting female performers: {str(e)}")
        return None


async def get_performer_profile_picture(performer_name: str, performer_id: str, posters_limit: int):
    """
    Queries an external API to get profile picture data for a performer
    :param posters_limit:
    :param performer_name: Name of the performer
    :param performer_id: Unique performer ID
    :return: List of poster URLs, and performer slug, or None and None on failure
    """
    max_retries = 3
    delay = 5

    if not performer_name:
        logger.error("Valid Performer ID is required, manual performer name was provided without ID.")
        return None

    try:
        api_auth, api_performers_url, _ = await load_credentials(mode=2)
        if not api_performers_url or not api_auth:
            logger.error("API URL or auth token missing. Aborting API request.")
            return None

        for attempt in range(max_retries):
            try:
                # logger.debug(f"Sending API request for performer '{performer_name}' (Attempt {attempt + 1})")
                raw_data = await send_request(api_performers_url, api_auth, performer_id, max_retries, delay)

                if raw_data:
                    # logger.debug(f"Received raw data for performer: {performer_name}")

                    # Process and return poster URLs
                    processed_data = await extract_performer_posters(raw_data, posters_limit)
                    performer_slug = raw_data.get("data", {}).get("slug", performer_id)

                    return processed_data, performer_slug
                else:
                    logger.warning(f"No data returned for performer: {performer_name}")

            except Exception:
                logger.exception(f"Error occurred while requesting data for performer: {performer_name}")
                await asyncio.sleep(delay)

        logger.error(f"Failed to retrieve profile picture data after {max_retries} attempts for: {performer_name}")
        return None, None

    except Exception:
        logger.exception("An unexpected error occurred in get_performer_profile_picture")
        return None, None


async def extract_performer_posters(performer_data: dict, posters_limit: int) -> Optional[list[str]]:
    try:
        posters = performer_data.get("data", {}).get("posters", [])
        if not posters:
            return None
        # Sort posters by order (if order exists) to ensure correct sequence
        sorted_posters = sorted(posters, key=lambda x: x.get("order", 0))

        poster_urls = [poster.get("url") for poster in sorted_posters if "url" in poster]

        # Limit to the first 5 posters
        return poster_urls[:posters_limit] if poster_urls else None

    except Exception:
        logger.exception("Error extracting poster URLs")
        return None


async def extract_scene_tags(scene_data: dict) -> Optional[list[str]]:
    try:
        if not scene_data:
            return None

        scene_tags = []
        scene_data_tags = scene_data.get("tags", [])
        for tag in scene_data_tags:
            name = tag.get("name", "")

            # Remove anything inside brackets and the brackets themselves
            name = re.sub(r"\(.*?\)", "", name)
            # Remove leading/trailing spaces
            name = name.strip()
            # Replace remaining spaces with dots
            name = name.replace(" ", ".")
            # Remove all special characters except dots
            name = re.sub(r"[^a-zA-Z0-9.]", "", name)
            # Convert to lowercase
            name = name.lower()

            # remove consecutive dots
            while ".." in name:
                name = name.replace("..", ".")

            scene_tags.append(name)

        return scene_tags or None

    except Exception:
        logger.exception("Error extracting scene tags")
        return None


async def convert_number_suffix_to_word(s: str) -> str:
    """
    Converts a numeric suffix in a string like '.part.1' to a word form like '.part.one'.

    Args:
        s (str): Input string with a numeric suffix.

    Returns:
        str: Modified string with the number converted to words.
    """
    match = re.search(r"(.*\.part\.)(\d+)$", s, re.IGNORECASE)
    if match:
        prefix, number = match.groups()
        number_word = num2words(int(number))
        return f"{prefix}{number_word}"
    return s


async def remove_date_from_text(text: str) -> str:
    # This pattern matches dates in formats like YY.MM.DD or YYYY.MM.DD
    date_pattern = r'\b(?:\d{2}|\d{4})\.\d{2}\.\d{2}\b'
    # Remove the date pattern and any extra dots caused by removal
    cleaned = re.sub(date_pattern, '', text)
    # Remove any duplicate or trailing dots caused by the removal
    cleaned = re.sub(r'\.{2,}', '.', cleaned).strip('.')
    return cleaned


async def ensure_scene_collected(scene_id: str, jav_api_mode: bool, movies_api_mode: bool) -> bool:
    """
    Checks whether a scene is already collected.
    If not collected, adds it to the collection.

    This function is intentionally blocking and uses `requests`.
    """

    if not scene_id:
        logger.error("Scene ID is required.")
        return False

    def _request(method: str, url: str, token: str) -> dict | None:
        """
        Internal request handler (unique to this function).
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                timeout=60
            )

            if response.status_code not in (200, 201):
                logger.error(
                    f"{method} request failed [{response.status_code}]: {response.text}"
                )
                return None

            return response.json()

        except Exception:
            logger.exception(f"{method} request failed")
            return None

    try:
        api_auth, api_url, _ = await load_credentials(mode=8)

        if not api_auth or not api_url:
            logger.error("API auth token or URL missing (mode 8).")
            return False
        if jav_api_mode:
            url = f"{api_url}?scene_id={scene_id}&type=JAV"
        elif movies_api_mode:
            url = f"{api_url}?scene_id={scene_id}&type=Movie"
        else:
            url = f"{api_url}?scene_id={scene_id}&type=Scene"

        # ---- 1. CHECK (GET) ----
        check_response = _request("GET", url, api_auth)

        if not isinstance(check_response, dict) or "value" not in check_response:
            logger.error(f"Invalid check response for scene {scene_id}: {check_response}")
            return False

        if check_response["value"] is True:
            logger.warning(f"Scene {scene_id} already collected.")
            return True

        # ---- 2. ADD (POST) ----
        add_response = _request("POST", url, api_auth)

        if add_response is not None:
            if movies_api_mode:
                logger.info(f"Movie added to collection.")
            elif jav_api_mode:
                logger.info(f"JAV Scene added to collection.")
            else:
                logger.info(f"Scene added to collection.")
            return True

        logger.error(f"Failed to add scene {scene_id} to collection.")
        return False

    except Exception:
        logger.exception("Unexpected error while ensuring scene is collected.")
        return False
