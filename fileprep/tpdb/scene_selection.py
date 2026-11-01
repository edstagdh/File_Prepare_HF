"""
Narrowing a raw API response down to a single scene entry: date-based
filtering of candidates, and manual selection when more than one survives.
"""

import asyncio
import time
from datetime import datetime
from typing import Optional

from loguru import logger

from .prompt_utils import NotifyFn, notify_and_pause, prompt


async def select_entry(valid_entries: list, send_notification: NotifyFn) -> Optional[dict]:
    """
    Let the user pick which of several candidate scene entries to keep.

    Args:
        valid_entries: Candidate scene dicts (already date-filtered).
        send_notification: Optional async push-notification callback.

    Returns:
        The chosen entry dict, the single entry directly if there was only
        one, or None if there were no entries or the user picked "0".
    """
    advanced_mode = False

    if len(valid_entries) > 1:
        logger.warning(
            "More than 1 scene returned in results. Please select the one to keep "
            "(or choose 0 to select nothing):"
        )
        base_url = "https://theporndb.net/scenes/"
        for index, item in enumerate(valid_entries, start=1):
            duration = item["duration"]
            formatted_duration = (
                time.strftime("%H:%M:%S", time.gmtime(duration)) if duration is not None else None
            )
            performers = ", ".join(p.get("name", "Unknown") for p in item.get("performers", []))
            try:
                if advanced_mode:
                    logger.info(
                        f"{index}. UUID: {item['id']} | Studio: {item['site']['name']} | Title: {item['title']} | "
                        f"Date: {item['date'].replace('-', '.')} | Duration: {formatted_duration} | "
                        f"Performers: {performers}\n{item['url']} | {base_url}{item['slug']}"
                    )
                else:
                    logger.info(
                        f"{index}. Studio: {item['site']['name']} | Title: {item['title']} | "
                        f"Date: {item['date'].replace('-', '.')} | Duration: {formatted_duration} | "
                        f"Performers: {performers}\n{item['url']} | {base_url}{item['slug']}"
                    )
            except KeyError:
                logger.warning(f"{index}. (No title available)")

        logger.info("0. None of the results are good")
        await asyncio.sleep(0.5)
        await notify_and_pause(send_notification, "User input required - Select Entry to Keep")

        while True:
            try:
                choice = int(await prompt(f"Enter the number of the result to keep (0-{len(valid_entries)}): \n"))
                if 0 <= choice <= len(valid_entries):
                    break
                logger.error(f"Please enter a number between 0 and {len(valid_entries)}.")
            except ValueError:
                logger.error("Invalid input. Please enter a number.")

        if choice == 0:
            logger.info("You chose to select nothing.")
            return None

        chosen_entry = valid_entries[choice - 1]
        logger.success(f"You selected: {chosen_entry.get('title', '(No title available)')}")
        return chosen_entry

    if len(valid_entries) == 1:
        logger.info("Only one entry found. Automatically keeping it.")
        return valid_entries[0]

    logger.warning("No valid entries found.")
    return None


async def filter_entries_by_date(
        response_data: dict,
        scene_date: str,
        api_mode_url: str,
        send_notification: NotifyFn,
        mode: int,
) -> Optional[list]:
    """
    Filter API results down to entries matching (or close to) a target
    date, with interactive fallbacks for near-misses and "interview"-titled
    scenes.

    Args:
        response_data: Raw API response with a "data" list of scene dicts.
        scene_date: Target date as "YYYY-MM-DD" (only used when mode == 2).
        api_mode_url: Base URL used to build a scene link for prompts.
        send_notification: Optional async push-notification callback.
        mode: 1 to accept every entry as-is, 2 to apply date filtering/
            interactive review. Any other value returns an empty result
            (via the ``valid_entries`` list staying empty).

    Returns:
        The list of accepted entries, or None if none were accepted or an
        error occurred.
    """
    try:
        valid_entries = []
        unmatched_entries = []

        if mode == 1:
            valid_entries.extend(response_data["data"])

        elif mode == 2:
            target_date = datetime.strptime(scene_date, "%Y-%m-%d")

            for item in response_data["data"]:
                slug = item.get("slug", "").lower()
                full_scene_url = f"{api_mode_url}{slug}"
                title = item.get("title", "").lower()
                item_date = datetime.strptime(item.get("date", ""), "%Y-%m-%d")

                if "interview" in title:
                    # Interview-tagged scenes are often not what the user wants matched -
                    # double sleep here (before and after the notification) is intentional
                    # in the original flow, preserved as-is.
                    await asyncio.sleep(0.5)
                    await notify_and_pause(send_notification, "User input required - Filter Entries by Date")
                    user_input = (await prompt(
                        f"The scene title '{item.get('title')}' contains 'interview'. "
                        f"Do you want to exclude it from processing? (y/n): "
                    )).strip().lower()
                    if user_input in ("y", "yes"):
                        logger.info(f"Ignoring scene: {item.get('title')}")
                        continue
                    logger.info(f"Including scene: {item.get('title')}")

                if item_date == target_date:
                    valid_entries.append(item)
                elif abs((item_date - target_date).days) in range(1, 7):
                    days_off = abs((item_date - target_date).days)
                    await asyncio.sleep(0.5)
                    await notify_and_pause(send_notification, "User input required - Filter Entries by Date")
                    user_input = (await prompt(
                        f"The scene '{item.get('title')}' has a date that is {days_off} day(s) away from the "
                        f"target date. Do you want to include it in the results? (y/n): "
                    )).strip().lower()
                    if user_input in ("y", "yes"):
                        valid_entries.append(item)
                        logger.warning(
                            f"Scene '{item.get('title')}' has a date that is {days_off} day(s) away from the "
                            f"target date and was added."
                        )
                    else:
                        logger.info(f"Scene '{item.get('title')}' was not included due to date difference.")
                else:
                    unmatched_entries.append((item.get("title"), full_scene_url, item, item.get("date")))

            if not valid_entries and unmatched_entries:
                logger.warning(
                    "No entries matched the exact or close date range, but the following scenes were found "
                    "with some confidence to be matched:"
                )
                for idx, (title, url, _, entry_date) in enumerate(unmatched_entries, 1):
                    logger.info(f"{idx}. {title} - {entry_date} - {url}")

                await asyncio.sleep(0.5)
                await notify_and_pause(send_notification, "User input required - Manual Selection of Entries")
                user_input = (await prompt(
                    "Enter the number of the entry you'd like to select (or press Enter to skip): "
                )).strip()

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
