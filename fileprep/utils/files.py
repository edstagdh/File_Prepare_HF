"""File helpers: JSON loading, safe renames and OSHash calculation."""
import json
import os
import struct
import tempfile
from loguru import logger


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

        logger.debug(f"Renamed file: {file_path} -> {new_file_path}")
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


async def calculate_oshash(filepath: str) -> str:
    """
    Compute the OpenSubtitles-compatible OSHash for a media file.

    Sums the file size and every 64-bit little-endian word in the first
    and last 64 KB of the file (mod 2^64), returning a 16-char hex string.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is smaller than 128 KB.
        OSError: On any I/O failure.
    """
    try:
        filesize = os.path.getsize(filepath)
    except FileNotFoundError:
        logger.error("File not found: '{}'", filepath)
        raise

    if filesize < 65536 * 2:
        raise ValueError(f"File too small ({filesize} bytes); minimum is 131072 bytes.")

    hash_value = filesize

    try:
        with open(filepath, "rb") as f:
            # Hash first 64 KB
            for _ in range(65536 // 8):
                (value,) = struct.unpack("<Q", f.read(8))
                hash_value = (hash_value + value) & 0xFFFFFFFFFFFFFFFF

            # Hash last 64 KB
            f.seek(filesize - 65536)
            for _ in range(65536 // 8):
                (value,) = struct.unpack("<Q", f.read(8))
                hash_value = (hash_value + value) & 0xFFFFFFFFFFFFFFFF

    except OSError:
        logger.exception("I/O error while hashing '{}'", filepath)
        raise

    result = f"{hash_value:016x}"
    # logger.debug("OSHash for '{}': {}", filepath, result)
    return result
