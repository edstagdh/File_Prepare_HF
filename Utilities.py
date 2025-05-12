import json
import os
import re
import subprocess
import sys
from datetime import datetime
from loguru import logger

CLEAN_CHARS = "!@#$%^&*()_+=’' :?"


async def run_command(command):
    """Execute a shell command and return stdout, stderr, and exit code."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            errors='ignore'  # Silently ignore decode errors
        )
        stdout = result.stdout.strip() if result.stdout else ''
        stderr = result.stderr.strip() if result.stderr else ''
        return stdout, stderr, result.returncode if result.returncode == 0 else 22  # 22 = Command Failed

    except Exception as e:
        logger.exception(f"Command failed: {command}")
        return '', str(e), 99  # 99 = Unknown exception


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
            date_ok = False

            # Extract version info
            version_match = re.search(rf"{tool_name} version (\d+)\.(\d+)\.(\d+)", stdout)
            if version_match:
                tool_version = tuple(map(int, version_match.groups()))
                if tool_version >= MIN_FFMPEG_VERSION:
                    version_ok = True
                else:
                    logger.error(
                        f"{tool_name} version {tool_version[0]}.{tool_version[1]}.{tool_version[2]} "
                        f"is too old. Minimum required version is {MIN_FFMPEG_VERSION[0]}."
                        f"{MIN_FFMPEG_VERSION[1]}.{MIN_FFMPEG_VERSION[2]}."
                    )
            else:
                # logger.warning(f"Could not parse {tool_name} version number.")
                pass

            # Extract build date
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", stdout)
            if date_match:
                tool_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                if tool_date >= MIN_FFMPEG_DATE:
                    date_ok = True
                else:
                    logger.error(
                        f"{tool_name} build date {tool_date.strftime('%Y-%m-%d')} is too old. "
                        f"Minimum required build date is {MIN_FFMPEG_DATE.strftime('%Y-%m-%d')}."
                    )
            else:
                # logger.warning(f"Could not parse {tool_name} build date.")
                pass

            if version_ok or date_ok:
                # logger.debug(f"{tool_name} passed check (version_ok={version_ok}, date_ok={date_ok}).")
                return True, 0
            else:
                logger.error(f"{tool_name} did not meet version or build date requirements.")
                return False, 33  # Neither version nor date are acceptable

        except Exception:
            logger.exception(f"An exception occurred while verifying {tool_name} installation.")
            return False, 98  # Exception during check

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
        for word in bad_words:
            base_name = re.sub(re.escape(word), "", base_name, flags=re.IGNORECASE)
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
    sanitized = input_str.replace(":", "-").replace(".", " ")
    sanitized = sanitized.translate(translation_table)
    return sanitized


async def is_valid_filename_format(filename: str) -> bool:
    """Validates filename against expected format."""
    base = os.path.splitext(filename)[0]
    return bool(re.fullmatch(
        r'([A-Za-z0-9\-!]+)\.(\d{2})\.(\d{2})\.(\d{2})\.([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)',
        base
    ))


async def pre_process_files(directory, bad_words, mode):
    try:
        for filename in os.listdir(directory):
            if not filename.lower().endswith('.mp4'):
                continue

            if ' ' in filename:
                logger.error(f"Filename contains spaces: '{filename}'. Please remove spaces before proceeding.")
                return False, 12

            new_filename = await clean_filename(filename, bad_words, mode)
            old_path = os.path.join(directory, filename)
            new_path = os.path.join(directory, new_filename)

            if old_path != new_path:
                try:
                    os.rename(old_path, new_path)
                    logger.info(f"Renamed: {filename} -> {new_filename}")
                except OSError as e:
                    logger.error(f"Failed to rename '{filename}' -> '{new_filename}': {e}")
                    return False, 13  # Exit code for rename failure

            if not await is_valid_filename_format(new_filename):
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
        translation_table = str.maketrans("", "", "!@#$%^&*()_+='")
        sanitized_performers = [
            p.translate(translation_table).replace(".", " ") for p in unique_performers
        ]
        return ", ".join(sanitized_performers)

    elif mode == 2:  # Filename (replace spaces with dots, then sanitize)
        translation_table = str.maketrans("", "", "!@#$%^&*()_+='")
        sanitized_performers = []
        for p in unique_performers:
            p = p.replace(" ", ".")
            p = p.translate(translation_table)
            p = re.sub(r"\.{2,}", ".", p)
            sanitized_performers.append(p)

        return ".and.".join(sanitized_performers)

    else:
        logger.error("Error: Unrecognized mode passed to format_performers.")
        return ""


async def rename_file(file_path, new_filename):
    """
    Renames a file to a new filename without changing the path.

    Args:
        file_path (str): The full current path of the file (including filename and extension).
        new_filename (str): The new filename (including extension) to rename the file to.

    Returns:
        bool: True if renaming was successful, False otherwise.
    """
    try:
        # Get the directory path where the file is located
        directory = os.path.dirname(file_path)
        # Create the full new file path
        new_file_path = os.path.join(directory, new_filename)
        if file_path.lower() != new_file_path.lower():
            # Rename the file
            os.rename(file_path, new_file_path)
            logger.info(f"Renamed file: {file_path} -> {new_file_path}")
            return True

    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return False
    except PermissionError:
        logger.error(f"Permission denied to rename file: {file_path}")
        return False
    except OSError as e:
        logger.error(f"OS error occurred while renaming file {file_path}: {e}")
        return False
    except Exception:
        logger.exception(f"Unexpected error occurred while renaming file {file_path}")
        return False


async def generate_mediainfo_file(filename, mediainfo_path, file_path):
    try:

        # Split the full path into directory, filename, and extension
        _, file_name = os.path.split(filename)
        file_base_name, file_extension = os.path.splitext(file_name)

        # Define the output text file name
        output_file = os.path.join(file_path, f"{file_base_name}_mediainfo.txt")

        # Check if the output file exists and delete it
        if os.path.exists(output_file):
            os.remove(output_file)
            # logger.debug(f"Existing mediainfo file {output_file} deleted.")

        # Enclose the mediainfo path in quotes in case it contains spaces, and use --Output=TEXT option
        command = f'"{mediainfo_path}" --Inform=file://{output_file} "{filename}"'

        # Run the mediainfo command and capture the output using the provided run_command function
        stdout, stderr, returncode = await run_command(command)

        # Check if there was an error with the mediainfo command
        if returncode != 0:
            raise Exception(f"Error running mediainfo: {stderr}")

        # Write the mediainfo output to the text file
        with open(output_file, 'w') as file:
            file.write(stdout)

        # logger.debug(f"Mediainfo for {filename} has been saved to {output_file}")

        # Return True indicating success
        return True

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        # Return False indicating failure
        return False


async def generate_template_video(
        new_title: str,
        scene_pretty_date: str,
        scene_description: str,
        formatted_names: str,
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
        fill_imgbox_urls: bool,
        imgbox_file_path: str,
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

    # Load JSON config
    json_map, exit_code = await load_json_file("BBCode_Images.json")
    if exit_code != 0 or json_map is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Additional information
    fps_icon_url = json_map[f"{fps}"]
    resolution_icon_url = json_map[f"{resolution}"]
    codec_icon_url = json_map[f"{codec}"]
    extension_icon_url = json_map[f"{extension.replace('.', '')}"]

    # Default image paths
    cover_image = f"{new_filename_base_name}.{image_output_format}"
    preview_sheet_image = f"{new_filename_base_name}_preview_sheet.webp"  # Hardcoded due to HF supported file hosting requirements(Either WEBP or GIF)
    thumbnails_image = f"{new_filename_base_name}_thumbnails.{image_output_format}"

    # Optionally fill URLs from imgbox
    if fill_imgbox_urls and os.path.isfile(imgbox_file_path):
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

    # Load JSON config
    performers_images, exit_code = await load_json_file("Performers_Images.json")
    if exit_code != 0 or performers_images is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    if formatted_names:
        # Step 1: Replace ", " with a separator for easy splitting
        name_blocks = formatted_names.replace(", ", "\n").splitlines()

        processed_blocks = []
        mapped_names = []

        for block in name_blocks:
            full_name = block.strip()

            # Step 2a: Prepare dot-joined name (e.g., "John Doe" -> "John.Doe")
            names = full_name.split()
            if len(names) > 1:
                joined = ".".join(names)
                processed_blocks.append(joined)
            else:
                processed_blocks.append(full_name)

            # Step 2b: Map full name to image URL or fallback to name
            if full_name in performers_images:
                mapped_names.append(f"[img]{performers_images[full_name]}[/img]")
            else:
                mapped_names.append(f"[img]{full_name}[/img]")
    else:
        processed_blocks = ""
        mapped_names = []

    processed_string = " ".join(processed_blocks)
    mapped_names = " ".join(mapped_names)

    for tag in studio_tags:
        cleaned_tag = tag.replace("'", "")
        cleaned_tag = re.sub(pattern, ".", cleaned_tag)
        processed_string += " " + cleaned_tag
        if cleaned_tag != tag.replace(" ", "") and (cleaned_tag.replace(" ", "")) not in processed_string:
            processed_string += " " + cleaned_tag.replace(" ", "").lower()

    processed_string += " " + " ".join(scene_tags)
    processed_string += f" {fps}fps"
    if resolution == "1080p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution} FHD"
    elif resolution == "2160p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution} UHD 4K"
    elif resolution == "720p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution} HD"
    else:
        processed_string += f" {resolution}"
    processed_string += f" {codec}"
    processed_string += f" {extension.replace('.', '')}"
    if suffix != "":
        processed_string += f" {suffix}"
    # Build output filename and path
    tags_filename = f"{new_filename_base_name}_HF_tags.txt"
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
        "{NEW_TITLE}": new_title,
        "{SCENE_PRETTY_DATE}": scene_pretty_date,
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

        # Save the modified file
        output_filename = f"{new_filename_base_name}_HF_template.txt"
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
            if mode == 1:
                return secrets["api_auth"], secrets["api_scenes_url"], secrets["api_sites_url"]
            elif mode == 2:
                return secrets["api_auth"], secrets["api_performer_url"], None
            elif mode == 3:
                return secrets["imgbox_u"], secrets["imgbox_u"], None
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
