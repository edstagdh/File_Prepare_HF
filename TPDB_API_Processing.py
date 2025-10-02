import asyncio
import json
import re
import requests
from num2words import num2words
import time
from loguru import logger
from datetime import datetime
from typing import Optional
from Utilities import load_credentials


async def get_data_from_api(string_parse, scene_date, manual_mode, tpdb_scenes_url, part_match, generate_hf_template, jav_api_mode, mode):
    max_retries = 3
    delay = 5
    try:
        work_mode = 4 if jav_api_mode else 1
        api_auth, api_scenes_url, api_sites_url = await load_credentials(mode=work_mode)
        if not api_scenes_url or not api_auth:
            logger.error("API URL or auth token missing. Aborting API request.")
            return None, None, None, None, None, None, None, None, None, None, None

        if mode == 1:
            # logger.debug(string_parse)
            response_data = await send_request(api_scenes_url, api_auth, string_parse, max_retries, delay)
        elif mode == 2:
            # logger.debug(string_parse)
            response_data = await send_request(api_scenes_url, api_auth, string_parse, max_retries, delay)
            if response_data is None or not response_data.get('data'):
                string_parse_fallback = await convert_number_suffix_to_word(string_parse)
                # logger.debug(string_parse_fallback)
                if string_parse_fallback != string_parse and part_match:
                    response_data = await send_request(api_scenes_url, api_auth, string_parse_fallback, max_retries, delay)
                elif response_data is None or not response_data.get('data'):
                    string_advanced_parse_fallback = await remove_date_from_text(string_parse)
                    # logger.debug(string_advanced_parse_fallback)
                    response_data = await send_request(api_scenes_url, api_auth, string_advanced_parse_fallback, max_retries, delay)
        else:
            return None, None, None, None, None, None, None, None, None, None, None

        if response_data is None or not response_data.get('data'):
            return None, None, None, None, None, None, None, None, None, None, None
        valid_entries = await filter_entries_by_date(response_data, scene_date, tpdb_scenes_url, mode)

        if not valid_entries:
            logger.error(f"No matching entries for the provided date for string: {string_parse}")
            return None, None, None, None, None, None, None, None, None, None, None

        if len(valid_entries) > 1:
            logger.warning("More than 1 scene returned in results, please be more specific")
            selected_entry = await filter_entries_by_user_choice(valid_entries)
        else:
            selected_entry = valid_entries[0]
        if selected_entry is None:
            logger.error("No matching entries selected by user.")
            return None, None, None, None, None, None, None, None, None, None, None
        # Safely extract fields from selected_entry
        title = selected_entry.get('title')
        image_url = selected_entry.get('image')
        alt_image = selected_entry.get("background", {}).get("full")
        scene_description = selected_entry.get('description')
        scene_date = selected_entry.get('date')
        slug = selected_entry.get('slug')
        url = selected_entry.get('url')
        if generate_hf_template:
            scene_tags = await extract_scene_tags(selected_entry)
        else:
            scene_tags = None
        site = selected_entry.get("site", {}).get("name")
        if "onlyfans" in site.lower() and "fansdb" in site.lower():
            site = site.replace("FansDB: ", "")
            site = site.replace(" (onlyfans)", "")
            site = "OnlyFans-" + site
        if "manyvids" in site.lower() and "fansdb" in site.lower():
            site = site.replace("FansDB: ", "")
            site = site.replace(" (manyvids)", "")
            site = "ManyVids-" + site
        site_parent = selected_entry.get("site", {}).get("parent")
        if site_parent:
            site_parent_uuid = site_parent.get("uuid")
            site_owner = await fetch_api_site_data(api_sites_url, api_auth, site_parent_uuid, max_retries, delay)
        else:
            site_owner = site
        if not manual_mode:
            female_performers = await extract_female_performers(selected_entry)
        else:
            await asyncio.sleep(0.5)
            female_performers = []
            while True:
                logger.info("Enter Performers Manually")
                user_input = input("Enter a value (or type 'exit' to stop): ")
                if user_input.lower() == 'exit':
                    break
                female_performers.append((user_input, ""))
        if not female_performers:
            return title, None, image_url, slug, url, alt_image, site, site_owner, scene_description, scene_date, scene_tags
        elif "Unknown" in female_performers:
            return title, "Invalid", image_url, slug, url, alt_image, site, site_owner, scene_description, scene_date, scene_tags

        return title, female_performers, image_url, slug, url, alt_image, site, site_owner, scene_description, scene_date, scene_tags

    except Exception as e:
        logger.error(f"An unexpected error occurred in get_data_from_api: {str(e)}")
        return None, None, None, None, None, None, None, None, None, None, None


