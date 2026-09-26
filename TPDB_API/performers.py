"""
Everything performer-related: extracting the acceptable-gender performers
from a scene entry, manual performer entry prompts, and fetching a
performer's profile pictures.

Terminology: a performer entry with no "parent" is a bare performer-site
record - a performer as extracted from one specific site. When the same
person appears on multiple sites, those site-specific records get linked
to a single canonical "parent" performer shared across sites. A performer
without a parent simply hasn't been linked yet (the server may attach one
later), which is why we treat that case cautiously in a few places below.
"""

import asyncio
import re
from typing import List, Optional, Tuple

from loguru import logger

from .api_client import send_request
from .prompt_utils import NotifyFn, notify_and_pause, parse_keep_selection, prompt

PerformerEntry = Tuple[str, str]  # (display_name, performer_id)


def _clean_name(name: str) -> str:
    """
    Strip 'ID <digits>' tokens (case-insensitive) from a name and collapse
    any resulting double spaces. Example: "Jane Doe ID 123" -> "Jane Doe".
    """
    cleaned_name = re.sub(r"\bID\s*\d+\b", "", name, flags=re.IGNORECASE).strip()
    return re.sub(r"\s{2,}", " ", cleaned_name)


def _build_entry(performer: dict, ignore_performer_id: bool) -> PerformerEntry:
    """
    Build a ``(display_name, performer_id)`` tuple for a performer that has
    been determined to belong in the output list.

    Handles both the "no alias" case (the credited name matches the parent
    performer's canonical name) and the "alias" case, where the credited
    name is formatted as "Parent Name(Alias)". If the performer has no
    parent record at all - i.e. this is a bare performer-site record, a
    performer as extracted from one specific site, not yet linked to a
    canonical parent performer shared across sites - falls back to the
    performer's own top-level "id"/"name" with no alias formatting -
    there's no parent name to compare against.

    Args:
        performer: A single entry from a scene's "performers" list. Usually
            has a "name" and a "parent" dict with "id"/"name", but the
            parent may be absent.
        ignore_performer_id: If True, strip "ID <digits>" tokens from the
            credited (non-alias) name before returning it.

    Returns:
        A ``(display_name, performer_id)`` tuple, falling back to
        "Invalid Name" / "Unknown" when a usable name can't be determined.
    """
    parent = performer.get("parent")

    if not parent:
        # No parent record to compare against or attribute to - use the
        # performer's own id/name directly, no alias formatting possible.
        own_id = performer.get("id", "")
        own_name = performer.get("name", "Unknown")
        if not ignore_performer_id:
            return own_name, own_id
        cleaned = _clean_name(own_name)
        return (cleaned or "Invalid Name"), own_id

    parent_id = parent.get("id", "")
    parent_name = parent.get("name", "Unknown")
    credited_name = performer.get("name", "Unknown")

    if credited_name == parent_name:
        # No alias used.
        if not ignore_performer_id:
            return credited_name, parent_id
        cleaned = _clean_name(credited_name)
        return (cleaned or "Invalid Name"), parent_id

    # Alias used - always cleaned of ID tokens regardless of
    # ignore_performer_id (preserved from the original behavior).
    alias = _clean_name(performer.get("name", ""))
    if not alias:
        logger.warning("No valid alias found for this performer.")
        return parent_name, parent_id
    return f"{parent_name}({alias})", parent_id


DEFAULT_ACCEPTABLE_GENDERS = {"Female", "Transgender Female"}


def _classify_gender(
        parent_gender: Optional[str],
        own_gender: Optional[str],
        acceptable_genders,
) -> str:
    """
    Decide what to do with a performer given two gender signals: their
    canonical parent record's "parent.extras.gender" (authoritative, since
    it comes from the main performer data) and their own record's
    "extra.gender" (often unset/alias-specific).

    The parent's value is the only thing consulted when it's known - it is
    never overridden or second-guessed by the performer's own value:

    - Parent gender known and in ``acceptable_genders`` -> include.
    - Parent gender known and NOT in ``acceptable_genders`` -> skip.

    The performer's own value is only consulted as a fallback when the
    parent's gender is unknown (parent missing entirely, or its gender is
    null):

    - Parent gender unknown, own gender known and in ``acceptable_genders``
      -> include.
    - Parent gender unknown, and the own gender is also unknown or not in
      ``acceptable_genders`` -> ask the user whether to still use this
      performer.

    Args:
        parent_gender: The parent record's "extras.gender" value, or None
            if the performer has no parent record (a bare performer-site
            entry not yet linked to a cross-site parent performer) or the
            field is unset.
        own_gender: The performer's own "extra.gender" value, or None.
        acceptable_genders: Collection of gender strings to auto-include
            (checked with ``in``, so any container works - a set is
            fastest).

    Returns:
        "auto_include", "skip", or "ask".
    """
    if parent_gender is not None:
        return "auto_include" if parent_gender in acceptable_genders else "skip"

    if own_gender is not None and own_gender in acceptable_genders:
        return "auto_include"

    return "ask"


