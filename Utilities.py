import json
import os
import re
import subprocess
import sys
import tempfile
import asyncio
from datetime import datetime
from loguru import logger
from pymediainfo import MediaInfo
from typing import Union, Sequence, Tuple

CLEAN_CHARS = "!@#$%^&*()_+=’' :?"
INVALID_CHARS = set('\\/:*?"<>|')
RUN_DEBUG_MODE = False


async def run_command(command: Union[str, Sequence[str]]) -> Tuple[str, str, int]:
    """
    Execute a command and return (stdout, stderr, code).
    - Accepts either a list/tuple (recommended) or a string.
    - Tries asyncio subprocess APIs first (non-blocking). If they are unsupported
      on the current event loop (Windows selectors), falls back to running the
      blocking subprocess in a thread to avoid blocking the loop.
    - Returns rc == 0 as 0, non-zero as 22, exceptions as 99 (to match your existing codes).
    """

    # Normalize & choose shell mode
    is_sequence = isinstance(command, (list, tuple))
    use_shell = not is_sequence  # string -> shell, list -> exec
    cmd_for_log = ' '.join(command) if is_sequence else command

    try:
        if RUN_DEBUG_MODE:
            logger.debug(f"run_command using asyncio subprocess: {cmd_for_log!r}")
        if use_shell:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

        stdout_bytes, stderr_bytes = await proc.communicate()

        stdout = stdout_bytes.decode(errors='ignore').strip() if stdout_bytes else ''
        stderr = stderr_bytes.decode(errors='ignore').strip() if stderr_bytes else ''

        rc = proc.returncode
        if rc == 0:
            return stdout, stderr, 0
        else:
            return stdout, stderr, 22

    except Exception as exc:
        # Often on Windows you'll see NotImplementedError / RuntimeError for selector loops.
        if RUN_DEBUG_MODE:
            logger.debug(f"async subprocess failed ({exc!r}), falling back to threaded subprocess.run")

        try:
            def sync_run():
                if is_sequence:
                    # safer: pass list with shell=False
                    return subprocess.run(
                        list(command),
                        shell=False,
                        capture_output=True,
                        text=True,
                        errors='ignore'
                    )
                else:
                    # string -> run in shell (user asked for string)
                    return subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        errors='ignore'
                    )

            # Python 3.9+: use asyncio.to_thread, otherwise run_in_executor
            try:
                result = await asyncio.to_thread(sync_run)
            except AttributeError:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, sync_run)

            stdout = result.stdout.strip() if result.stdout else ''
            stderr = result.stderr.strip() if result.stderr else ''
            rc = result.returncode
            if rc == 0:
                return stdout, stderr, 0
            else:
                return stdout, stderr, 22

        except Exception as exc2:
            logger.exception(f"Fallback subprocess.run failed for command: {cmd_for_log!r}")
            return '', str(exc2), 99


async def verify_ffmpeg_and_ffprobe():
    # Define the minimum version required (e.g., 4.0.0)
    MIN_FFMPEG_VERSION = (4, 0, 0)
    MIN_FFMPEG_DATE = datetime(2024, 9, 1)

    async def check_ffmpeg_or_ffprobe(command, tool_name):
        try:
            stdout, stderr, code = await run_command(command)
            if code != 0:
                logger.error(f"{tool_name} returned a non-zero exit code.")
                logger.error(f"stderr: {stderr}")
                return False, 22  # Command failed

            version_ok = False
            date_ok = True  # assume OK unless proven otherwise

            # ---- Version Check ----
            output = stdout + '\n' + stderr
            if RUN_DEBUG_MODE:
                logger.debug(output)

            # ---- Version Check ----
            # Only accept semantic versions like 4.4.2
            version_match = re.search(r"version\s+(\d+)\.(\d+)\.(\d+)", output)

            # ---- Build Date Check ----
            # Match YYYY-MM-DD anywhere, including Git/Windows builds
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", output)

            if version_match:
                tool_version = tuple(map(int, version_match.groups()))
                if tool_version >= MIN_FFMPEG_VERSION:
                    version_ok = True
                else:
                    logger.error(
                        f"{tool_name} version {'.'.join(map(str, tool_version))} is too old. "
                        f"Minimum required: {'.'.join(map(str, MIN_FFMPEG_VERSION))}."
                    )
            else:
                if RUN_DEBUG_MODE:
                    logger.debug(f"Could not parse {tool_name} version from output.")
                version_ok = False

            # ---- Build Date Check ----
            if date_match:
                try:
                    tool_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                    if tool_date < MIN_FFMPEG_DATE:
                        date_ok = False
                        logger.error(
                            f"{tool_name} build date {tool_date.strftime('%Y-%m-%d')} is too old. "
                            f"Minimum required build date is {MIN_FFMPEG_DATE.strftime('%Y-%m-%d')}."
                        )
                except ValueError:
                    logger.warning(f"Invalid date format detected for {tool_name}: {date_match.group(1)}")

            # ---- Final Decision ----
            # Pass if either semantic version OR date meets the minimum
            if version_ok or date_ok:
                return True, 0
            else:
                logger.error(f"{tool_name} did not meet version/date requirements.")
                return False, 33  # Requirement failure

        except Exception:
            logger.exception(f"An exception occurred while verifying {tool_name}.")
            return False, 98

    # Run both checks
    ffmpeg_ok, ffmpeg_code = await check_ffmpeg_or_ffprobe(["ffmpeg", "-version"], "ffmpeg")
    ffprobe_ok, ffprobe_code = await check_ffmpeg_or_ffprobe(["ffprobe", "-version"], "ffprobe")

    if ffmpeg_ok and ffprobe_ok:
        return True, 0  # All good
    else:
        # Return the first failure code found (prioritizing ffmpeg)
        return False, ffmpeg_code if not ffmpeg_ok else ffprobe_code


