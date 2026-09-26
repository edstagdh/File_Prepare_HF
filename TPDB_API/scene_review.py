"""
The interactive "review this before we use it" steps that happen after a
scene entry is selected: checking file duration against markers, optionally
appending a scene number to the title, and letting the user approve/edit the
date and title.
"""

import asyncio
import time
from datetime import datetime
from typing import Optional

from loguru import logger

from .prompt_utils import NotifyFn, notify_and_pause, prompt


async def review_duration_and_markers(
        file,
        scene_data: dict,
        markers: Optional[list],
        warn_length_match: bool,
        add_timestamps_markers: bool,
        send_notification: NotifyFn,
) -> Optional[list]:
    """
    Compare the local file's duration against the scene's reported
    duration, warn on a large mismatch, and interactively fix or drop any
    markers that fall outside the file's actual duration.

    Args:
        file: Local media file (passed through to ``get_video_duration``).
        scene_data: The scene's API data, used for its reported "duration".
        markers: The scene's marker list (start_time/end_time/id/title), or
            None.
        warn_length_match: If True, log a warning when file/scene durations
            differ by more than 20 seconds.
        add_timestamps_markers: If True, interactively review markers whose
            start/end time exceeds the file's actual duration.
        send_notification: Optional async push-notification callback.

    Returns:
        The (possibly fixed) marker list, or None if there are none left.
        Returns the original ``markers`` unchanged if neither
        ``warn_length_match`` nor ``add_timestamps_markers`` triggered any
        checks.
    """
    from Media_Processing import get_video_duration  # local import to avoid a module-level dependency cycle

    duration, _ = await get_video_duration(file)
    scene_duration = scene_data.get("duration")
    temp_duration = time.strftime("%H:%M:%S", time.gmtime(duration)) if duration is not None else "N/A"
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

    if duration is None or not markers or not add_timestamps_markers:
        return markers

    fixed_markers = []
    for marker in markers:
        start = marker.get("start_time")
        end = marker.get("end_time")
        temp_start = time.strftime("%H:%M:%S", time.gmtime(start)) if start is not None else "N/A"
        formatted_start = f"{temp_start} ({start})"
        temp_end = time.strftime("%H:%M:%S", time.gmtime(end)) if end is not None else "N/A"
        formatted_end = f"{temp_end} ({end})"

        invalid = (start is not None and start > duration) or (end is not None and end > duration)
        if not invalid:
            fixed_markers.append(marker)
            continue

        logger.warning(
            "Invalid marker detected: id={} | start={} ({}) | end={} ({}) | video_duration={} ({}) | file={}",
            marker.get("id"),
            start,
            formatted_start if start is not None else "N/A",
            end,
            formatted_end if end is not None else "N/A",
            duration,
            formatted_duration,
            file,
        )

        while True:
            await notify_and_pause(send_notification, "User input required - Select Entry to Keep")
            choice = (await prompt(
                f"Marker '{marker.get('title')}' exceeds duration, would you like to (F)ix / (R)emove marker: \n"
            )).strip().lower()

            if choice == "f":
                if start is not None and start > duration:
                    marker["start_time"] = await _prompt_corrected_timestamp(
                        marker, "start_time", formatted_start, formatted_duration, duration
                    )
                if end is not None and end > duration:
                    marker["end_time"] = await _prompt_corrected_timestamp(
                        marker, "end_time", formatted_end, formatted_duration, duration
                    )
                fixed_markers.append(marker)
                break

            if choice == "r":
                logger.info("Removed marker id={}", marker.get("id"))
                break

            logger.error("Invalid choice. Use F/R/S.")

    return fixed_markers or None


async def _prompt_corrected_timestamp(marker: dict, field: str, formatted_current: str, formatted_duration: str, duration: float) -> int:
    """
    Repeatedly prompt for a corrected HH:MM:SS timestamp for one marker
    field until a value within the video's duration is given.

    Args:
        marker: The marker dict being corrected (used only for its title in
            log messages).
        field: "start_time" or "end_time" - used only for log messages.
        formatted_current: The current (invalid) value, formatted for
            display.
        formatted_duration: The file/scene duration, formatted for display.
        duration: The file's actual duration in seconds, as the upper bound.

    Returns:
        The corrected timestamp in seconds.
    """
    while True:
        logger.warning(
            "Marker '{}' {} ({}) exceeds duration ({}).",
            marker.get("title"), field, formatted_current, formatted_duration,
        )
        await asyncio.sleep(0.5)
        new_value_str = (await prompt(f"Enter corrected {field} in HH:MM:SS\n")).strip()
        try:
            h, m, s = map(int, new_value_str.split(":"))
            new_value = h * 3600 + m * 60 + s
            if new_value > duration:
                logger.error(f"{field} still exceeds video duration.")
                continue
            return new_value
        except (ValueError, TypeError):
            logger.error("Invalid timestamp. Format must be HH:MM:SS.")


