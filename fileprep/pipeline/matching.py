"""Resolve the scene metadata of one file (TPDB match or full manual input)."""

import re
from pathlib import Path

from loguru import logger

from fileprep.config.models import ProcessingSettings
from fileprep.media.metadata import get_existing_tpdb_uuid
from fileprep.pipeline.manual_input import full_manual_mode_input
from fileprep.pipeline.naming import SITES_WITHOUT_STUDIO, FileFlags, clean_scene_title
from fileprep.pipeline.plan import PlanIssue, SceneMatch
from fileprep.runtime import NotifyFn
from fileprep.tpdb import query_api
from fileprep.tpdb.scene_review import approve_studio
from fileprep.utils.text import validate_date

DESCRIPTION_NOT_FOUND = "Scene description not found"


def _err(code: str, message: str, file: str) -> PlanIssue:
    return PlanIssue(level="error", code=code, message=message, file=file)


async def resolve_match(file: Path, flags: FileFlags, settings: ProcessingSettings,
                        notify: NotifyFn) -> tuple[SceneMatch | None, list[PlanIssue]]:
    m, r = settings.matching, settings.review
    name = file.name
    issues: list[PlanIssue] = []
    override_uuid = m.uuid_overrides.get(name)
    existing_uuid = override_uuid or (await get_existing_tpdb_uuid(file) if m.rematch_using_embedded_uuid else None)
    if override_uuid:
        logger.info(f"Using the TPDB UUID set for this file: {override_uuid}")
    api_args = dict(generate_hf_template=settings.outputs.upload_template, jav_api_mode=m.api_mode == "jav",
                    movies_api_mode=m.api_mode == "movies", movies_scenes_mode=m.movie_entry_type,
                    date_approval=r.approve_date, title_approval=r.approve_title, performers_approval=r.approve_performers,
                    filename_ignore_performer_ID=settings.naming.ignore_performer_id, send_notification=notify,
                    existing_tpdb_uuid=existing_uuid, file=file, title_ignore_strings=settings.naming.title_strip_strings,
                    warn_length_match=r.warn_on_duration_mismatch, add_timestamps_markers=r.add_chapter_markers,
                    tpdb_try_match_oshash_before_parse=m.match_by_oshash_first)

    if m.matching_mode == "full_manual":
        logger.warning("Full manual mode selected, some features are disabled for this file.")
        data = await full_manual_mode_input(file.stem, settings.naming.ask_manual_suffix)
        match = SceneMatch(scene_title=data["scene_title"], performers=[list(p) for p in data["performers_names"]],
                           site=data["tpdb_site"], description=data["scene_description"], date=data["scene_date"],
                           tags=data["scene_tags"], manual_suffix=data.get("suffix", "") if settings.naming.ask_manual_suffix else "",
                           full_manual=True)
    elif m.matching_mode == "free_string_parse":
        query = flags.clean_name if flags.any else file.stem
        match = SceneMatch.from_api_tuple(await query_api(query, None, None, None, mode=1, **api_args))
    elif m.matching_mode == "strict":
        part_match = re.search(r"\.part\.\d+", file.stem, re.IGNORECASE)
        parts = file.stem.split(".")
        if len(parts) < 4:
            return None, [_err("E11_FORMAT", f"Invalid strict file name format: {file.stem}", name)]
        year, month, day = parts[1], parts[2], parts[3]
        valid, _ = await validate_date(year, month, day)
        if not valid:
            return None, [_err("E28_DATE", f"Invalid date in file name: {file.stem}", name)]
        scene_api_date = f"20{year}-{month}-{day}"
        match = SceneMatch.from_api_tuple(await query_api(flags.clean_name, scene_api_date, m.manual_performer_entry,
                                                          part_match, mode=2, **api_args))
    else:
        return None, [_err("E47_MODE", f"Invalid matching mode: {m.matching_mode}", name)]

    critical = {"scene_title": match.scene_title, "tpdb_id": match.tpdb_id, "performers": match.performers,
                "image_url": match.image_url, "slug": match.slug, "scene_url": match.scene_url,
                "tpdb_image_url": match.tpdb_image_url, "site": match.site, "studio": match.studio,
                "description": match.description}
    if not match.full_manual and all(v is None for v in critical.values()):
        # Not blocking: the run skips this file and alerts. Connection failures raise TpdbConnectionError instead.
        hint = ("The UUID set for this file returned nothing - check it." if override_uuid else
                "Search the scene manually on TPDB and use 'Set TPDB UUID', or 'Move out' to take it out of this job.")
        return None, [PlanIssue(level="warning", code="E38_NO_MATCH", file=name,
                                message=f"No TPDB match found - this file will be skipped. {hint}")]
    missing = [k for k, v in critical.items() if v is None
               and not (k == "studio" and match.site and any(s in match.site.lower() for s in SITES_WITHOUT_STUDIO))
               and not (match.full_manual and k in ("tpdb_id", "image_url", "slug", "scene_url", "tpdb_image_url", "studio"))]
    if missing:
        issues.append(PlanIssue(level="warning", code="MISSING_METADATA", file=name,
                                message=f"Missing metadata: {', '.join(missing)}"))
    if not match.description:
        match.description = DESCRIPTION_NOT_FOUND
        if settings.outputs.upload_template:
            issues.append(PlanIssue(level="warning", code="NO_DESCRIPTION", file=name,
                                    message="Scene description not found - update it manually in the template."))
    if not match.scene_title or match.scene_title == "Multiple results":
        return None, issues + [_err("NO_TITLE", "Missing or ambiguous title from the API", name)]
    match.scene_title = clean_scene_title(match.scene_title)
    if not match.site:
        return None, issues + [_err("NO_SITE", "No site/studio returned for this scene", name)]
    if r.approve_studio and not match.full_manual:
        match.studio, match.studio_mode = await approve_studio(match.site, match.studio, settings.naming.studio_mode, notify)
    if settings.naming.filename_mode in ("names", "both") and (not match.performers or match.performers == "Invalid"):
        return None, issues + [_err("NO_PERFORMERS", "Missing or invalid performers (required by the file name mode)", name)]
    return match, issues