async def load_json_file(file_name):
    try:
        with open(file_name, 'r') as config_file:
            json_data = json.load(config_file)
            return json_data, 0  # Success
    except FileNotFoundError:
        logger.error(f"{file_name} file not found.")
        return None, 20  # JSON file load error
    except KeyError as e:
        logger.error(f"Key {e} is missing in the {file_name} file.")
        return None, 21  # Missing keys in JSON
    except json.JSONDecodeError:
        logger.error(f"Error parsing {file_name}. Ensure the JSON is formatted correctly.")
        return None, 20  # JSON file load error
    except Exception:
        logger.exception(f"An unexpected error occurred while loading {file_name}.")
        return None, 99  # Unknown exception


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
        clean_title = clean_title.replace("  ", " ")
        clean_title = clean_title.replace("  ", " ")
        clean_title = clean_title.replace(" ", ".")
        clean_title = clean_title.translate(str.maketrans("", "", CLEAN_CHARS))
        clean_title = clean_title.replace("..", ".")
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
    translation_table = str.maketrans("", "", ":!@#$%^&*()_+=' ")
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


async def pre_process_files(directory, bad_words, matching_mode, mode):
    try:
        for filename in os.listdir(directory):
            if not filename.lower().endswith('.mp4'):
                continue
            if filename.lower().endswith('_old.mp4'):
                continue

            if matching_mode == "strict":
                if ' ' in filename:
                    logger.error(f"Filename contains spaces: '{filename}'. Please remove spaces before proceeding.")
                    return False, 12
                new_filename = await clean_filename(filename, bad_words, mode)
            else:
                new_filename = filename
            old_path = os.path.join(directory, filename)
            new_path = os.path.join(directory, new_filename)

            if old_path != new_path:
                try:
                    os.rename(old_path, new_path)
                    logger.info(f"Renamed: {filename} -> {new_filename}")
                except OSError as e:
                    logger.error(f"Failed to rename '{filename}' -> '{new_filename}': {e}")
                    return False, 13  # Exit code for rename failure

            if not await is_valid_filename_format(new_filename) and matching_mode == "strict":
                logger.error(f"Filename does not match required format: '{new_filename}'")
                return False, 14  # Exit code for bad format

        return True, 0  # Success

    except Exception:
        logger.exception("An unexpected error occurred while preprocessing files.")
        return False, 99  # General unexpected error


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


async def rename_file(file_path, new_filename):
    """
    Renames a file to a new filename without changing the path.

    Handles case-only renaming on case-insensitive file systems (e.g. Windows).

    Args:
        file_path (str): The full current path of the file (including filename and extension).
        new_filename (str): The new filename (including extension) to rename the file to.

    Returns:
        bool: True if renaming was successful, False otherwise.
    """
    try:
        directory = os.path.dirname(file_path)
        new_file_path = os.path.join(directory, new_filename)

        # If only the case is changing, do a two-step rename
        if os.path.abspath(file_path).lower() == os.path.abspath(new_file_path).lower() and file_path != new_file_path:
            temp_name = f"__temp__{next(tempfile._get_candidate_names())}" + os.path.splitext(new_filename)[1]
            temp_path = os.path.join(directory, temp_name)
            os.rename(file_path, temp_path)
            os.rename(temp_path, new_file_path)
        else:
            os.rename(file_path, new_file_path)

        logger.info(f"Renamed file: {file_path} -> {new_file_path}")
        return True, None

    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}, Error: {e}")
        return False, e
    except PermissionError as e:
        logger.error(f"Permission denied to rename file: {file_path}, Error: {e}")
        return False, e
    except OSError as e:
        logger.error(f"OS error occurred while renaming file {file_path}: {e}")
        return False, e
    except Exception as e:
        logger.exception(f"Unexpected error occurred while renaming file {file_path}, Error: {e}")
        return False, e