async def maybe_add_scene_number(
        title: str,
        title_ignore_strings,
        send_notification: NotifyFn,
        remove_ignored_strings,
) -> str:
    """
    Interactively offer to append a scene number (e.g. "- Scene 1") to the
    title, previewing the cleaned result before it's accepted.

    Args:
        title: Current scene title.
        title_ignore_strings: Strings to strip from the title, passed to
            ``remove_ignored_strings`` for the preview.
        send_notification: Optional async push-notification callback.
        remove_ignored_strings: Async callable ``(text, ignore_strings) -> str``.

    Returns:
        The (possibly updated) title.
    """
    while True:
        await notify_and_pause(send_notification, "User input required - Add Scene Number")
        logger.info(
            f"This is the current title, would you like to add a scene number to it? e.g. ' - Scene 1'\n"
            f"The following strings will be removed from the title afterwards: {title_ignore_strings}\n"
            f"Current title: {title}\n"
            f"Enter a scene number, or press Enter to skip:"
        )
        raw = (await prompt("> ")).strip()

        if raw == "":
            logger.info("Skipping scene number addition.")
            return title

        preview_title = f"{title} {raw}"
        preview_clean = await remove_ignored_strings(preview_title, title_ignore_strings)

        logger.info(
            f"\nPreview of resulting title:\n"
            f"  {preview_clean}\n"
            f"\n  [a] Approve  [r] Restart  [s] Skip (don't add scene number)"
        )
        await notify_and_pause(send_notification, "User input required - Confirm Scene Number")
        confirm = (await prompt("> ")).strip().lower()

        if confirm in ("a", "approve"):
            logger.info(f"Scene number accepted. Title set to: {preview_title}")
            return preview_title
        if confirm in ("s", "skip"):
            logger.info("Skipping scene number addition.")
            return title
        if confirm in ("r", "restart"):
            logger.info("Restarting scene number input...")
            continue
        logger.warning("Unrecognised input, restarting scene number input...")


async def approve_scene_date(scene_date: str, send_notification: NotifyFn) -> str:
    """
    Interactively approve or replace a scene's date.

    Args:
        scene_date: Current date as "YYYY-MM-DD".
        send_notification: Optional async push-notification callback.

    Returns:
        The approved or replaced date string.
    """
    while True:
        await notify_and_pause(send_notification, "User input required - Date Approval")
        logger.info(
            f"Date approval required.\n"
            f"Current date: {scene_date}\n"
            f"Press Enter to approve, or type a new date to replace it (format: YYYY-MM-DD):"
        )
        raw = (await prompt("> ")).strip()

        if raw == "":
            logger.info("Date approved as-is.")
            return scene_date

        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Invalid date format '{raw}', expected YYYY-MM-DD. Please try again.")
            continue

        logger.info(
            f"\nPreview of new date:\n"
            f"  {raw}\n"
            f"\n  [a] Approve  [r] Restart  [s] Skip (keep original date)"
        )
        await notify_and_pause(send_notification, "User input required - Confirm Date")
        confirm = (await prompt("> ")).strip().lower()

        if confirm in ("a", "approve"):
            logger.info(f"New date accepted: {raw}")
            return raw
        if confirm in ("s", "skip"):
            logger.info("Skipping date change, keeping original.")
            return scene_date
        if confirm in ("r", "restart"):
            logger.info("Restarting date input...")
            continue
        logger.warning("Unrecognised input, restarting date input...")


async def approve_scene_title(
        title: str,
        title_ignore_strings,
        send_notification: NotifyFn,
        remove_ignored_strings,
) -> str:
    """
    Interactively approve or replace a scene's title.

    Args:
        title: Current scene title.
        title_ignore_strings: Strings to strip from a replacement title,
            passed to ``remove_ignored_strings`` for the preview.
        send_notification: Optional async push-notification callback.
        remove_ignored_strings: Async callable ``(text, ignore_strings) -> str``.

    Returns:
        The approved title. Note: if approved after a replacement, the
        *cleaned* preview is what's returned (matching the original
        behavior); if approved as-is, the original title is returned
        unmodified (also matching the original).
    """
    while True:
        await notify_and_pause(send_notification, "User input required - Title Approval")
        logger.info(
            f"Title approval required.\n"
            f"Current title: {title}\n"
            f"Press Enter to approve, or type a new title to replace it:"
        )
        raw = (await prompt("> ")).strip()

        if raw == "":
            logger.info("Title approved as-is.")
            return title

        preview_clean = await remove_ignored_strings(raw, title_ignore_strings)

        logger.info(
            f"\nPreview of new title:\n"
            f"  {preview_clean}\n"
            f"\n  [a] Approve  [r] Restart  [s] Skip (keep original title)"
        )
        await notify_and_pause(send_notification, "User input required - Confirm Title")
        confirm = (await prompt("> ")).strip().lower()

        if confirm in ("a", "approve"):
            logger.info(f"New title accepted: {preview_clean}")
            return preview_clean
        if confirm in ("s", "skip"):
            logger.info("Skipping title change, keeping original.")
            return title
        if confirm in ("r", "restart"):
            logger.info("Restarting title input...")
            continue
        logger.warning("Unrecognised input, restarting title input...")