async def _confirm_performer_inclusion(
        performer: dict,
        context_message: str,
        ignore_performer_id: bool,
        send_notification: NotifyFn,
) -> Optional[PerformerEntry]:
    """
    Prompt the user to decide whether a performer should be included,
    showing ``context_message`` so they know why a decision they might
    expect to be automatic still needs a human call.

    Args:
        performer: The performer entry being classified.
        context_message: Short explanation shown in the prompt (e.g. what
            gender info is on file, or why it's being asked despite a
            gender match).
        ignore_performer_id: Passed through to ``_build_entry``.
        send_notification: Optional async push-notification callback.

    Returns:
        A ``(name, id)`` tuple if the user confirms "yes"/"y", else None.
    """
    await notify_and_pause(send_notification, f"User input required - Approve Performer ({context_message})")
    logger.info("User input required - Approve Performer")
    name = performer.get("name", "Unknown")
    user_input = (await prompt(f"Treat performer '{name}' as included? ({context_message}) (yes/no): ")).strip().lower()
    if user_input in ("yes", "y"):
        return _build_entry(performer, ignore_performer_id)
    return None


async def extract_female_performers(
        selected_entry: dict,
        api_mode_url: str,
        filename_ignore_performer_ID: bool,
        send_notification: NotifyFn,
        mode: int = 1,
        acceptable_genders=None,
) -> Optional[List[PerformerEntry]]:
    """
    Extract the acceptable-gender (by default, female) performers from a
    scene entry.

    Walks ``selected_entry["performers"]``. Each performer's canonical
    parent record's "parent.extras.gender" is authoritative and is the
    only signal used when it's known: in ``acceptable_genders`` -> include,
    otherwise -> skip, with no confirmation prompt either way. The
    performer's own "extra.gender" is only consulted as a fallback when the
    parent has no known gender (parent missing, or its gender is null): if
    that own value is itself in ``acceptable_genders`` it's included
    automatically - *unless the performer has no parent record at all*
    (a bare performer-site entry - extracted from one specific site, not
    yet linked to a canonical parent performer shared across sites), in
    which case the user is asked to confirm anyway, since a parent may be
    attached by the server later and rewrite the performer's credited name.
    If the own value doesn't resolve it either, the user is prompted
    (optionally preceded by a push notification) to decide. See
    ``_classify_gender`` for the exact rules. Aliases are rendered as
    "Parent Name(Alias)"; ``filename_ignore_performer_ID`` controls whether
    "ID <digits>" tokens are stripped from non-alias names so the result is
    filename-safe.

    Depending on ``mode``, the extracted list is then optionally refined:

    - ``mode=1``: return the extracted list as-is.
    - ``mode=2``: ask the user which extracted performers to keep.
    - ``mode=3``: full interactive edit loop - keep/remove extracted
      performers and/or add new names manually, with an approve/restart
      confirmation step.

    If the final list is empty, falls back to asking the user directly via
    ``get_user_input_performers``.

    Args:
        selected_entry: Scene entry containing a "performers" list; each
            item optionally has a "parent" dict with "id", "name", and
            "extras" (including "gender"), and its own "extra" dict
            (including "gender").
        api_mode_url: API base URL, forwarded to ``get_user_input_performers``.
        filename_ignore_performer_ID: If True, strip "ID <digits>" tokens
            from performer names before use in filenames.
        send_notification: Optional async callback that sends a push
            notification whenever user input is required.
        mode: Extraction/editing mode - 1 (default), 2, or 3. See above.
        acceptable_genders: Collection of gender strings to auto-include.
            Defaults to ``DEFAULT_ACCEPTABLE_GENDERS`` (Female/Transgender
            Female) if not given.

    Returns:
        A sorted list of ``(name, performer_id)`` tuples, or None if no
        performers could be determined or an error occurred.
    """
    if acceptable_genders is None:
        acceptable_genders = DEFAULT_ACCEPTABLE_GENDERS

    try:
        female_performers: List[PerformerEntry] = []

        for performer in selected_entry.get("performers", []):
            parent = performer.get("parent")
            # A missing parent means this is a bare performer-site record -
            # a performer as extracted from one specific site, not yet
            # linked to a canonical parent performer shared across sites.
            # It's treated the same as a parent with an unknown ("null")
            # gender for classification purposes - it falls through to the
            # performer's own gender as a fallback rather than being
            # skipped outright.
            parent_gender = parent.get("extras", {}).get("gender") if parent else None
            own_gender = (performer.get("extra") or {}).get("gender")
            decision = _classify_gender(parent_gender, own_gender, acceptable_genders)

            if decision == "auto_include" and not parent:
                # The own gender matched, but there's no parent record at
                # all yet - this is a bare performer-site entry (extracted
                # from one specific site) not yet linked to a canonical
                # parent performer shared across sites. The server may
                # attach one later, which can rewrite this performer's
                # credited name (alias formatting, canonical name) - so
                # confirm before locking in a name that might not stick,
                # instead of auto-including.
                context = f"performer-site with no parent yet; own gender on file: {own_gender}"
                entry = await _confirm_performer_inclusion(
                    performer, context, filename_ignore_performer_ID, send_notification
                )
                if entry:
                    female_performers.append(entry)

            elif decision == "auto_include":
                female_performers.append(_build_entry(performer, filename_ignore_performer_ID))

            elif decision == "ask":
                context = f"own gender on file: {own_gender}" if own_gender else "no gender info on file"
                entry = await _confirm_performer_inclusion(
                    performer, context, filename_ignore_performer_ID, send_notification
                )
                if entry:
                    female_performers.append(entry)

            # decision == "skip" -> parent explicitly says not-acceptable; leave out silently.

        female_performers.sort()

        if mode == 2 and female_performers:
            await notify_and_pause(send_notification, "User input required - Select Relevant Performers")
            logger.info("Extracted performers for this scene:")
            for i, (name, _) in enumerate(female_performers, start=1):
                logger.info(f"  {i}. {name}")

            logger.info(
                "Enter the numbers of the performers to KEEP, separated by commas "
                "(e.g. 1,3) or 'all' to keep everyone:"
            )
            raw = (await prompt("> ")).strip().lower()
            keep = parse_keep_selection(raw, len(female_performers))
            if keep is not None:
                female_performers = [p for i, p in enumerate(female_performers, start=1) if i in keep]

        elif mode == 3:
            female_performers = await _edit_performers_interactively(female_performers, send_notification)

        if not female_performers:
            user_entries = await get_user_input_performers(selected_entry, api_mode_url, send_notification)
            if user_entries:
                female_performers.extend((name, "") for name in user_entries)
            if not female_performers:
                return None

        return female_performers

    except Exception as e:
        logger.error(f"Error extracting female performers: {str(e)}")
        return None


