import json
import os
import re
import subprocess
from datetime import datetime
from loguru import logger

CLEAN_CHARS = "!@#$%^&*()_+=' "


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
                logger.debug(
                    f"{tool_name} passed check (version_ok={version_ok}, date_ok={date_ok})."
                )
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


async def load_config(config_name):
    try:
        with open(config_name, 'r') as config_file:
            json_data = json.load(config_file)
            return json_data, 0  # Success
    except FileNotFoundError:
        logger.error(f"{config_name} file not found.")
        return None, 20  # JSON file load error
    except KeyError as e:
        logger.error(f"Key {e} is missing in the {config_name} file.")
        return None, 21  # Missing keys in JSON
    except json.JSONDecodeError:
        logger.error(f"Error parsing {config_name}. Ensure the JSON is formatted correctly.")
        return None, 20  # JSON file load error
    except Exception:
        logger.exception(f"An unexpected error occurred while loading {config_name}.")
        return None, 99  # Unknown exception


async def clean_filename(filename: str) -> str:
    """Removes unwanted characters and standardizes casing rules."""
    base_name, extension = os.path.splitext(filename)

    # Remove prefix if present
    base_name = re.sub(re.escape("H265_"), "", base_name, flags=re.IGNORECASE)
    base_name = re.sub(re.escape(".Xxx.1080p.Hevc.X265.Prt"), "", base_name, flags=re.IGNORECASE)

    # Remove unwanted characters
    base_name = base_name.translate(str.maketrans("", "", CLEAN_CHARS))

    # Capitalize segments after the first
    parts = base_name.split('.')
    for i in range(1, len(parts)):
        if parts[i].lower() not in ["and", "vr2normal", "bts"]:
            parts[i] = parts[i].capitalize()

    return '.'.join(parts) + extension


async def sanitize_site_filename_part(input_str):
    """
    Sanitize a string by removing disallowed characters and replacing dots with spaces.

    Args:
        input_str (str): The original string to sanitize.

    Returns:
        str: The sanitized string.
    """
    translation_table = str.maketrans("", "", "!@#$%^&*()_+=' ")
    sanitized = input_str.translate(translation_table)
    return sanitized.replace(".", " ")


async def is_valid_filename_format(filename: str) -> bool:
    """Validates filename against expected format."""
    base = os.path.splitext(filename)[0]
    return bool(re.fullmatch(
        r'([A-Za-z]+)\.(\d{2})\.(\d{2})\.(\d{2})\.([A-Za-z]+(?:\.[A-Za-z]+)*)',
        base
    ))


async def pre_process_files(directory):
    try:
        for filename in os.listdir(directory):
            if not filename.lower().endswith('.mp4'):
                continue

            if ' ' in filename:
                logger.error(f"Filename contains spaces: '{filename}'. Please remove spaces before proceeding.")
                return False, 12

            new_filename = await clean_filename(filename)
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
    if not performers:
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
        logger.debug(directory)
        logger.debug(file_path)
        # Create the full new file path
        new_file_path = os.path.join(directory, new_filename)
        logger.debug(new_file_path)
        if file_path != new_file_path:
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


async def generate_mediainfo_file(filename, mediainfo_path):
    try:

        # Split the full path into directory, filename, and extension
        file_path, file_name = os.path.split(filename)
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
