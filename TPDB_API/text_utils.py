"""
Pure string/data transforms with no I/O: normalizing scene tags into
filename-safe slugs, converting numeric part-suffixes to words, and
stripping embedded dates out of filenames.
"""

import re
from typing import Optional

from loguru import logger
from num2words import num2words


async def extract_scene_tags(scene_data: dict) -> Optional[list]:
    """
    Normalize a scene's tag names into lowercase, dot-separated,
    filename-safe slugs.

    Args:
        scene_data: Scene API data with a "tags" list of {"name": ...}.

    Returns:
        A list of normalized tag strings, or None if there are none.
    """
    try:
        if not scene_data:
            return None

        scene_tags = []
        for tag in scene_data.get("tags", []):
            name = tag.get("name", "")
            name = re.sub(r"\(.*?\)", "", name)  # drop bracketed content
            name = name.strip()
            name = name.replace(" ", ".")
            name = re.sub(r"[^a-zA-Z0-9.]", "", name)  # drop remaining special chars
            name = name.lower()
            while ".." in name:
                name = name.replace("..", ".")
            scene_tags.append(name)

        return scene_tags or None

    except Exception:
        logger.exception("Error extracting scene tags")
        return None


async def convert_number_suffix_to_word(s: str) -> str:
    """
    Convert a numeric ".part.N" suffix into its word form, e.g.
    ".part.1" -> ".part.one".

    Args:
        s: Input string with a possible numeric suffix.

    Returns:
        The string with the suffix converted, or unchanged if there's no
        match.
    """
    match = re.search(r"(.*\.part\.)(\d+)$", s, re.IGNORECASE)
    if match:
        prefix, number = match.groups()
        return f"{prefix}{num2words(int(number))}"
    return s


async def remove_date_from_text(text: str) -> str:
    """
    Strip an embedded YY.MM.DD or YYYY.MM.DD date pattern out of a string
    and clean up any resulting duplicate/trailing dots.

    Args:
        text: Input string possibly containing an embedded date.

    Returns:
        The string with the date removed.
    """
    date_pattern = r"\b(?:\d{2}|\d{4})\.\d{2}\.\d{2}\b"
    cleaned = re.sub(date_pattern, "", text)
    cleaned = re.sub(r"\.{2,}", ".", cleaned).strip(".")
    return cleaned