async def _edit_performers_interactively(
        female_performers: List[PerformerEntry],
        send_notification: NotifyFn,
) -> List[PerformerEntry]:
    """
    Mode-3 helper: full interactive edit loop for a scene's performer list -
    keep/remove extracted performers, add new names, and approve or restart.

    Args:
        female_performers: The extracted performers to start editing from.
        send_notification: Optional async push-notification callback.

    Returns:
        The final, sorted performer list once the user approves it.
    """
    original_performers = female_performers.copy()  # snapshot before any edits

    while True:
        await notify_and_pause(send_notification, "User input required - Edit Performers List")

        if female_performers:
            logger.info("Extracted performers for this scene:")
            for i, (name, _) in enumerate(female_performers, start=1):
                logger.info(f"  {i}. {name}")
        else:
            logger.info("No extracted performers for this scene.")

        logger.info(
            "Options:\n"
            "  Enter numbers to KEEP, separated by commas (e.g. 1,3) - others will be removed\n"
            "  [all]  Keep all extracted performers\n"
            "  [none] Remove all extracted performers\n"
            "  Then you will be prompted to add performers manually."
        )
        raw = (await prompt("> ")).strip().lower()

        if raw == "none":
            female_performers = []
        else:
            keep = parse_keep_selection(raw, len(female_performers))
            if keep is not None:
                female_performers = [p for i, p in enumerate(female_performers, start=1) if i in keep]
            # raw in ("all", "") -> keep list unchanged

        logger.info(
            "Enter additional performer names to add, one per line.\n"
            "Press Enter on an empty line when done (or immediately to add none):"
        )
        while True:
            new_name = (await prompt("> ")).strip()
            if new_name == "":
                break
            female_performers.append((new_name, ""))
            logger.info(f"Added performer: {new_name}")

        if female_performers:
            logger.info("Final performers list:")
            for i, (name, _) in enumerate(female_performers, start=1):
                logger.info(f"  {i}. {name}")
        else:
            logger.info("Performers list is empty.")

        logger.info("  [a] Approve  [r] Restart")
        await notify_and_pause(send_notification, "User input required - Confirm Performers List")
        confirm = (await prompt("> ")).strip().lower()

        if confirm in ("a", "approve"):
            logger.info("Performers list approved.")
            break
        if confirm in ("r", "restart"):
            logger.info("Restarting performers edit...")
        else:
            logger.warning("Unrecognised input, restarting performers edit...")
        female_performers = original_performers.copy()  # restore snapshot and loop again

    female_performers.sort()
    return female_performers


