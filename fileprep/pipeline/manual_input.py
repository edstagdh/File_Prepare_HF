"""Interactive input collection (full manual mode, user-input filename)."""
import asyncio
from datetime import datetime
from loguru import logger
from fileprep.runtime import prompt, prompt_list
from fileprep.utils.text import INVALID_CHARS, clean_filename, is_valid_filename


async def ainput(text: str, multiline: bool = False) -> str:
    """Ask one question through the active runtime (web UI inside a job, terminal otherwise)."""
    return await prompt(text, title="Manual input", multiline=multiline)


async def collect_list_input(title: str, mode, validate=False):
    label = {"performers": "Performer", "tags": "Tag", "suffix": "Suffix part"}.get(mode, "Entry")
    while True:
        items, invalid = [], []
        for entry in await prompt_list(f"Enter the {title}", title="Manual input", item_label=label):
            # Optional filename-like validation
            if validate and not is_valid_filename(entry):
                invalid.append(entry)
                continue
            items.append((entry, None) if mode == "performers" else entry)
        if not invalid:
            return items
        for entry in invalid:
            bad = [c for c in entry if c in INVALID_CHARS]
            logger.error(f"Invalid characters {bad} in entry: {entry!r}. Please enter the list again.")


async def full_manual_mode_input(file_base_name, manual_mode_ask_suffix):
    await asyncio.sleep(1)
    logger.info(f"Full Manual Mode for file: {file_base_name}")

    # --- site with validation ---
    while True:
        site = (await ainput("[REQUIRED]Enter site:\n")).strip()

        # Empty check (critical field)
        if not site:
            logger.error("Site cannot be empty.")
            continue

        # Filename validation
        if is_valid_filename(site):
            break

        bad = [c for c in site if c in INVALID_CHARS]
        logger.error(f"Invalid characters in site: {bad}")
        logger.error("Please enter a valid site name.")

    # --- scene_date (must be YYYY-MM-DD or the literal none/None) ---
    while True:
        scene_date = (await ainput("[REQUIRED]Enter scene date (YYYY-MM-DD or none):\n")).strip()

        # Accept None / none (meaning: no date available)
        if scene_date.lower() == "none":
            scene_date = None
            break

        # Validate strict YYYY-MM-DD
        try:
            datetime.strptime(scene_date, "%Y-%m-%d")
            break
        except ValueError:
            logger.error("Invalid date format. Use YYYY-MM-DD or 'none'.")

    # --- scene_title with validation ---
    while True:
        scene_title = (await ainput("[REQUIRED]Enter new title:\n")).strip()

        # Check for empty
        if not scene_title:
            logger.error("Title cannot be empty.")
            continue

        # Check filename validity
        if is_valid_filename(scene_title):
            break

        bad = [c for c in scene_title if c in INVALID_CHARS]
        logger.error(f"Invalid characters in title: {bad}")
        logger.error("Please enter a valid title.")

    performers_names = await collect_list_input(
        "performers",
        mode="performers",
        validate=True
    )

    # --- scene_description ---
    scene_description = (await ainput("Enter scene description(blank for none):\n")).strip()

    # --- scene_tags (one per line, no validation needed) ---
    scene_tags = await collect_list_input("tags", mode="tags", validate=True)

    # --- optional multi-part suffix input ---
    suffix = ""
    if manual_mode_ask_suffix:
        while True:
            suffix_parts = await collect_list_input("suffix parts", mode="suffix", validate=True)
            if suffix_parts:
                break
            logger.warning("ask_manual_suffix is enabled but no suffix was entered.")

        # Transform each part: uppercase first letter, keep rest the same
        suffix_parts = [part[:1].upper() + part[1:] if part else part for part in suffix_parts]

        # Join with dots and preserve your leading dot
        suffix = "." + ".".join(suffix_parts)

    return {
        "scene_title": scene_title,
        "performers_names": performers_names,
        "image_url": None,
        "slug": None,
        "scene_url": None,
        "tpdb_image_url": None,
        "tpdb_site": site,
        "site_studio": None,
        "scene_description": scene_description,
        "scene_date": scene_date if scene_date else "0000-00-00",
        "scene_tags": scene_tags,
        "suffix": suffix
    }


async def get_selected_filename(
        bad_words,
        max_filename_length,
        send_notification
):
    """
    Prompt the user for a title, clean it, validate it,
    and return selected_filename.
    """

    while True:
        try:
            # Send notification
            try:
                result = await send_notification(
                    "INFO - User input is required."
                )

                if not result:
                    logger.warning(
                        "Notifier failed to send user input request."
                    )

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.exception(
                    "Failed to send notification: {}",
                    e
                )

            # Get user input
            try:
                scene_filename = (await prompt("Enter user input for filename: ")).strip()

            except Exception as e:
                logger.exception(
                    "Failed to read user input: {}",
                    e
                )
                continue

            # Validate raw input
            if not scene_filename:
                logger.warning(
                    "User entered an empty title."
                )

                logger.error(
                    "Invalid input. Please try again."
                )

                continue

            # Clean input
            try:
                clean_user_input = await clean_filename(
                    scene_filename,
                    bad_words,
                    mode=2
                )

                logger.info(
                    "Cleaned filename: {}",
                    clean_user_input
                )

            except Exception as e:
                logger.exception(
                    "Failed to clean filename: {}",
                    e
                )
                continue

            # Validate cleaned input
            if not clean_user_input:
                logger.warning(
                    "clean_user_input is empty after cleaning."
                )

                logger.error(
                    "Invalid input. Please try again."
                )

                continue

            # Validate filename length
            if len(clean_user_input) > max_filename_length:
                logger.warning(
                    "Filename exceeds max length "
                    "({} > {}).",
                    len(clean_user_input),
                    max_filename_length
                )

                logger.error(
                    "Filename is too long ({} / {}). "
                    "Please try again.",
                    len(clean_user_input),
                    max_filename_length
                )

                continue

            # Final variable
            selected_filename = clean_user_input

            logger.success(
                "Selected filename: {}",
                selected_filename
            )

            return selected_filename

        except Exception as e:
            logger.exception(
                "Unexpected error in get_selected_filename: {}",
                e
            )
