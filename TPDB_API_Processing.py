import json
import requests
from loguru import logger
import time
from time import sleep
from datetime import datetime
from typing import Optional


async def load_api_credentials(mode):
    # mode = 1, return scene data, mode = 2, return performer data
    try:
        with open('creds.secret', 'r') as secret_file:
            secrets = json.load(secret_file)
            if mode == 1:
                return secrets["api_auth"], secrets["api_scenes_url"], secrets["api_sites_url"]
            elif mode == 2:
                return secrets["api_auth"], secrets["api_performer_url"], None
            else:
                return None, None, None

    except FileNotFoundError:
        logger.error("creds.secret file not found.")
        return None, None, None
    except KeyError as e:
        logger.error(f"Key {e} is missing in the secret.json file.")
        return None, None, None
    except json.JSONDecodeError:
        logger.error("Error parsing creds.secret. Ensure the JSON is formatted correctly.")
        return None, None, None


async def get_data_from_api(string_parse, scene_date, manual_mode):
    max_retries = 3
    delay = 5
    try:
        api_auth, api_scenes_url, api_sites_url = await load_api_credentials(mode=1)
        if not api_scenes_url or not api_auth:
            logger.error("API URL or auth token missing. Aborting API request.")
            return None, None, None, None, None, None, None, None

        response_data = await send_request(api_scenes_url, api_auth, string_parse, max_retries, delay)
        if response_data is None:
            return None, None, None, None, None, None, None, None
        valid_entries = await filter_entries_by_date(response_data, scene_date)

        if not valid_entries:
            logger.error("No matching entries for the provided date.")
            return None, None, None, None, None, None, None, None

        if len(valid_entries) > 1:
            logger.warning("More than 1 scene returned in results, please be more specific")
            selected_entry = await filter_entries_by_user_choice(valid_entries)
        else:
            selected_entry = valid_entries[0]
        if selected_entry is None:
            logger.error("No matching entries selected by user.")
            return None, None, None, None, None, None, None, None
        # Safely extract fields from selected_entry
        title = selected_entry.get('title')
        image_url = selected_entry.get('image')
        alt_image = selected_entry.get("background", {}).get("full")
        slug = selected_entry.get('slug')
        url = selected_entry.get('url')
        site = selected_entry.get("site", {}).get("name")
        if "onlyfans" in site.lower() and "FansDB" in site.lower():
            site = site.replace("FansDB: ", "")
            site = site.replace(" (onlyfans)", "")
            site = "OnlyFans-" + site
        if "manyvids" in site.lower() and "FansDB" in site.lower():
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
            sleep(0.5)
            female_performers = []
            while True:
                logger.debug("Enter Performers Manually")
                user_input = input("Enter a value (or type 'exit' to stop): ")
                if user_input.lower() == 'exit':
                    break
                female_performers.append((user_input, ""))

        if not female_performers:
            return title, None, image_url, slug, url, alt_image, site, site_owner
        elif "Unknown" in female_performers:
            return title, "Invalid", image_url, slug, url, alt_image, site, site_owner

        return title, female_performers, image_url, slug, url, alt_image, site, site_owner

    except Exception as e:
        logger.error(f"An unexpected error occurred in get_data_from_api: {str(e)}")
        return None, None, None, None, None, None, None, None


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
                time.sleep(delay)
            else:
                logger.error("Maximum retries reached. Request failed.")
                return None


async def filter_entries_by_user_choice(valid_entries):
    if len(valid_entries) > 1:
        logger.warning("More than 1 scene returned in results. Please select the one to keep (or choose 0 to select nothing):")
        base_url = "https://theporndb.net/scenes/"
        for index, item in enumerate(valid_entries, start=1):

            try:
                logger.info(f"{index}. {item['title']} | {item['site']['name']} | {item['url']} | {base_url}{item['slug']}")
            except KeyError:
                logger.warning(f"{index}. (No title available)")

        logger.info("0. None of the results are good")

        while True:
            try:
                choice = int(input(f"Enter the number of the result to keep (0-{len(valid_entries)}): "))
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
                time.sleep(delay)
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
    while True:
        try:
            response = input("Do you want to provide Manual Performers? (yes/no): ").strip().lower()

            if response == "yes" or response == "y":
                temp_performers = input("Enter Performers Manually: ").strip()
                if temp_performers:
                    female_performers = temp_performers
                    return female_performers
            elif response == "no" or response == "n":
                return None
            else:
                logger.warning("Invalid input. Please enter 'yes' or 'no'.")

        except Exception as e:
            logger.error(f"An error occurred: {e}")
            return None  # Return None in case of unexpected errors


async def filter_entries_by_date(response_data, scene_date):
    try:
        valid_entries = []
        # logger.debug(response_data)
        # logger.debug(scene_date)
        scene_date = datetime.strptime(scene_date, '%Y-%m-%d')  # Assuming scene_date is a string in 'YYYY-MM-DD' format
        for item in response_data['data']:
            title = item.get('title', '').lower()
            item_date = datetime.strptime(item.get('date', ''), '%Y-%m-%d')  # Assuming item date is also 'YYYY-MM-DD'

            # Check if title contains 'interview'
            if "interview" in title:
                sleep(0.5)
                user_input = input(f"The scene title '{item.get('title')}' contains 'interview'. Do you want to ignore it? (y/n): ").strip().lower()
                if user_input in ["y", "yes"]:
                    logger.info(f"Including scene: {item.get('title')}")
                else:
                    logger.info(f"Ignoring scene: {item.get('title')}")
                    continue

            # Exact date match
            if item_date == scene_date:
                valid_entries.append(item)

            # Date range check (within ±1 to ±4 days)
            elif abs((item_date - scene_date).days) in range(1, 5):
                sleep(0.5)
                user_input = input(f"The scene '{item.get('title')}' has a date that is {abs((item_date - scene_date).days)} day(s) away from the target date. Do you want to "
                                   f"include it? (y/n): ").strip().lower()
                if user_input in ["y", "yes"]:
                    valid_entries.append(item)
                    logger.warning(f"Scene '{item.get('title')}' has a date that is {abs((item_date - scene_date).days)} day(s) away from the target date and was added.")
                else:
                    logger.info(f"Scene '{item.get('title')}' was not included due to date difference.")

        if valid_entries:
            return valid_entries
        else:
            logger.error("No matching entries for the provided date.")
            return None

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
                    performer["parent"]["extras"].get("gender") == "Female"
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
            female_performers.append(await get_user_input())
            if not female_performers:
                return ["Unknown"]
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
        api_auth, api_performers_url, _ = await load_api_credentials(mode=2)
        if not api_performers_url or not api_auth:
            logger.error("API URL or auth token missing. Aborting API request.")
            return None

        for attempt in range(max_retries):
            try:
                # logger.debug(f"Sending API request for performer '{performer_name}' (Attempt {attempt + 1})")
                raw_data = await send_request(api_performers_url, api_auth, performer_id, max_retries, delay)

                if raw_data:
                    logger.success(f"Received raw data for performer: {performer_name}")

                    # Process and return poster URLs
                    processed_data = await extract_performer_posters(raw_data, posters_limit)
                    performer_slug = raw_data.get("data", {}).get("slug", performer_id)

                    return processed_data, performer_slug
                else:
                    logger.warning(f"No data returned for performer: {performer_name}")

            except Exception:
                logger.exception(f"Error occurred while requesting data for performer: {performer_name}")
                time.sleep(delay)

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