async def manual_performer_entry(send_notification: NotifyFn) -> List[PerformerEntry]:
    """
    Fully manual performer entry loop, used when the caller opts out of
    automatic extraction entirely.

    Args:
        send_notification: Optional async push-notification callback.

    Returns:
        A list of ``(name, "")`` tuples, one per entered name.
    """
    await asyncio.sleep(0.5)
    female_performers: List[PerformerEntry] = []
    while True:
        logger.info("Enter Performers Manually")
        await notify_and_pause(send_notification, "User input required - Manual Performer Entry")
        user_input = await prompt("Enter a value (or type 'exit' to stop): ")
        if user_input.lower() == "exit":
            break
        female_performers.append((user_input, ""))
    return female_performers


async def get_user_input_performers(
        selected_entry: dict,
        api_mode_url: str,
        send_notification: NotifyFn,
) -> Optional[List[str]]:
    """
    Ask the user whether they want to manually supply performer names for a
    scene, and if so, collect them one per line.

    Args:
        selected_entry: The scene entry, used to build a friendly prompt
            (title/slug/URL).
        api_mode_url: API base URL, used to build a scene link for the
            prompt (the leading "api." is stripped).
        send_notification: Optional async push-notification callback.

    Returns:
        A list of entered performer names, or None if the user declines.
    """
    scene_title = selected_entry.get("title", "(No title available)")
    scene_slug = selected_entry.get("slug", "(No scene available)")
    clean_mode_url = api_mode_url.replace("api.", "")
    scene_url = f"{clean_mode_url}/{scene_slug}"

    while True:
        try:
            await notify_and_pause(send_notification, "User input required - Manual Performer Entry")
            logger.info(f'User input required - Manual Performer Entry for scene: "{scene_title}" | {scene_url}')
            await asyncio.sleep(0.5)
            response = (await prompt("Do you want to provide Manual Performers? (yes/no): ")).strip().lower()

            if response in ("yes", "y"):
                while True:
                    temp_performers: List[str] = []  # reset the list at the start of each attempt
                    while True:
                        await asyncio.sleep(0.5)
                        name = (await prompt("Enter Performer (leave blank to finish, type 'restart' to start over): ")).strip()

                        if name.lower() == "restart":
                            print("Restarting performer entry...")
                            break  # break inner loop to start over

                        if not name:
                            break  # finish entering names

                        temp_performers.append(name)

                    if temp_performers and name.lower() != "restart":
                        return temp_performers

            elif response in ("no", "n"):
                return None
            else:
                logger.warning("Invalid input. Please enter 'yes' or 'no'.")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            return None


async def get_performer_profile_picture(
        performer_name: str,
        performer_id: str,
        posters_limit: int,
):
    """
    Query the performers API for a performer's poster images.

    Args:
        performer_name: Name of the performer (used for logging only).
        performer_id: Unique performer ID to query.
        posters_limit: Maximum number of poster URLs to return.

    Returns:
        A ``(poster_urls, performer_slug)`` tuple, or ``(None, None)`` on
        failure. Returns plain ``None`` (not a tuple) if ``performer_name``
        is falsy, matching the original guard clause.
    """
    from Utilities import load_credentials  # local import to avoid a module-level dependency cycle

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
                raw_data = await send_request(api_performers_url, api_auth, performer_id, max_retries, delay)

                if raw_data:
                    processed_data = await extract_performer_posters(raw_data, posters_limit)
                    performer_slug = raw_data.get("data", {}).get("slug", performer_id)
                    return processed_data, performer_slug

                logger.warning(f"No data returned for performer: {performer_name}")

            except Exception:
                logger.exception(f"Error occurred while requesting data for performer: {performer_name}")
                await asyncio.sleep(delay)

        logger.error(f"Failed to retrieve profile picture data after {max_retries} attempts for: {performer_name}")
        return None, None

    except Exception:
        logger.exception("An unexpected error occurred in get_performer_profile_picture")
        return None, None


async def extract_performer_posters(performer_data: dict, posters_limit: int) -> Optional[List[str]]:
    """
    Pull poster URLs out of a performer API payload, sorted by their
    declared order and capped to ``posters_limit``.

    Args:
        performer_data: Raw performer API response.
        posters_limit: Maximum number of poster URLs to return.

    Returns:
        A list of poster URLs, or None if there are none.
    """
    try:
        posters = performer_data.get("data", {}).get("posters", [])
        if not posters:
            return None

        sorted_posters = sorted(posters, key=lambda x: x.get("order", 0))
        poster_urls = [poster.get("url") for poster in sorted_posters if "url" in poster]
        return poster_urls[:posters_limit] if poster_urls else None

    except Exception:
        logger.exception("Error extracting poster URLs")
        return None
