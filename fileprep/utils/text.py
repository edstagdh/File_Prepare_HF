"""Filename / title / performer text normalisation and validation helpers."""
import os
import re
from datetime import datetime
from loguru import logger

CLEAN_CHARS = "!@#$%^&*()_~|+=’/' :\\?"

INVALID_CHARS = set('\\:*/?"<>|’')


async def replace_episode_tag(filename: str) -> str:
    """
    Replace .E##. with .00.00.00.Episode.##. in the filename.
    Supports episode numbers from 1 to 9999.
    """
    return re.sub(r'\.E(\d{1,4})\.', r'.00.00.00.Episode.\1.', filename)


async def clean_filename(input_string: str, bad_words: list, mode: int) -> str:
    """Removes unwanted characters and standardizes casing rules."""

    if mode == 1:
        base_name, extension = os.path.splitext(input_string)
        # Remove unwanted parts in filename
        base_name = re.sub(re.escape("H265_"), "", base_name, flags=re.IGNORECASE)
        # base_name = await replace_episode_tag(base_name)
        base_name = re.sub(re.escape(".Xxx.1080p.Hevc.X265.Prt"), "", base_name, flags=re.IGNORECASE)
        base_name = re.sub(re.escape(".Xxx.repack.1080p.Hevc.X265.Prt"), "", base_name, flags=re.IGNORECASE)
        base_name = re.sub(re.escape(".Xxx.1080p.Mp4-ktr"), "", base_name, flags=re.IGNORECASE)
        for word in bad_words:
            base_name = re.sub(re.escape(word), "", base_name, flags=re.IGNORECASE)
            logger.debug(base_name)
        # Remove unwanted characters
        base_name = base_name.translate(str.maketrans("", "", CLEAN_CHARS))

        # Capitalize segments after the first
        parts = base_name.split('.')
        for i in range(1, len(parts)):
            if parts[i].lower() not in ["and", "vr2normal", "bts"]:
                parts[i] = parts[i].capitalize()
        return '.'.join(parts) + extension

    elif mode == 2:
        # Remove unwanted characters
        clean_title = input_string.replace(", ", ".")
        clean_title = clean_title.replace("-", " ")

        while "  " in clean_title:
            clean_title = clean_title.replace("  ", " ")

        clean_title = clean_title.replace(" ", ".")

        clean_title = clean_title.translate(
            str.maketrans("", "", CLEAN_CHARS)
        )

        while ".." in clean_title:
            clean_title = clean_title.replace("..", ".")

        clean_title = clean_title.strip(".")

        return clean_title
    else:
        return ""


async def sanitize_site_filename_part(input_str):
    """
    Sanitize a string by removing disallowed characters and replacing dots with spaces.

    Args:
        input_str (str): The original string to sanitize.

    Returns:
        str: The sanitized string.
    """
    translation_table = str.maketrans("", "", ":!@#$%^&*()_+='’ ")
    sanitized = input_str.replace(":", "-").replace(".", " ").replace("/", "-")
    sanitized = sanitized.translate(translation_table)
    return sanitized


async def is_valid_filename_format(filename: str) -> bool:
    """Validates filename against expected format."""
    base = os.path.splitext(filename)[0]
    return bool(re.fullmatch(
        r'([A-Za-z0-9\-!]+)\.(\d{2})\.(\d{2})\.(\d{2})\.([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)',
        base
    ))


async def is_valid_part(value, mode):
    """Checks if a specific part of the filename is valid based on the mode."""
    if mode == "year":
        # Year should be a 2-digit number (e.g., 23 for 2023)
        return len(value) == 2 and value.isdigit()

    elif mode == "month":
        # Month should be between 01 and 12
        return len(value) == 2 and value.isdigit() and 1 <= int(value) <= 12

    elif mode == "day":
        # Day should be between 01 and 31 (could be validated more depending on month)
        return len(value) == 2 and value.isdigit() and 1 <= int(value) <= 31

    else:
        # Invalid mode (only year, month, or day are supported)
        return False


async def validate_date(year, month, day):
    """Validates if the date (year, month, day) is a valid calendar date and checks each part."""
    # Check if each part is valid
    if not await is_valid_part(year, "year"):
        return False, 25  # Invalid year format (exit code 25)
    if not await is_valid_part(month, "month"):
        return False, 26  # Invalid month format (exit code 26)
    if not await is_valid_part(day, "day"):
        return False, 27  # Invalid day format (exit code 27)

    # Now check if the date is a valid calendar date
    try:
        # Use the 'YY-MM-DD' format for validation
        date = datetime.strptime(f"{year}-{month}-{day}", "%y-%m-%d")
        return True, 0  # Success (exit code 0)
    except ValueError:
        return False, 28  # Invalid date (exit code 28)


async def format_performers(performers, mode):
    """
    Format a list of performer names based on the mode.

    Args:
        performers (list): List of performer names.
        mode (int): Formatting mode (1 = title, 2 = filename).

    Returns:
        str: Formatted performer string.
    """
    if not performers or performers is None:
        return ""

    unique_performers = sorted(set(p[0] for p in performers))

    if mode == 1:  # Title (allow spaces)
        translation_table = str.maketrans("", "", "!@#$%^&*_+='")
        sanitized_performers = [
            p.translate(translation_table).replace(".", " ") for p in unique_performers
        ]
        return ", ".join(sanitized_performers)

    elif mode == 2:  # Filename (replace spaces with dots, then sanitize)
        translation_table = str.maketrans("", "", "!@#$%^&*()_+='")
        sanitized_performers = []
        for p in unique_performers:
            # Remove anything inside parentheses and the parentheses themselves
            p = re.sub(r"\s*\([^)]*\)", "", p)

            # Replace spaces with dots and sanitize
            p = p.replace(" ", ".")
            p = p.translate(translation_table)
            p = re.sub(r"\.{2,}", ".", p)
            sanitized_performers.append(p)

        return ".and.".join(sanitized_performers)

    elif mode == 3:  # Performers Images Json
        translation_table = str.maketrans("", "", "!@#$%^&*()_+='")
        sanitized_performers = []
        for p in unique_performers:
            # Remove anything inside parentheses and the parentheses themselves
            p = re.sub(r"\s*\([^)]*\)", "", p)
            p = p.translate(translation_table)
            sanitized_performers.append(p)

        return sanitized_performers

    else:
        logger.error("Error: Unrecognized mode passed to format_performers.")
        return ""


def is_valid_filename(s: str) -> bool:
    return not any(c in INVALID_CHARS for c in s)


async def remove_ignored_strings(title: str, title_ignore_strings: list[str]) -> str:
    original_title = title

    try:
        for s in title_ignore_strings:
            if s:
                title = title.replace(s, "").strip()

        if not title:
            logger.error(
                "Title became empty after removing ignore strings. "
                "Original title: {!r} | Ignore strings: {!r}",
                original_title,
                title_ignore_strings,
            )
            return original_title

        return title

    except Exception as e:
        logger.exception(
            "Failed while removing ignore strings from title. "
            "Original title: {!r} | Ignore strings: {!r}",
            original_title,
            title_ignore_strings,
        )
        return original_title