async def generate_mediainfo_file(input_file_full_path, output_path):
    try:
        # Split the full path into directory, file name, and extension
        _, file_name = os.path.split(input_file_full_path)
        file_base_name, _ = os.path.splitext(file_name)

        # Define the output text file name
        output_file = os.path.join(output_path, f"{file_base_name}_mediainfo.txt")

        # Check if the output file exists and delete it
        if os.path.exists(output_file):
            os.remove(output_file)
            # logger.debug(f"Existing mediainfo file {output_file} deleted.")

        # Use pymediainfo to parse the media file
        media_info_text = MediaInfo.parse(input_file_full_path, output="text", full=False)

        # Write the mediainfo output to the text file
        with open(output_file, 'w', encoding='utf-8', newline='') as file:
            file.write(media_info_text)

        # logger.debug(f"Mediainfo for {input_file_full_path} has been saved to {output_file}")

        return True

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return False


async def generate_template_video(
        new_title: str,
        scene_title: str,
        scene_pretty_date: str,
        scene_description: str,
        scene_performers: str,
        fps: float,
        resolution: str,
        is_vertical: bool,
        codec: str,
        extension: str,
        directory: str,
        new_filename_base_name: str,
        template_file_full_path: str,
        code_version: str,
        scene_tags: list,
        studio_tags: list,
        image_output_format: str,
        fill_img_urls: bool,
        imgbox_file_path: str,
        imgbb_file_path: str,
        hamster_file_path: str,
        suffix: str
) -> bool:
    media_info_file_path = os.path.join(directory, f"{new_filename_base_name}_mediainfo.txt")

    pattern = f"[{re.escape(CLEAN_CHARS)}]"

    if not os.path.isfile(template_file_full_path):
        raise FileNotFoundError(f"Template file not found: {template_file_full_path}")

    if not os.path.isfile(media_info_file_path):
        raise FileNotFoundError(f"Media info file not found: {media_info_file_path}")

    # Load the template content
    with open(template_file_full_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    # Load media info
    with open(media_info_file_path, "r", encoding="utf-8") as f:
        media_info = f.read()

    resources_img_host_url, _, _ = await load_credentials(7)

    # Load JSON config
    json_map, exit_code = await load_json_file("Resources/BBCode_Images.json")
    if exit_code != 0 or json_map is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Additional information
    _, template_name = os.path.split(template_file_full_path)
    template_base_name, _ = os.path.splitext(template_name)
    fps_icon_url = f"{resources_img_host_url}{json_map[str(fps)]}"
    resolution_icon_url = f"{resources_img_host_url}{json_map[resolution]}"
    codec_icon_url = f"{resources_img_host_url}{json_map[codec]}"
    extension_icon_url = f"{resources_img_host_url}{json_map[extension.replace('.', '')]}"
    desc_button_icon_url = f"{resources_img_host_url}{json_map['desc_button']}"
    release_date_button_icon_url = f"{resources_img_host_url}{json_map['release_date_button']}"
    performers_button_icon_url = f"{resources_img_host_url}{json_map['performers_button']}"
    mediainfo_button_icon_url = f"{resources_img_host_url}{json_map['mediainfo_button']}"
    screens_button_icon_url = f"{resources_img_host_url}{json_map['screens_button']}"
    bg = f"{resources_img_host_url}{json_map['bg']}"

    # Default image paths
    cover_image = f"{new_filename_base_name}.{image_output_format}"
    preview_sheet_image = f"{new_filename_base_name}_preview_sheet.webp"  # Hardcoded due to HF supported file hosting requirements(Either WEBP or GIF)
    thumbnails_image = f"{new_filename_base_name}_thumbnails.{image_output_format}"

    # Optionally fill URLs from imgbb(first), imgbox
    if imgbb_file_path != "":
        if fill_img_urls and os.path.isfile(imgbb_file_path):
            try:
                with open(imgbb_file_path, "r", encoding="utf-8") as f:
                    imgbb_data = json.load(f)

                thumbs_key = f"{new_filename_base_name} - thumbnails"
                cover_key = f"{new_filename_base_name} - cover"
                preview_sheet_key = f"{new_filename_base_name} - Preview Sheet WebP"

                if thumbs_key in imgbb_data and isinstance(imgbb_data[thumbs_key], list):
                    thumbs_entry = imgbb_data[thumbs_key][0]
                    if "direct_link" in thumbs_entry:
                        thumbnails_image = thumbs_entry["direct_link"]

                if cover_key in imgbb_data and isinstance(imgbb_data[cover_key], list):
                    cover_entry = imgbb_data[cover_key][0]
                    if "direct_link" in cover_entry:
                        cover_image = cover_entry["direct_link"]

                if preview_sheet_key in imgbb_data and isinstance(imgbb_data[preview_sheet_key], list):
                    preview_sheet_entry = imgbb_data[preview_sheet_key][0]
                    if "direct_link" in preview_sheet_entry:
                        preview_sheet_image = preview_sheet_entry["direct_link"]

            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                raise ValueError(f"Failed to parse imgbb file or missing expected data: {e}")
    elif imgbox_file_path != "":
        if fill_img_urls and os.path.isfile(imgbox_file_path):
            try:
                with open(imgbox_file_path, "r", encoding="utf-8") as f:
                    imgbox_data = json.load(f)

                thumbs_key = f"{new_filename_base_name} - thumbnails"
                cover_key = f"{new_filename_base_name} - cover"

                if thumbs_key in imgbox_data and isinstance(imgbox_data[thumbs_key], list):
                    thumbs_entry = imgbox_data[thumbs_key][0]
                    if "image_url" in thumbs_entry:
                        thumbnails_image = thumbs_entry["image_url"]

                if cover_key in imgbox_data and isinstance(imgbox_data[cover_key], list):
                    cover_entry = imgbox_data[cover_key][0]
                    if "image_url" in cover_entry:
                        cover_image = cover_entry["image_url"]

            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                raise ValueError(f"Failed to parse imgbox file or missing expected data: {e}")
    elif hamster_file_path != "":
        if fill_img_urls and os.path.isfile(hamster_file_path):
            try:
                with open(hamster_file_path, "r", encoding="utf-8") as f:
                    hamster_data = json.load(f)

                thumbs_key = f"{new_filename_base_name} - thumbnails"
                cover_key = f"{new_filename_base_name} - cover"
                preview_sheet_key = f"{new_filename_base_name} - Preview Sheet WebP"

                if thumbs_key in hamster_data and isinstance(hamster_data[thumbs_key], list):
                    thumbs_entry = hamster_data[thumbs_key][0]
                    if "image_url" in thumbs_entry:
                        thumbnails_image = thumbs_entry["image_url"]

                if cover_key in hamster_data and isinstance(hamster_data[cover_key], list):
                    cover_entry = hamster_data[cover_key][0]
                    if "image_url" in cover_entry:
                        cover_image = cover_entry["image_url"]

                if preview_sheet_key in hamster_data and isinstance(hamster_data[preview_sheet_key], list):
                    preview_sheet_entry = hamster_data[preview_sheet_key][0]
                    if "image_url" in preview_sheet_entry:
                        preview_sheet_image = preview_sheet_entry["image_url"]

            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                raise ValueError(f"Failed to parse hamster file or missing expected data: {e}")

    # Load JSON config
    performers_images, exit_code = await load_json_file("Resources/Performers_Images.json")
    if exit_code != 0 or performers_images is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Make a lowercase mapping of the JSON to support case-insensitive lookup
    performers_images_lower = {k.lower(): v for k, v in performers_images.items()}

    all_in_images = False

    if scene_performers:
        translation_table = str.maketrans("", "", "!@#$%^&*()_+='")

        processed_blocks = []
        mapped_names_list = []
        aliases_list = []

        # Clean and normalize names
        cleaned_names = []

        for data in scene_performers:
            performer_name = data[0]
            # Extract alias if it exists inside parentheses
            alias_match = re.search(r"\(([^)]*)\)", performer_name)
            if alias_match:
                alias = alias_match.group(1).strip()
                aliases_list.append(alias)
            else:
                alias = None

            # Remove anything inside parentheses and unwanted characters
            p = re.sub(r"\s*\([^)]*\)", "", performer_name).strip()
            p = p.translate(translation_table)
            cleaned_names.append(p)

        # Case-insensitive check if *all* names exist in performers_images_lower
        all_in_images = all(p.lower() in performers_images_lower for p in cleaned_names)

        # Process each cleaned name
        for full_name in cleaned_names:
            names = full_name.split()
            if len(names) > 1:
                joined = ".".join(names)
                processed_blocks.append(joined)
            else:
                processed_blocks.append(full_name)

            # Add [img] tag if all performers exist in performers_images_lower
            if all_in_images:
                lower_name = full_name.lower()
                if lower_name in performers_images_lower:
                    mapped_names_list.append(f"[img]{performers_images_lower[lower_name]}[/img]")
                else:
                    mapped_names_list.append(full_name)
            else:
                mapped_names_list.append(full_name)

        for alias in aliases_list:
            names = alias.split()
            if len(names) > 1:
                # If the second word starts with "id" followed by digits (case-insensitive)
                if re.match(r"^id\d+$", names[1], re.IGNORECASE):
                    joined = names[0]  # keep only the first word
                else:
                    joined = ".".join(names)
                processed_blocks.append(joined)
            else:
                processed_blocks.append(alias)
    else:
        processed_blocks = []
        mapped_names_list = []

    # Join the processed names (always space-separated)
    processed_string = " ".join(processed_blocks)

    # Join mapped names:
    # - if using [img], separate by a single space
    # - if not using [img], separate by ", "
    if scene_performers:
        if all_in_images:
            # ✅ 3 per row: insert a newline after every 3 images
            mapped_names = ""
            for i, img_tag in enumerate(mapped_names_list, start=1):
                mapped_names += img_tag + " "
                if i % 3 == 0 and i != len(mapped_names_list):
                    mapped_names += "\n"  # new line after every 3
            mapped_names = mapped_names.strip()
        else:
            # Normal comma-separated text for names
            mapped_names = ", ".join(mapped_names_list)
    else:
        mapped_names = ""

    for tag in studio_tags:
        cleaned_tag = tag.replace("'", "")
        cleaned_tag = re.sub(pattern, ".", cleaned_tag)
        processed_string += " " + cleaned_tag
        if cleaned_tag != tag.replace(" ", "") and (cleaned_tag.replace(" ", "")) not in processed_string:
            processed_string += " " + cleaned_tag.replace(" ", "").lower()

    if scene_tags:
        processed_string += " " + " ".join(scene_tags)
    processed_string += f" {fps}fps"
    # logger.debug(resolution)
    if resolution == "1080p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution} FHD"
    elif resolution == "2160p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution} UHD 4K"
    elif resolution == "720p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution} HD"
    else:
        processed_string += f" {resolution}"
    if codec == "hevc":
        processed_string += f" {codec} h265"
    else:
        processed_string += f" {codec}"
    # remove consecutive dots
    while ".." in processed_string:
        processed_string = processed_string.replace("..", ".")
    processed_string += f" {extension.replace('.', '')}"
    if suffix != "":
        processed_string += f" {suffix}"
    # Build output filename and path
    tags_filename = f"{new_filename_base_name}_tags.txt"
    tags_path = os.path.join(directory, tags_filename)

    # Write the string to the file
    try:
        with open(tags_path, "w", encoding="utf-8") as file:
            file.write(processed_string)
        # logger.debug(f"Tags saved to: {tags_path}")
    except Exception as e:
        logger.error(f"Failed to save tags: {e}")

    # Create replacement dictionary
    replacements = {
        "{BG}": bg,
        "{DESC_BUTTON}": desc_button_icon_url,
        "{RELEASE_DATE_BUTTON}": release_date_button_icon_url,
        "{PERFORMERS_BUTTON}": performers_button_icon_url,
        "{MEDIAINFO_BUTTON}": mediainfo_button_icon_url,
        "{SCREENS_BUTTON}": screens_button_icon_url,
        "{NEW_TITLE}": scene_title,
        "{SCENE_PRETTY_DATE}": scene_pretty_date if scene_pretty_date != "" else "N/A",
        "{SCENE_DESCRIPTION}": scene_description if len(scene_description) <= 200 else f"[spoiler=Full Description]{scene_description}[/spoiler]",
        "{FORMATTED_NAMES}": mapped_names,
        "{FPS}": fps_icon_url,
        "{RESOLUTION}": resolution_icon_url,
        "{IS_VERTICAL}": "yes" if is_vertical else "no",
        "{CODEC}": codec_icon_url,
        "{EXTENSION}": extension_icon_url,
        "{MEDIA_INFO}": media_info,
        "{COVER_IMAGE}": cover_image,
        "{PREVIEW_SHEET}": preview_sheet_image,
        "{STATIC_THUMBNAILS_SHEET}": thumbnails_image,
        "{CODE_VERSION}": code_version
    }
    try:
        # Replace placeholders
        for placeholder, value in replacements.items():
            if placeholder in template_content:
                template_content = template_content.replace(placeholder, value)

        # Make sure the target directory exists
        os.makedirs(directory, exist_ok=True)

        # build template filename
        output_filename = f"{new_filename_base_name}_template.txt"

        # Save the modified file
        output_path = os.path.join(directory, output_filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(template_content)
            # logger.debug(f"Template saved to: {tags_path}")
    except Exception as e:
        logger.error(f"An error has occured during creating of template for file: {new_filename_base_name}.{extension}")
        return False

    return True


async def parse_version(version_str: str) -> tuple:
    return tuple(map(int, version_str.strip().split('.')))


async def is_supported_major_minor(min_major_minor, max_major_minor) -> bool:
    current_major, current_minor = sys.version_info[:2]
    min_major, min_minor = min_major_minor
    max_major, max_minor = max_major_minor

    return (min_major, min_minor) <= (current_major, current_minor) <= (max_major, max_minor)


async def load_credentials(mode):
    # mode = 1, return scene data, mode = 2, return performer data, mode = 3, return ibb api key
    try:
        with open('creds.secret', 'r') as secret_file:
            secrets = json.load(secret_file)
            if mode == 1:  # Scenes API endpoint
                return secrets["api_auth"], secrets["api_scenes_url"], secrets["api_sites_url"]
            elif mode == 2:
                return secrets["api_auth"], secrets["api_performer_url"], None
            elif mode == 3:
                return secrets["imgbox_u"], secrets["imgbox_u"], None
            elif mode == 4:  # JAV API endpoint
                return secrets["api_auth"], secrets["api_jav_url"], secrets["api_sites_url"]
            elif mode == 5:
                return secrets["hamster_album_id"], secrets["hamster_api_key"], secrets["hamster_site_url"]
            elif mode == 6:
                return secrets["trackers"], None, None
            elif mode == 7:
                return secrets["hamster_site_url"], None, None
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


def is_valid_filename(s: str) -> bool:
    return not any(c in INVALID_CHARS for c in s)


async def ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def collect_list_input(title: str, mode, validate=False):
    logger.info(f"Start entering {title} (blank line to finish)")
    items = []
    while True:
        entry = (await ainput("")).strip()

        # Blank line = end of input
        if entry == "":
            break

        # Prevent empty performer names (e.g. when user enters whitespace)
        if mode == "performers" and not entry:
            logger.error("Performer name cannot be empty.")
            continue

        # Optional filename-like validation
        if validate and not is_valid_filename(entry):
            bad = [c for c in entry if c in INVALID_CHARS]
            logger.error(f"Invalid characters in entry: {bad}")
            logger.error("Please enter a valid value.")
            continue

        if mode == "performers":
            items.append((entry, None))
        else:
            items.append(entry)

    return items


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

    logger.info("[REQUIRED]Enter performers names (blank line to finish):")
    performers_names = await collect_list_input(
        "performers",
        mode="performers",
        validate=True
    )

    # --- scene_description ---
    scene_description = (await ainput("Enter scene description(blank for none):\n")).strip()

    # --- scene_tags (one per line, no validation needed) ---
    logger.info("Enter scene tags (blank line to finish):")
    scene_tags = await collect_list_input("tags", mode="tags", validate=True)

    # --- optional multi-part suffix input ---
    suffix = ""
    if manual_mode_ask_suffix:
        suffix_parts = []
        logger.info("Enter suffix parts (blank line to finish):")

        while True:
            entry = (await ainput("")).strip()

            # Blank line = end
            if entry == "":
                # Require at least one suffix part
                if not suffix_parts:
                    logger.warning("manual_mode_ask_suffix is enabled however no suffix was inputted.")
                    continue
                break

            # Validate characters
            if not is_valid_filename(entry):
                bad = [c for c in entry if c in INVALID_CHARS]
                logger.error(f"Invalid characters in suffix part: {bad}")
                logger.error("Please enter a valid suffix part.")
                continue

            suffix_parts.append(entry)

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