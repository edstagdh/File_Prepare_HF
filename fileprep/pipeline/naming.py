"""
Pure naming rules: file flags, suffixes, dates, the new file name and the video title.

Everything here is deterministic and free of I/O so the planner (preview / dry-run) and the runner
produce exactly the same names.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from fileprep.config.models import NamingSettings
from fileprep.utils.text import clean_filename, format_performers, sanitize_site_filename_part

FILE_FLAGS = ["vr2normal", "upscaled", "bts", "pov", "vertical", "trailer", "v2", "extended"]
# Flag -> suffix appended to the new name / title (first match wins, in this order)
FLAG_SUFFIXES = [("vr2normal", "VR2Normal"), ("bts", "BTS"), ("upscaled", "Upscaled"), ("vertical", "Vertical"),
                 ("pov", "Pov"), ("trailer", "Trailer"), ("v2", "V2"), ("extended", "Extended")]
NO_DATE = "0000-00-00"
# Sites whose entries legitimately have no parent studio (missing studio is not reported)
SITES_WITHOUT_STUDIO = ["onlyfans", "manyvids", "fansly"]


@dataclass
class FileFlags:
    flags: dict[str, bool]
    clean_name: str  # lower-cased file name without flag tokens (used for TPDB queries)

    @property
    def any(self) -> bool:
        return any(self.flags.values())

    @property
    def suffix(self) -> str:
        for flag, suffix in FLAG_SUFFIXES:
            if self.flags.get(flag):
                return suffix
        return ""


def detect_flags(file_name: str) -> FileFlags:
    """Detect flag tokens (exact '.'-separated tokens) and return the name without them."""
    parts = file_name.lower().split(".")
    flags = {flag: flag in parts for flag in FILE_FLAGS}
    return FileFlags(flags, ".".join(part for part in parts if part not in FILE_FLAGS))


def detect_pre_suffix(clean_name: str, naming: NamingSettings) -> str:
    """'.Part.N' and resolution tokens carried over from the original name (unless ignored)."""
    pre_suffix = ""
    match_part = re.search(r"\bPart[\s._-]*(\d+)\b", clean_name, re.IGNORECASE)
    if match_part and not naming.ignore_part_number:
        pre_suffix += f".Part.{match_part.group(1)}"
    match_res = re.search(r"\.(480p|720p|1080p|1440p|2160p)(?=[\W_]|$)", clean_name, re.IGNORECASE)
    if match_res and not naming.ignore_resolution_tag:
        # legacy behaviour: the leading dot of the part token is dropped when a resolution follows
        pre_suffix = pre_suffix.lstrip(".") + f".{match_res.group(1).lower()}"
    return pre_suffix


@dataclass
class SceneDate:
    year_full: str
    year: str
    month: str
    day: str
    pretty: str

    @property
    def has_date(self) -> bool:
        return self.year_full != "0000"


def format_scene_date(scene_date: str, title_date_format: str) -> SceneDate:
    year_full, month, day = scene_date.split("-")
    year = year_full[-2:]
    if scene_date == NO_DATE:
        return SceneDate(year_full, year, month, day, "")
    month_name = datetime.strptime(month, "%m").strftime("%B")
    year_token = year_full if title_date_format.startswith("YYYY") else year
    month_token = month_name if "Month" in title_date_format else month
    separator = "." if "." in title_date_format else "-"
    return SceneDate(year_full, year, month, day, separator.join([year_token, month_token, day]))


@dataclass
class NameCandidates:
    title: str
    names: str
    both: str
    tpdb_id: str


async def build_name_candidates(site: str, date: SceneDate, performers, scene_title: str, tpdb_id,
                                pre_suffix: str, manual_suffix: str, strip_words: list[str]) -> NameCandidates:
    formatted_site = await sanitize_site_filename_part(site)
    performer_part = await format_performers(performers, 2)
    safe_title = await clean_filename(scene_title, strip_words, mode=2)
    prefix = f"{formatted_site}.{date.year}.{date.month}.{date.day}." if date.has_date else f"{formatted_site}."
    title_name = f"{prefix}{safe_title}{pre_suffix}{manual_suffix}"
    names_name = f"{prefix}{performer_part}{pre_suffix}{manual_suffix}"
    both_name = f"{prefix}{performer_part}.{safe_title}{pre_suffix}{manual_suffix}"
    tpdb_name = f"{formatted_site}.{date.year}.{date.month}.{date.day}.{tpdb_id}"
    return NameCandidates(title_name, names_name, both_name, tpdb_name)


def select_filename(candidates: NameCandidates, mode: str, max_length: int) -> tuple[str, list[str]]:
    """Pick the name for ``mode`` with the legacy fallback chain. Returns (name, warnings).
    ``user_input`` is resolved by the caller."""
    chains = {
        "title": [("title", candidates.title), ("names", candidates.names)],
        "names": [("names", candidates.names), ("title", candidates.title)],
        "both": [("both", candidates.both), ("names", candidates.names), ("title", candidates.title)],
    }
    if mode == "tpdb_id":
        return candidates.tpdb_id, []
    if mode not in chains:
        raise ValueError(f"Invalid filename mode: {mode}")
    tried = []
    for label, name in chains[mode]:
        if len(name) <= max_length:
            warnings = [f"{'/'.join(tried)} file name longer than {max_length} characters, using {label}"] if tried else []
            return name, warnings
        tried.append(label)
    return candidates.tpdb_id, [f"{'/'.join(tried)} file names longer than {max_length} characters, using the TPDB id"]


SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def clean_suffix(value: str) -> str:
    """User suffix: dots/spaces at the ends removed, inner spaces become dots. Raises ValueError when invalid."""
    value = re.sub(r"\s+", ".", value.strip()).strip(".")
    if value and not SUFFIX_RE.match(value):
        raise ValueError("A suffix may only contain letters, digits, '.', '-' and '_'.")
    return value


def combine_suffixes(*parts: str) -> str:
    return ".".join(p for p in parts if p)


def full_file_name(base_name: str, suffix: str, extension: str) -> str:
    return f"{base_name}.{suffix}{extension}" if suffix else f"{base_name}{extension}"


def folder_name(base_name: str, suffix: str) -> str:
    return f"{base_name}.{suffix}" if suffix else base_name


async def build_title(site: str, studio: str | None, date: SceneDate, scene_title: str, performers,
                      suffix: str) -> tuple[str, str, list[str], bool]:
    """Video title. Returns (title, studio_info, studio_tags, has_performers)."""
    if site != studio and studio:
        studio_info, studio_tags = f"{site}({studio})", [site, studio]
    else:
        studio_info, studio_tags = site, [site]
    formatted_names = await format_performers(performers, mode=1)
    parts = [studio_info, date.pretty, scene_title, formatted_names]
    has_performers = bool(parts[-1])
    if not has_performers:
        parts.pop()
    if suffix:
        parts.append(suffix)
    return " - ".join(p for p in parts if p), studio_info, studio_tags, has_performers


def clean_scene_title(scene_title: str) -> str:
    if scene_title.endswith(" -"):
        scene_title = scene_title[:-2]
    if scene_title.endswith(" - "):
        scene_title = scene_title[:-3]
    return scene_title