async def send_request(api_url, api_auth, string_parse, max_retries, delay):
    if "performers" in api_url:
        url = f"{api_url}{string_parse}"
    else:
        url = f"{api_url}?parse={string_parse}"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_auth}"
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            response_data = response.json()
            if 'data' in response_data:
                if attempt > 0:
                    logger.info("Retry successful!")
                # Debug
                # logger.info(f"API data fetched successfully for file {string_parse}")
                return response_data
        except requests.RequestException as e:
            logger.error(f"Attempt {attempt + 1} failed: {str(e).replace(api_url, '**REDACTED**')}")
            if attempt < max_retries - 1:
                logger.warning(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error("Maximum retries reached. Request failed.")
                return None


async def filter_entries_by_user_choice(valid_entries):
    if len(valid_entries) > 1:
        logger.warning("More than 1 scene returned in results. Please select the one to keep (or choose 0 to select nothing):")
        base_url = "https://theporndb.net/scenes/"
        for index, item in enumerate(valid_entries, start=1):
            duration = item['duration']
            formatted_duration = (
                time.strftime('%H:%M:%S', time.gmtime(duration))
                if duration is not None else None
            )
            try:
                logger.info(f"{index}. Studio: {item['site']['name']} | Title: {item['title']} | Date: {item['date']} | Duration: {formatted_duration}\n{item['url']} | {base_url}{item['slug']}")
            except KeyError:
                logger.warning(f"{index}. (No title available)")

        logger.info("0. None of the results are good")
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
            else:
                logger.error("Maximum retries reached. Request failed.")
                return None


async def get_user_input():
    """
    Asks the user for a yes/no response.
    If 'yes', prompts for text input and returns it.
    If 'no', returns None.
    Continues prompting until a valid response is given.
    """
    temp_performers = []
    while True:
        try:
            response = input("Do you want to provide Manual Performers? (yes/no): ").strip().lower()
            if response in ("yes", "y"):
                while True:
                    name = input("Enter Performer (leave blank to finish): ").strip()
                    if not name:
                        break
                    temp_performers.append(name)
                if temp_performers:
                    return temp_performers
            elif response in ("no", "n"):
                return None
            else:
                logger.warning("Invalid input. Please enter 'yes' or 'no'.")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            return None


async def filter_entries_by_date(response_data, scene_date, tpdb_scenes_url, mode):
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
                full_scene_url = f"{tpdb_scenes_url}{slug}"
                title = item.get('title', '').lower()
                item_date = datetime.strptime(item.get('date', ''), '%Y-%m-%d')  # Assuming item date is also 'YYYY-MM-DD'
                # Check if title contains 'interview'
                if "interview" in title:
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


async def extract_female_performers(selected_entry):
    try:
        female_performers = []
        for performer in selected_entry.get("performers", []):  # Access the 'performers' list directly
            if (
                    performer.get("parent") and
                    performer["parent"].get("extras") and
                    (performer["parent"]["extras"].get("gender") == "Female" or performer["parent"]["extras"].get("gender") == "Transgender Female")
            ):
                if performer.get("name") == performer["parent"].get("name"):
                    female_performers.append((performer.get("name", "Unknown"), performer["parent"].get("id", "")))
                else:
                    female_performers.append((performer["parent"].get("name", "Unknown"), performer["parent"].get("id", "")))
            elif (
                    performer.get("parent") and
                    performer["parent"].get("extras") and
                    performer["parent"]["extras"].get("gender") is None
            ):
                # Ask the user for input
                user_input = input(f"Treat performer '{performer.get('name', 'Unknown')}' as Female? (yes/no): ").strip().lower()
                if user_input in ("yes", "y"):
                    female_performers.append((performer.get("name", "Unknown"), performer["parent"].get("id", "")))

        female_performers.sort()
        if female_performers:
            return female_performers
        else:
            user_entries = await get_user_input()
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
        scene_tags = []
        if not scene_data:
            return None

        scene_data_tags = scene_data.get("tags", [])
        for tag in scene_data_tags:
            name = tag.get("name", "")

            # Remove anything inside brackets and the brackets themselves
            name = re.sub(r"\(.*?\)", "", name)
            # Remove trailing space
            if name.endswith(" "):
                name = name[:-1]
            # Replace remaining spaces with dots
            name = name.replace(" ", ".")
            # Remove all special characters except dots
            name = re.sub(r"[^a-zA-Z0-9.]", "", name)
            # Convert to lowercase
            name = name.lower()

            scene_tags.append(name)

        return scene_tags

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
