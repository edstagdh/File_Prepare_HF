import math
import sys
import cv2
import json
import numpy as np
import os
import re
import httpx
import shutil
import subprocess
import textwrap
import time
import asyncio
import tempfile
from typing import List, Dict
from io import BytesIO
from loguru import logger
from mutagen.mp4 import MP4
from pymediainfo import MediaInfo
from Utilities import run_command, load_json_file
from TPDB_API import get_performer_profile_picture
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from tqdm import tqdm


async def has_unwanted_metadata(file_path, set_exclusive, exclusive_string) -> bool:
    try:
        media_info = MediaInfo.parse(file_path)

        for track in media_info.tracks:
            track_type = track.track_type.lower()

            # Debug view tracks metadata fields
            # logger.debug(track_type)
            # for attr, value in track.__dict__.items():
            #     logger.debug(f"{attr} = {value}")


            # ✅ Check Encoded/Tagged date everywhere
            if getattr(track, "encoded_date", None) or getattr(track, "tagged_date", None):
                # In case file has any Encoded/Tagged date attribute
                return True

            # ✅ Check copyright on general track
            if track_type == "general":
                if getattr(track, "copyright", None):
                    if not set_exclusive:
                        # Video has copyright attribute and set_exclusive = False
                        return True
                    else:
                        # Video has copyright attribute, and it doesn't match exclusive_string
                        if getattr(track, "copyright", None) != exclusive_string:
                            return True

            # ✅ Custom logic for audio track
            if track_type == "audio":
                title = getattr(track, "title", None)
                if title is not None and title != "Stereo":
                    # Title exists and is not equal to "Stereo"
                    return True

            # ✅ Custom logic for video track
            if track_type == "video":
                if getattr(track, "title", None):
                    # Video has any title attribute
                    return True

        return False

    except Exception as e:
        logger.error(f"Error reading metadata: {e}")
        return False


async def check_multiple_audio_tracks(input_file):
    """
    Check whether a video file contains more than one audio track using pymediainfo.

    Args:
        input_file (str): Full path to the video file to inspect.

    Returns:
        bool: True if the file has more than 1 audio track, False otherwise
              (including when the file can't be parsed or has 0/1 audio tracks).
    """
    try:
        media_info = MediaInfo.parse(input_file)

        audio_track_count = sum(1 for track in media_info.tracks if track.track_type == "Audio")

        return audio_track_count > 1

    except Exception:
        logger.exception(f"Error checking audio tracks for {input_file}")
        return False


async def select_and_filter_audio_tracks(file_path, keep_old=True):
    """
    Prompt the user to choose which audio track(s) to keep in a video file, then use ffmpeg
    (via run_command) to rebuild the file with only the selected audio track(s) via stream
    copy (no re-encoding), preserving video, subtitles, other data streams, metadata, and
    chapters.

    Lists all audio tracks found (index, language, title, codec, bitrate, default flag) and
    asks the user to select one, several (comma-separated), or all of them to keep.

    Args:
        file_path (str): Full path to the video file to process.
        keep_old (bool): If True, keeps the original file untouched and writes the result to
                          a new file with a "_removed_tracks" suffix. If False, replaces the
                          original file in place (original is deleted after processing).

    Returns:
        str | None: Path to the resulting file on success, or None if the operation failed,
                    was cancelled, or no changes were needed.
    """
    # --- Parse media info ---
    try:
        media_info = MediaInfo.parse(file_path)
        audio_tracks = [track for track in media_info.tracks if track.track_type == "Audio"]
    except Exception:
        logger.exception(f"Error parsing media info for {file_path}")
        return None

    if not audio_tracks:
        logger.warning(f"No audio tracks found in {file_path}")
        return None

    if len(audio_tracks) == 1:
        logger.info(f"Only one audio track found in {file_path}, nothing to filter.")
        return None

    # --- Display tracks ---
    try:
        print(f"\nAudio tracks found in: {os.path.basename(file_path)}")
        print("-" * 90)
        for idx, track in enumerate(audio_tracks):
            language = track.language or "und"
            title = track.title or "N/A"
            codec = track.codec_id or track.format or "N/A"
            bitrate = track.bit_rate or "N/A"
            default = track.default or "No"
            print(f"[{idx + 1}] Language: {language} | Title: {title} | Codec: {codec} | "
                  f"Bitrate: {bitrate} | Default: {default}")
        print("-" * 90)
    except Exception:
        logger.exception(f"Error displaying audio tracks for {file_path}")
        return None

    # --- Get user selection ---
    try:
        user_input = input("Enter track number(s) to keep (e.g. '1', '1,3', or 'all'): ").strip().lower()

        if user_input == "all":
            selected_indexes = list(range(len(audio_tracks)))
        else:
            selected_indexes = [int(x.strip()) - 1 for x in user_input.split(",") if x.strip()]

            if not selected_indexes or any(i < 0 or i >= len(audio_tracks) for i in selected_indexes):
                logger.error(f"Invalid track selection: '{user_input}'")
                return None
    except ValueError:
        logger.error(f"Invalid input received: '{user_input}'")
        return None
    except Exception:
        logger.exception(f"Error reading user selection for {file_path}")
        return None

    if len(selected_indexes) == len(audio_tracks):
        logger.info("All audio tracks selected, no changes needed.")
        return None

    # --- Build and run ffmpeg command ---
    base_name, ext = os.path.splitext(file_path)
    temp_output = f"{base_name}_temp_audio_filter{ext}"

    try:
        # -map 0 keeps everything (video, subtitles, data, chapters), -map -0:a strips all
        # audio, then each selected track is re-added individually by its audio stream index.
        command = ["ffmpeg", "-y", "-i", file_path, "-map", "0", "-map", "-0:a"]
        for idx in selected_indexes:
            command.extend(["-map", f"0:a:{idx}"])
        command.extend(["-c", "copy", "-map_metadata", "0", "-map_chapters", "0", temp_output])

        logger.debug(f"Running ffmpeg command: {' '.join(command)}")

        stdout, stderr, exit_code = await run_command(command)

        if exit_code != 0:
            logger.error(f"ffmpeg failed (exit_code={exit_code}) for {file_path}: {stderr}")
            if os.path.exists(temp_output):
                os.remove(temp_output)
            return None
    except Exception:
        logger.exception(f"Error running ffmpeg command for {file_path}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return None

    # --- Finalize output file ---
    try:
        if keep_old:
            keep_old_path = f"{base_name}_original_tracks{ext}"
            os.rename(file_path, keep_old_path)
            os.rename(temp_output, file_path)
            final_path = file_path
            logger.success(f"Kept original file while renaming it to: {keep_old_path}")
        else:
            os.remove(file_path)
            os.rename(temp_output, file_path)
            final_path = file_path
            logger.success(f"Replaced original file with filtered audio tracks: {final_path}")

        return final_path
    except Exception:
        logger.exception(f"Error finalizing output file for {file_path}")
        return None

async def get_existing_title(input_file):
    try:
        media_info = MediaInfo.parse(input_file)

        for track in media_info.tracks:
            if track.track_type == "General":
                title = track.title  # corresponds to ©nam
                if title:
                    return title.strip()
                return None

        return None

    except Exception:
        logger.exception(f"Error retrieving title from {input_file}")
        return None


async def get_existing_description(input_file):
    try:
        media_info = MediaInfo.parse(input_file)

        for track in media_info.tracks:
            if track.track_type == "General":
                description = track.comment  # corresponds to ©cmt
                if description:
                    return description.strip()
                return None

        return None

    except Exception:
        logger.exception(f"Error retrieving description from {input_file}")
        return None


async def check_existing_exclusive(input_file, exclusive_string):
    try:
        media_info = MediaInfo.parse(input_file)

        for track in media_info.tracks:
            if track.track_type == "General":
                copyright_text = track.copyright  # corresponds to ©cprt
                logger.debug(copyright_text)

                if copyright_text:
                    if copyright_text == exclusive_string:
                        return None
                    else:
                        return True
                else:
                    return True

        return None

    except Exception:
        logger.exception(f"Error retrieving description from {input_file}")
        return None


async def check_existing_Encoder_Library(input_file):
    try:
        media_info = MediaInfo.parse(input_file)

        for track in media_info.tracks:
            if track.track_type == "General":
                writing_library = track.writing_application

                if not writing_library:
                    return True

                if writing_library.strip() == "File_Prepare_HF":
                    return False

                return True

        return True

    except Exception:
        logger.exception(f"Error retrieving writing library from {input_file}")
        return True


async def chapters_need_update(input_file, add_timestamps_markers, markers_list):
    """
    Returns:
        True  -> if no chapters exist OR they differ from markers_list
        False -> if chapters exist and match markers_list
        None  -> on error
    """
    try:
        # logger.debug(f"Checking chapters for: {input_file}")
        # logger.debug(f"Incoming markers_list: {markers_list}")

        media_info = MediaInfo.parse(input_file)

        existing_chapters = []

        menu_tracks = [t for t in media_info.tracks if t.track_type == "Menu"]

        # logger.debug(f"Total tracks found: {len(media_info.tracks)}")
        # logger.debug(f"Menu tracks found: {len(menu_tracks)}")

        if not menu_tracks:
            if add_timestamps_markers:
                # logger.info("No Menu tracks detected → returning True")
                return True
            else:
                return False

        track_dict = menu_tracks[0].to_data()

        # logger.debug(f"Menu track raw keys: {list(track_dict.keys())}")

        for key, value in track_dict.items():
            if isinstance(key, str) and re.match(r"\d{2}_\d{2}_\d{5}", key):

                # logger.debug(f"Chapter key matched: {key} -> {value}")

                try:
                    h, m, ms = key.split("_")
                    total_seconds = (
                            int(h) * 3600 +
                            int(m) * 60 +
                            int(ms) / 1000
                    )
                except Exception as e:
                    logger.error(f"Time parse failed for {key}: {e}")
                    continue

                existing_chapters.append({
                    "title": (value or "").strip(),
                    "start_time": round(total_seconds)
                })

        existing_chapters.sort(key=lambda x: x["start_time"])

        # logger.debug(f"Parsed existing chapters: {existing_chapters}")

        if not markers_list:
            if add_timestamps_markers:
                # logger.warning("Markers list is empty → returning True")
                return True
            else:
                return False

        normalized_markers = sorted(
            [
                {
                    "title": (m.get("title") or "").strip(),
                    "start_time": int(m.get("start_time", 0))
                }
                for m in markers_list
            ],
            key=lambda x: x["start_time"]
        )

        # logger.debug(f"Existing count: {len(existing_chapters)}")
        # logger.debug(f"Markers count: {len(normalized_markers)}")

        if len(existing_chapters) != (len(normalized_markers)):
            logger.warning("Chapter count mismatch → returning True")
            return True

        for i, (existing, marker) in enumerate(zip(existing_chapters, normalized_markers)):
            # logger.debug(f"Comparing chapter {i}")
            # logger.debug(f"Existing: {existing}")
            # logger.debug(f"Marker:   {marker}")

            if (
                    existing["start_time"] != marker["start_time"]
                    or existing["title"] != marker["title"]
            ):
                logger.warning("Mismatch detected → returning True")
                return True

        # logger.debug("Chapters fully match → returning False")
        return False

    except Exception:
        logger.exception(f"Error checking chapters for {input_file}")
        return None


async def get_existing_tpdb_uuid(input_file):
    try:
        media_info = MediaInfo.parse(input_file)

        for track in media_info.tracks:
            if track.track_type == "General":
                tpdb_id = track.album  # corresponds to ©alb
                if tpdb_id:
                    return tpdb_id.strip()
                return None

        return None

    except Exception:
        logger.exception(f"Error retrieving tpdb_id from {input_file}")
        return None


async def cover_image_output_file_exists(input_video_file_name,
                                         original_video_file_name,
                                         output_path,
                                         image_output_format,
                                         cover_regeneration_mode,
                                         use_sub_folder=False,
                                         sub_folder_path=None):
    """
    Check if an output file already exists for the input or original video,
    and handle it according to the cover_regeneration_mode:
      - 'user input': prompt user
      - 'force regenerate': always regenerate
      - 'force keep': always keep existing file(s)
    """
    input_base_name, _ = os.path.splitext(input_video_file_name)
    original_base_name, _ = os.path.splitext(original_video_file_name)

    expected_input_output_file = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
    expected_original_output_file = os.path.join(output_path, f"{original_base_name}.{image_output_format}")

    # Also check subfolder if enabled
    sub_input_file = os.path.join(sub_folder_path, f"{input_base_name}.{image_output_format}") if use_sub_folder and sub_folder_path else None
    sub_original_file = os.path.join(sub_folder_path, f"{original_base_name}.{image_output_format}") if use_sub_folder and sub_folder_path else None

    def find_existing_file(*paths):
        for p in paths:
            if p and os.path.exists(p):
                return p
        return None

    existing_input = find_existing_file(expected_input_output_file, sub_input_file)
    existing_original = find_existing_file(expected_original_output_file, sub_original_file)

    def safe_move(src, dst_dir):
        """Safely move file to dst_dir, renaming if necessary."""
        if not src or not os.path.exists(src):
            return
        os.makedirs(dst_dir, exist_ok=True)
        base_name = os.path.basename(src)
        dst = os.path.join(dst_dir, base_name)
        name, ext = os.path.splitext(dst)
        counter = 1
        while os.path.exists(dst):
            dst = f"{name} ({counter}){ext}"
            counter += 1
        shutil.move(src, dst)
        logger.info(f"Moved existing file to subfolder: {dst}")

    # --- FORCE REGENERATE ---
    if cover_regeneration_mode == "force regenerate":
        # Delete any existing file(s)
        for existing_file in [existing_original, existing_input]:
            if existing_file and os.path.exists(existing_file):
                os.remove(existing_file)
                logger.info(f"[Force Regenerate] mode, Removed existing file:  {existing_file}")
        return False  # Always regenerate

    # --- FORCE KEEP ---
    if cover_regeneration_mode == "force keep":
        for existing_file in [existing_original, existing_input]:
            if existing_file and os.path.exists(existing_file):
                logger.info(f"[Force Keep] mode, Keeping existing file: '{existing_file}'")
                final_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
                if existing_file != final_path:
                    os.rename(existing_file, final_path)
                    logger.info(f"Renamed existing file to: {final_path}")
                if use_sub_folder and sub_folder_path:
                    safe_move(final_path, sub_folder_path)
                return True
        logger.info("[Force Keep] mode, No existing file found — Generating")
        return False

    # --- USER INPUT MODE (default behavior) ---
    if cover_regeneration_mode == "user input":
        if existing_original and existing_input and input_video_file_name.lower() != original_video_file_name.lower():
            logger.info(f"Both files '{existing_original}' and '{existing_input}' exist.")
            await asyncio.sleep(0.5)
            user_choice = input(f"Would you like to (K)eep one of existing files or (R)e-download? [K/R]: ").lower()

            if user_choice == "k":
                await asyncio.sleep(0.5)
                keep_file = input(f"(O)riginal '{existing_original}' or (I)nput '{existing_input}': ").lower()
                if keep_file == "o":
                    os.remove(existing_input)
                    kept_path = existing_original
                elif keep_file == "i":
                    os.remove(existing_original)
                    kept_path = existing_input
                else:
                    logger.error("Invalid choice! Skipping file processing.")
                    return False

                final_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
                os.rename(kept_path, final_path)
                logger.info(f"Kept and renamed file: {final_path}")

                if use_sub_folder and sub_folder_path:
                    safe_move(final_path, sub_folder_path)
                return True

            elif user_choice == "r":
                logger.info("User selected to re-download file.")
                return False
            else:
                logger.error("Invalid choice! Skipping file processing.")
                return False

        # Handle only one existing file
        for existing_file in [existing_original, existing_input]:
            if existing_file:
                logger.info(f"File '{existing_file}' exists.")
                await asyncio.sleep(0.5)
                user_choice = input(f"Would you like to (K)eep it or (R)e-download? [K/R]: ").lower()

                if user_choice == "k":
                    final_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
                    os.rename(existing_file, final_path)
                    logger.info(f"Kept and renamed file: {final_path}")

                    if use_sub_folder and sub_folder_path:
                        safe_move(final_path, sub_folder_path)
                    return True
                elif user_choice == "r":
                    os.remove(existing_file)
                    logger.info("User selected to re-download cover image.")
                    return False
                else:
                    logger.error("Invalid choice! Skipping file processing.")
                    return False

    # No existing file found → regenerate
    return False


async def cover_image_download_and_conversion(image_url: str,
                                              tpdb_image_url: str,
                                              input_video_file_name: str,
                                              original_video_file_name: str,
                                              output_path: str,
                                              image_output_format: str,
                                              use_sub_folder,
                                              sub_folder_path,
                                              cover_regeneration_mode) -> bool:
    try:
        input_base_name, _ = os.path.splitext(input_video_file_name)

        exists = await cover_image_output_file_exists(
            input_video_file_name,
            original_video_file_name,
            output_path,
            image_output_format,
            cover_regeneration_mode,
            use_sub_folder,
            sub_folder_path
        )

        # If file exists, and we’re not regenerating, skip download
        if exists and cover_regeneration_mode != "force regenerate":
            return True

        # Download helper
        async def download_image(url):
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                try:
                    img = Image.open(BytesIO(response.content))
                    img.verify()
                except Exception as e:
                    raise ValueError(f"URL does not contain a valid image: {url}") from e
            return response

        # Try downloading
        try:
            response = await download_image(image_url)
        except Exception as e:
            # commented out to avoid log clutter, will always fall back to TPDB image url if exists.
            # logger.error(f"Failed to download from primary URL: {image_url}, error: {e}")
            if tpdb_image_url:
                response = await download_image(tpdb_image_url)
            else:
                raise


        temp_image_path = os.path.join(output_path, f"temp_image.{image_output_format}")
        with open(temp_image_path, "wb") as f:
            f.write(response.content)

        # Downscale if too large
        try:
            with Image.open(temp_image_path) as img:
                width, height = img.size
                if width > 1920 or height > 1080:
                    resample = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
                    img.thumbnail((1920, 1080), resample)
                    img.save(temp_image_path, format=image_output_format.upper())
                    logger.info(f"Image downscaled to fit within 1080p: {temp_image_path}")
        except Exception as e:
            logger.error(f"Error while checking/downscaling image: {e}")

        final_image_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
        shutil.move(temp_image_path, final_image_path)
        logger.success(f"Image saved to {final_image_path}")

        if use_sub_folder and sub_folder_path:
            os.makedirs(sub_folder_path, exist_ok=True)
            sub_final_path = os.path.join(sub_folder_path, os.path.basename(final_image_path))
            shutil.move(final_image_path, sub_final_path)
            logger.info(f"Image moved to subfolder: {sub_final_path}")

        return True

    except Exception as e:
        logger.error(f"Unhandled error in cover image processing: {e}")
        return False


async def convert_image_format(input_file_path: str, output_file_path: str, output_format: str):
    """
    Converts an image to the specified format and saves it in the same directory.

    Args:
        input_file_path (str): Full path to the input image file.
        output_file_path (str): Path to save the image
        output_format (str): Target image format (e.g., "jpeg", "png", "webp", "jpg").

    Returns:
        Bool: if successful
        str: path to file generated
    """
    try:
        if not os.path.exists(input_file_path):
            raise FileNotFoundError(f"Input file does not exist: {input_file_path}")

        format_mapping = {
            "jpg": "JPEG",
            "jpeg": "JPEG",
            "png": "PNG",
            "webp": "WEBP",
            "bmp": "BMP",
        }

        normalized_format = output_format.lower()
        pil_format = format_mapping.get(normalized_format)

        if not pil_format:
            raise ValueError(f"Unsupported output format: {output_format}")

        input_dir, input_filename = os.path.split(input_file_path)
        base_name, _ = os.path.splitext(input_filename)
        output_file_name = f"{base_name}.{normalized_format}"
        output_image_path = os.path.join(output_file_path, output_file_name)

        with Image.open(input_file_path) as img:
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            img.save(output_image_path, format=pil_format)

        logger.success(f"Image converted to {pil_format} and saved at: {output_image_path}")
        return True, output_image_path

    except Exception as e:
        logger.error(f"Failed to convert image: {e}")
        return False, None


async def generate_performer_profile_picture(performers, directory, tpdb_performer_url, target_size, zoom_factor, blur_kernel_size, posters_limit, face_detector_model_path,
                                             performer_image_output_format, font_full_name):
    """
        Creates a folder named 'faces' in the specified directory and processes performer pictures.

        :param font_full_name:
        :param performer_image_output_format:
        :param face_detector_model_path:
        :param posters_limit:
        :param target_size: Set the desired output size (X, Y)
        :param zoom_factor: Set the zoom factor for cropping
        :param blur_kernel_size: Kernel size for the Gaussian blur (adjust as needed)
        :param tpdb_performer_url: for debug purposes
        :param performers: List of tuples, where the second item in each tuple is a performer ID.
        :param directory: Path to the base directory where the 'faces' folder will be created.
        """
    if not performers or performers == "":
        return True
    try:
        faces_dir = os.path.join(directory, "faces")
        os.makedirs(faces_dir, exist_ok=True)
        logger.success(f"Created/verified directory: {faces_dir}")
    except Exception:
        logger.exception(f"Failed to create directory in: {directory}")
        return False

    # Load JSON config
    performers_images, exit_code = await load_json_file("Resources/Performers_Images.json")
    if exit_code != 0 or performers_images is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Create lowercase lookup map for fast case-insensitive checks
    lower_map = {k.lower(): k for k in performers_images.keys()}

    for data in performers:
        try:
            if len(data) < 2:
                logger.warning(f"Skipping invalid tuple: {data}")
                continue
            # Clean performer name if alias is included:
            translation_table = str.maketrans("", "", "!@#$%^&*()_+='")
            # Remove anything inside parentheses and the parentheses themselves
            performer_name = data[0]
            p = re.sub(r"\s*\([^)]*\)", "", performer_name)
            p = p.translate(translation_table)

            performer_id = data[1]

            if p.lower() in lower_map:
                logger.debug(f"Performer {p} already has mapped image in json file (case-insensitive)")
                continue
            logger.debug(f"Processing performer {p}, ID: {performer_id}")
            performer_posters, performer_slug = await get_performer_profile_picture(p, performer_id, posters_limit)
            # performer_url = tpdb_performer_url + performer_slug if performer_slug else ""
            # logger.debug(f"Performer URL: {performer_url}")
            downloaded_files = await download_poster_images(performer_posters, faces_dir, performer_slug, posters_limit)
            if not downloaded_files:
                return False
            if "already downloaded" in downloaded_files:
                continue
            font_size = 18  # Font size
            text_color = (255, 255, 255)  # Text color (black)
            position_percentage = 0.8
            for file in downloaded_files:
                await process_detection(file, faces_dir, zoom_factor, target_size, blur_kernel_size, p, font_size, text_color, position_percentage, face_detector_model_path,
                                        performer_image_output_format, font_full_name)

        except Exception:
            logger.exception(f"Error processing performer {p}, ID: {performer_id}")
            return False
    return True


async def download_poster_images(poster_urls, faces_dir, performer_slug, posters_limit):
    os.makedirs(faces_dir, exist_ok=True)
    downloaded_files = []

    existing_files = [f for f in os.listdir(faces_dir)
                      if f.startswith(performer_slug) and f.lower().endswith(".webp")]
    if existing_files:
        logger.info(f"Performer posters already exist in {faces_dir}")
        return ["already downloaded"]

    async def fetch_one(index, url):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            filename = f"{performer_slug}_{index}.webp"
            filepath = os.path.join(faces_dir, filename)
            image.save(filepath, format="WEBP")
            logger.success(f"Saved image to {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"Failed to download or save poster {index} for {performer_slug}: {e}")
            return None

    results = await asyncio.gather(*(fetch_one(i, url) for i, url in enumerate(poster_urls[:posters_limit], start=1)))
    downloaded_files = [r for r in results if r]

    return downloaded_files if downloaded_files else False


async def process_detection(image_path, output_path, zoom_factor, target_size, blur_kernel_size, text, font_size, text_color, position_percentage, face_detector_model_path,
                            performer_image_output_format, font_full_name):
    filename = os.path.basename(image_path)
    base_filename = os.path.splitext(filename)[0]
    # Detect faces in the image
    bounding_boxes, keypoints, image = await detect_faces(image_path, face_detector_model_path)

    if len(bounding_boxes) == 0:
        logger.error(f"No faces detected in image: {image_path}")
        return

    for i, box in enumerate(bounding_boxes):
        face = await crop_face(image, box, zoom_factor)

        if face.shape[0] < 10 or face.shape[1] < 10:
            logger.error(f"Skipping image {image_path} due to small face crop.")
            continue

        # Create a long vertical elliptical mask
        try:
            mask = await create_long_vertical_elliptical_mask(face, blur_kernel_size)
        except ValueError as e:
            logger.error(f"Error creating long vertical elliptical mask for {image_path}: {e}")
            continue

        # Save the face image with a long vertical elliptical shape
        output_file = f"{base_filename}-face-{i + 1}.{performer_image_output_format}"
        full_output_file_path = os.path.join(output_path, output_file)
        await save_face_image_with_rounded_corners(face, mask, full_output_file_path, target_size)

        await overlay_text(
            input_file=full_output_file_path,
            output_file=full_output_file_path,
            text=text,
            font_size=font_size,
            font_full_name=font_full_name,
            text_color=text_color,  # White text
            glow_color=(0, 0, 0),  # Black glow
            glow_thickness=3,  # Thickness of the glow
            bold=True,  # Simulated bold
            bold_thickness=0,  # Adjust thickness as needed
            max_chars_per_line=13,
            position_percentage=position_percentage,
            line_spacing=15,  # Increased spacing for better readability
        )

        logger.success(f"Finished processing {image_path} - {output_file}")


async def overlay_text(
        input_file,
        output_file,
        text,
        font_size,
        font_full_name,
        text_color=(255, 255, 255),  # White text
        glow_color=(0, 0, 0),  # Black glow
        glow_thickness=3,  # Thickness of the glow
        bold=True,  # Simulate bold
        bold_thickness=0,  # Number of repeated draws for bold effect
        max_chars_per_line=13,  # Maximum characters per line
        position_percentage=0.8,  # Position percentage (vertical alignment)
        line_spacing=10,  # Gap between lines
):
    """
    Overlay text with a black glow effect and simulated bold on an image with transparency preserved.
    """
    # Open the input image in RGBA mode
    image = Image.open(input_file).convert("RGBA")
    width, height = image.size

    # Create a blank RGBA image for the overlay (same size as the original)
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))  # Fully transparent background

    # Draw on the overlay
    draw = ImageDraw.Draw(overlay)

    try:
        font_path = f"Resources/{font_full_name}"
        font = ImageFont.truetype(font_path, size=18)  # Adjust size here
    except IOError:
        font = ImageFont.load_default()  # Fallback if font is not available

    # Wrap text into multiple lines
    wrapped_text = textwrap.fill(text, width=max_chars_per_line)
    lines = wrapped_text.split("\n")

    # Measure total text block size
    text_sizes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [bbox[3] - bbox[1] for bbox in text_sizes]
    max_width = max([bbox[2] - bbox[0] for bbox in text_sizes])
    total_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Calculate the starting position for the text block
    x = (width - max_width) // 2
    y = int(height * position_percentage) - total_height // 2

    for line_index, line in enumerate(lines):
        text_bbox = draw.textbbox((0, 0), line, font=font)
        line_width = text_bbox[2] - text_bbox[0]
        line_x = (width - line_width) // 2  # Center each line
        line_y = y + sum(line_heights[:line_index]) + line_spacing * line_index

        for offset_x in range(-glow_thickness, glow_thickness + 1):
            for offset_y in range(-glow_thickness, glow_thickness + 1):
                draw.text((line_x + offset_x, line_y + offset_y), line, font=font, fill=glow_color)

    # Draw the text
    for line_index, line in enumerate(lines):
        text_bbox = draw.textbbox((0, 0), line, font=font)
        line_width = text_bbox[2] - text_bbox[0]
        line_x = (width - line_width) // 2  # Center each line
        line_y = y + sum(line_heights[:line_index]) + line_spacing * line_index

        if bold and bold_thickness > 0:
            for offset_x in range(-bold_thickness, bold_thickness + 1):
                for offset_y in range(-bold_thickness, bold_thickness + 1):
                    draw.text((line_x + offset_x, line_y + offset_y), line, font=font, fill=text_color)
        else:
            draw.text((line_x, line_y), line, font=font, fill=text_color)

    # Combine the overlay with the original image while preserving transparency
    combined = Image.alpha_composite(image, overlay)

    # Save the output image in WEBP format
    combined.save(output_file, "WEBP")
    logger.success(f"Saved output image as WebP: {output_file}")


async def detect_faces(image_path, face_detector_model_path):
    threshold = 0.93

    image = cv2.imread(image_path)
    img_h, img_w = image.shape[:2]

    detector = cv2.FaceDetectorYN.create(
        model=face_detector_model_path,
        config="",
        input_size=(img_w, img_h),
        score_threshold=threshold,
    )

    _, faces = detector.detect(image)

    bounding_boxes = []
    keypoints = []

    for face in (faces if faces is not None else []):
        logger.debug(f"Confidence: {float(face[-1]):.2f}")
        x, y, w, h = face[0:4].astype(int)
        bounding_boxes.append([x, y, w, h])
        keypoints.append(face[4:14].reshape(5, 2).tolist())  # right eye, left eye, nose, right mouth, left mouth

    return bounding_boxes, keypoints, image

async def crop_face(image, bounding_box, zoom_factor=1.2):
    # Extract the face region from the image using bounding box (x, y, w, h)
    x, y, w, h = bounding_box

    # Calculate new dimensions with zoom factor (zoom out equally in all directions)
    new_w = int(w * zoom_factor)
    new_h = int(h * zoom_factor)

    # Calculate center of the face (this will be the center point for zooming)
    center_x = x + w // 2
    center_y = y + h // 2

    # Adjust the bounding box to zoom out symmetrically around the center
    new_x = center_x - new_w // 2
    new_y = center_y - new_h // 2

    # Ensure new_x, new_y are not out of bounds
    new_x = max(new_x, 0)
    new_y = max(new_y, 0)

    # Make sure the new cropped area doesn't exceed image bounds
    image_height, image_width = image.shape[:2]
    new_w = min(new_w, image_width - new_x)
    new_h = min(new_h, image_height - new_y)

    # Crop the face with the adjusted bounding box
    face = image[new_y:new_y + new_h, new_x:new_x + new_w]
    return face


async def create_long_vertical_elliptical_mask(image, blur_kernel_size=21):
    """
    Create a long vertical elliptical mask with faded edges.
    :param image: The input face image.
    :param blur_kernel_size: The kernel size for Gaussian blur to smooth the fade.
    :return: A mask with a long vertical elliptical shape.
    """
    height, width = image.shape[:2]

    # Create a black mask
    mask = np.zeros((height, width), dtype=np.uint8)

    # Define the center and axes of the ellipse
    center = (width // 2, height // 2)  # Center of the image
    axes = (width // 2, height // 2)  # Horizontal axis is smaller; vertical axis is larger for elongated ellipse

    # Draw a filled white ellipse in the center
    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, thickness=-1)

    # Apply Gaussian blur to smooth the edges of the ellipse
    blurred_mask = cv2.GaussianBlur(mask, (blur_kernel_size, blur_kernel_size), 0)

    return blurred_mask


async def save_face_image_with_rounded_corners(face, mask, output_path, target_size=(256, 256)):
    """
    Save a face image with a mask of rounded corners.
    :param face: The cropped face image.
    :param mask: The mask with rounded corners.
    :param output_path: The file path to save the image.
    :param target_size: The desired output size of the image.
    """
    # Apply the mask to the face image
    result = cv2.bitwise_and(face, face, mask=mask)

    # Create an image with an alpha channel
    result_with_alpha = cv2.cvtColor(result, cv2.COLOR_BGR2BGRA)
    result_with_alpha[:, :, 3] = mask

    # Resize to the target size
    result_resized = cv2.resize(result_with_alpha, target_size, interpolation=cv2.INTER_LINEAR)

    # Save the image
    cv2.imwrite(output_path, result_resized)


async def re_encode_video(new_filename, directory, keep_original_file, is_vertical, re_encode_downscale, limit_cpu_usage, remove_existing_chapters,
                          re_encode_hevc_CRF):
    file_path = os.path.join(directory, new_filename)
    # logger.debug(f"Processing file: {file_path}")

    if is_vertical is None:
        _, is_vertical = await get_video_resolution_and_orientation(str(file_path))

    if not isinstance(re_encode_hevc_CRF, int) or not (0 <= re_encode_hevc_CRF < 30):
        logger.error(f"processing failed for {file_path}, unexpected CRF value: {re_encode_hevc_CRF}")
        return False

    temp_output = await re_encode_to_hevc(file_path, is_vertical, re_encode_downscale, limit_cpu_usage, remove_existing_chapters, re_encode_hevc_CRF)

    # Return True if the file is already encoded with HEVC/AV1
    if temp_output is None:
        # logger.debug(f"The file is already encoded with HEVC/AV1: {file_path}")
        return True

    # Return False if the re encoding failed
    if temp_output is False:
        logger.error(f"processing failed for {file_path}")
        return False

    try:
        if isinstance(temp_output, str):
            if keep_original_file:
                old_file_path = os.path.join(directory, f"{os.path.splitext(new_filename)[0]}_old{os.path.splitext(new_filename)[1]}")
                os.rename(file_path, old_file_path)
                logger.info(f"Original file renamed to: {old_file_path}")
            else:
                os.remove(file_path)

            final_output = os.path.join(directory, new_filename)
            shutil.move(temp_output, final_output)

            logger.info(f"Replaced original file with HEVC version: {final_output}")
            return True
        else:
            raise "Invalid type returned from encode function"
    except Exception as e:
        logger.error(f"Failed to replace {file_path}: {e}")
        return False


async def re_encode_to_hevc(file_path, is_vertical, re_encode_downscale, limit_cpu_usage, remove_existing_chapters, re_encode_hevc_CRF):
    """
    Re-encode the given file to HEVC and show progress with a tqdm bar.

    Returns:
        str  : Path to the converted file if successful
        None : If already encoded in HEVC/AV1
        False: If encoding failed
    """

    width, height, bit_rate = await get_video_resolution(file_path)
    directory, filename = os.path.split(file_path)
    temp_output = await generate_temp_filename(directory, filename)
    duration, fps = await get_video_duration(file_path)
    duration = int(duration)

    keyint = int(fps * 2)  # every 2 seconds

    x265_params = (
        f"crf={re_encode_hevc_CRF}:"
        "preset=medium:"
        "ref=3:"
        "limit-refs=2:"
        f"keyint={keyint}:"
    )

    if limit_cpu_usage:
        threads = max(1, math.ceil(os.cpu_count() * 0.70))
        x265_params += f"pools={threads}:"

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i", file_path,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx265",
        "-vtag", "hvc1",
        "-x265-params", x265_params,
        "-c:a", "aac",
        "-b:a", "128k",
        "-map_metadata", "-1",
    ]

    ffmpeg_cmd += ["-map_chapters", "-1" if remove_existing_chapters else "0"]
    ffmpeg_cmd += ["-dn", "-sn", ]

    if re_encode_downscale and width and height:
        if not is_vertical and (width > 1920 or height > 1080):
            ffmpeg_cmd += ["-vf", "scale='min(1920,iw)':'min(1080,ih)'"]
        elif is_vertical and height > 1080:
            ffmpeg_cmd += ["-vf", "scale=-2:1080"]

    ffmpeg_cmd.append(temp_output)

    # --- start ffmpeg process ---
    process = subprocess.Popen(
        ffmpeg_cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace"  # so it won’t crash on bad bytes
    )

    time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
    size_pattern = re.compile(r"size=\s*(\d+)KiB")
    speed_pattern = re.compile(r"speed=([\d\.x]+)")

    start_time = time.time()
    last_update = 0

    # tqdm progress bar
    pbar = tqdm(total=duration,
                unit="s",
                ncols=120,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}s")

    for line in process.stderr:
        now = time.time()
        if now - last_update >= 3:  # update every 3 seconds
            t_match = time_pattern.search(line)
            s_match = size_pattern.search(line)
            sp_match = speed_pattern.search(line)

            if t_match and s_match:
                encoded_time = parse_ffmpeg_time(t_match.group(1))
                current_size_kib = int(s_match.group(1))
                speed = float(sp_match.group(1).replace('x', '')) if sp_match else 1.0

                elapsed = now - start_time
                remaining = max(0, duration / speed - elapsed)
                eta_human = format_eta(int(remaining))
                if encoded_time > 0:
                    predicted_size = format_size((current_size_kib / encoded_time) * duration)
                else:
                    predicted_size = "estimating…"
                bitrate = format_bitrate(current_size_kib, encoded_time)

                msg = (f"Speed: {speed:.2f}x "
                       f"ETA: {eta_human} "
                       f"Est Size: {predicted_size} "
                       f"Bitrate: {bitrate}")

                pbar.update(encoded_time - pbar.n)  # jump to current second
                pbar.set_description(msg)

                last_update = now

    process.wait()
    pbar.close()

    if process.returncode != 0 or not os.path.exists(temp_output):
        sys.stderr.write(f"\n[ERROR] Re-encoding failed for {file_path} (code {process.returncode})\n")
        return False

    return temp_output


async def is_video_hevc_or_av1(file_path: str, warn_CRF_match: bool, re_encode_hevc_CRF: int) -> bool:
    """
    Check if the video is encoded with HEVC or AV1 using pymediainfo.
    Log codec and CRF if detected.
    Return True if HEVC or AV1, False otherwise.
    """

    if not os.path.isfile(file_path):
        logger.error(f"File does not exist: {file_path}")
        return False

    try:
        media_info = MediaInfo.parse(file_path)
    except Exception as e:
        logger.error(f"Failed to parse file with pymediainfo: {file_path}: {e}")
        return False

    video_track = None
    for track in media_info.tracks:
        if track.track_type == "Video":
            video_track = track
            break

    if not video_track:
        logger.error(f"No video track found: {file_path}")
        return False

    codec = (video_track.format or "").lower()
    codec_clean = codec.replace(" ", "").replace("-", "")

    is_hevc = codec_clean in {"hevc", "hvc1", "hev1"}
    is_av1 = codec_clean == "av1"

    # Extract CRF from encoding settings
    crf_value = None
    encoding_settings = getattr(video_track, "encoding_settings", "") or ""

    if encoding_settings:
        # Look for patterns like `crf=24.0`
        match = re.search(r"crf\s*=\s*([0-9]+(?:\.[0-9]+)?)", encoding_settings.lower())
        if match:
            # Convert 24.0 → 24
            crf_value = int(float(match.group(1)))
            logger.info(f"Detected CRF {crf_value} for {file_path}")


    # Log HEVC / AV1 detection
    if is_hevc:
        if crf_value:
            if crf_value != re_encode_hevc_CRF and warn_CRF_match:
                logger.warning(f"CRF value of file: {file_path} does not match configured CRF value - file: {crf_value} vs Configured CRF value: {re_encode_hevc_CRF}")
        else:
            logger.warning(f"CRF value not found in file: {file_path}")
        logger.info(f"{file_path} detected as HEVC (H.265). CRF={crf_value}")
        return True

    if is_av1:
        logger.info(f"{file_path} detected as AV1. CRF={crf_value}")
        return True

    # logger.info(f"{file_path} codec is '{codec}', not HEVC/AV1")
    return False


async def get_video_duration(filepath):
    """Returns duration of the video in seconds using OpenCV, rounded to 1 decimal place."""
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        logger.error(f"Failed to open video file: {filepath}")
        return 0.0, 0.0

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0:
        logger.error(f"Invalid FPS value for file: {filepath}")
        return 0.0, 0.0

    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()

    if fps == 0:
        logger.error(f"Invalid FPS value for file: {filepath}")
        return 0.0, 0.0

    duration = frame_count / fps  # float division
    duration = round(duration, 1)  # round to 1 decimal place
    return duration, fps


async def get_video_fps(video_path: str) -> float:
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise IOError(f"Failed to open video file: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return round(fps)


async def get_video_resolution_and_orientation(video_path: str) -> tuple[str, bool]:
    """
    Returns (resolution_label, is_vertical)
    resolution_label = "2160p", "1440p", "1080p", "720p", or "<height>p"
    is_vertical = True if displayed height > width (rotation corrected)
    """

    # ffprobe JSON query including optional rotation
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,rotation",
        "-of", "json",
        video_path
    ]

    stdout, stderr, code = await run_command(cmd)

    if code != 0:
        raise IOError(f"ffprobe failed: {stderr or stdout}")

    import json
    info = json.loads(stdout)

    stream = info["streams"][0]

    width = int(stream.get("width"))
    height = int(stream.get("height"))

    # rotation is optional — if missing, assume 0°
    rotation = int(stream.get("rotation", 0)) % 180

    # Correct for rotation (90°/270° means displayed width/height are swapped)
    if rotation == 90:
        width, height = height, width

    # Determine orientation
    is_vertical = height > width

    # Resolution is always based on **height**
    if height >= 2160:
        resolution = "2160p"
    elif height >= 1440:
        resolution = "1440p"
    elif height >= 1080:
        resolution = "1080p"
    elif height >= 720:
        resolution = "720p"
    elif height <= 719:
        resolution = "SD"
    else:
        resolution = f"{height}p"

    return resolution, is_vertical


def parse_ffmpeg_time(time_str):
    """Convert HH:MM:SS.xx to seconds."""
    try:
        parts = time_str.split(':')
        seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return seconds
    except Exception:
        return 0


def format_eta(seconds):
    """Format seconds into human-readable ETR like '2m 15s'."""
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    elif minutes:
        return f"{minutes}m {sec}s"
    else:
        return f"{sec}s"


def format_size(size_kib):
    """Format file size in MB or KB based on the size."""
    if size_kib < 1024:
        return f"{size_kib} KB"
    size_mb = size_kib / 1024
    return f"{size_mb:.0f} MB"


def format_bitrate(size_kib, elapsed_time):
    """Calculate the bitrate from size and elapsed time and format it."""
    if elapsed_time <= 0:
        return "N/A"
    bitrate_kbps = (size_kib * 8) / elapsed_time
    return f"{bitrate_kbps / 1000:.2f} Mbps"


async def get_video_resolution(file_path):
    """Get the video resolution and bitrate using ffprobe."""
    cmd = (
        f'ffprobe -v error -select_streams v:0 '
        f'-show_entries stream=width,height,bit_rate '
        f'-of json "{file_path}"'
    )
    stdout, stderr, code = await run_command(cmd)
    if code != 0:
        logger.error(f"Failed to get resolution/bitrate for {file_path}. Error: {stderr}")
        return None, None, None

    try:
        data = json.loads(stdout)
        stream = data["streams"][0]
        width = stream.get("width")
        height = stream.get("height")
        bitrate = stream.get("bit_rate", 0)
        if bitrate is None:
            bitrate = 0
        return width, height, int(bitrate)
    except Exception as e:
        logger.exception(f"Error parsing resolution/bitrate for {file_path}: {e}")
        return None, None, None


async def generate_temp_filename(directory, original_name):
    """Generate a temporary filename for re-encoded file."""
    name, ext = os.path.splitext(original_name)
    return os.path.join(directory, f"{name}_temp{ext}")


async def get_video_codec(file_path):
    """Return 'avc', 'hevc', or 'av1' if the codec is supported; otherwise return None."""
    command = (
        f'ffprobe -v error -select_streams v:0 '
        f'-show_entries stream=codec_name '
        f'-of default=noprint_wrappers=1:nokey=1 "{file_path}"'
    )

    try:
        stdout, stderr, code = await run_command(command)
        codec_name = stdout.strip().lower()

        # Map common codec names to the desired labels
        codec_map = {
            "h264": "avc",
            "avc1": "avc",
            "hevc": "hevc",
            "h265": "hevc",
            "hev1": "hevc",
            "av01": "av1",
        }

        if codec_name in codec_map:
            return codec_map[codec_name]
        else:
            logger.warning(f"Codec '{codec_name}' is not supported for {file_path}")
            return None

    except Exception as e:
        logger.error(f"Error getting codec for {file_path}: {e}")
        return None


async def add_mp4_chapters(
        input_file: str,
        chapters_list: List[Dict],
        run_command_func,
        title,
        description,
        tpdb_id,
        matching_mode, set_exclusive, exclusive_string
) -> bool:
    logger.debug("Chapters detected, Chapters will be applied and only then metadata will be applied.")
    # logger.debug(f"Incoming chapters_list: {chapters_list}")

    if not chapters_list:
        # logger.debug("No chapters provided → returning True")
        return True

    input_path = Path(input_file)
    output_path = input_path.with_suffix(".chapters_tmp.mp4")

    try:
        # Sort chapters
        sorted_chapters = sorted(
            chapters_list,
            key=lambda x: x["start_time"]
        )

        # logger.debug(f"Sorted chapters: {sorted_chapters}")

        # Insert beginning chapter if needed
        if sorted_chapters and sorted_chapters[0]["start_time"] > 0:
            # logger.debug("Inserting synthetic 0-start beginning chapter")
            sorted_chapters.insert(0, {
                "title": "",
                "start_time": 0
            })

        # logger.debug(f"Chapters after beginning check: {sorted_chapters}")

        duration, _ = await get_video_duration(input_file)
        if not duration:
            logger.error("Could not read duration from media info")
            return False

        # Build metadata
        metadata_lines = [";FFMETADATA1"]

        for i, chapter in enumerate(sorted_chapters):
            start = float(chapter["start_time"])

            if i + 1 < len(sorted_chapters):
                end = float(sorted_chapters[i + 1]["start_time"])
            else:
                end = duration

            # logger.debug(f"Chapter {i}: start={start}, end={end}, title={chapter['title']}")

            metadata_lines.extend([
                "[CHAPTER]",
                "TIMEBASE=1/1",
                f"START={int(start)}",
                f"END={int(end)}",
                f"title={chapter['title']}",
            ])

        metadata_content = "\n".join(metadata_lines)

        # logger.debug("Generated ffmetadata content:")
        # logger.debug(metadata_content)

        # Write temp metadata file
        with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".txt",
                mode="w",
                encoding="utf-8"
        ) as tmp_meta:
            tmp_meta.write(metadata_content)
            tmp_meta_path = tmp_meta.name

        # logger.debug(f"Temporary metadata file created: {tmp_meta_path}")

        # ffmpeg command
        # -map_metadata 1 pulls global metadata from the FFMETADATA file (input 1)
        # instead of the original video (input 0). Since that file only ever
        # contains ";FFMETADATA1" + "[CHAPTER]" blocks and nothing else, this is
        # equivalent to stripping all unwanted global metadata (encoded_date,
        # tagged_date, copyright, etc.) while embedding chapters, in a single pass.
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-i", tmp_meta_path,
            "-map_metadata", "1",
            "-map_chapters", "1",
            "-codec", "copy",
            str(output_path)
        ]

        # logger.debug(f"Running ffmpeg command: {ffmpeg_cmd}")

        stdout, stderr, rc = await run_command_func(ffmpeg_cmd)

        # logger.debug(f"ffmpeg rc={rc}")
        # logger.debug(f"ffmpeg stdout={stdout}")
        # logger.debug(f"ffmpeg stderr={stderr}")

        if rc != 0:
            logger.error(f"ffmpeg failed: {stderr}")
            return False

        if not output_path.exists():
            logger.error("Output file was not created!")
            return False

        os.replace(output_path, input_path)
        # logger.debug("Chapter file successfully replaced original, global metadata stripped")

        video = MP4(input_file)
        await apply_mp4_metadata(video, title, description, tpdb_id, set_exclusive, exclusive_string, matching_mode)
        video.save()

        return True

    except Exception:
        logger.exception(f"Failed to add chapters to {input_file}")
        return False

    finally:
        try:
            if 'tmp_meta_path' in locals() and os.path.exists(tmp_meta_path):
                # logger.debug(f"Cleaning up temp file: {tmp_meta_path}")
                for attempt in range(3):
                    try:
                        os.remove(tmp_meta_path)
                        # logger.debug("Temp file removed successfully")
                        break
                    except Exception as e:
                        logger.warning(f"Temp cleanup attempt {attempt + 1} failed: {e}")
                        await asyncio.sleep(0.5)
        except Exception:
            pass


async def update_metadata(input_file, title, description, tpdb_id, matching_mode, add_timestamps_markers, chapters_list, set_exclusive, exclusive_string):
    """
    Updates the metadata of an MP4 video file with the specified title and description,
    and removes unwanted fields completely.
    """
    await asyncio.sleep(0.5)
    # logger.debug(input_file)

    try:
        if add_timestamps_markers and chapters_list:
            success = await add_mp4_chapters(
                input_file,
                chapters_list,
                run_command,
                title,
                description,
                tpdb_id,
                matching_mode, set_exclusive, exclusive_string
            )
            if not success:
                return False

        else:
            logger.debug("No chapters detected, metadata will be applied.")

            video = MP4(input_file)
            await apply_mp4_metadata(video, title, description, tpdb_id, set_exclusive, exclusive_string, matching_mode)
            video.save()

        return True

    except Exception as e:
        logger.error(f"Failed to update metadata for {input_file}: {e}")
        return False


async def apply_mp4_metadata(video, title, description, tpdb_id, set_exclusive, exclusive_string, matching_mode):
    video["\xa9nam"] = [title]

    if matching_mode != "full_manual":
        video["\xa9cmt"] = [description]
        video["\xa9alb"] = [tpdb_id]

    for key in ["\xa9cpy", "ldes", "tven", "\xa9ART"]:
        if key in video:
            del video[key]

    if not set_exclusive:
        if "cprt" in video:
            del video["cprt"]
    else:
        video["cprt"] = [exclusive_string]

    video["\xa9too"] = ["File_Prepare_HF"]


async def reset_all_metadata(file_path: str) -> bool:
    """
    Recreates the file without any metadata using FFmpeg.
    Optionally preserves specific metadata passed in `preserve_metadata` dictionary.
    :param file_path: Path to the MP4 file
    :return: True if successful, False otherwise
    """
    try:
        original = Path(file_path)
        tmp_file = Path(str(file_path) + ".tmp.mp4")
        backup_file = Path(str(file_path) + ".backup.mp4")

        # --- Build ffmpeg command ---
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            file_path,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c", "copy",
            "-map_metadata", "-1",
            str(tmp_file)
                      ]

        stdout, stderr, returncode = await run_command(ffmpeg_cmd)
        if returncode != 0:
            logger.error(f"FFmpeg failed to recreate {file_path} without metadata:\n{stderr}")
            # Ensure tmp file is removed if FFmpeg failed
            if tmp_file.exists():
                tmp_file.unlink()
            return False

        # --- Backup original file before replacement ---
        if original.exists():
            shutil.move(str(original), str(backup_file))

        # --- Replace original file with temp file ---
        shutil.move(str(tmp_file), str(original))
        logger.info(f"File successfully recreated without unwanted metadata: {file_path}")

        # --- Remove backup if everything went well ---
        if backup_file.exists():
            backup_file.unlink()

        return True

    except Exception as e:
        logger.error(f"Error recreating file without metadata {file_path}: {e}")

        # Restore from backup if something went wrong
        if backup_file.exists():
            if original.exists():
                original.unlink(missing_ok=True)
            shutil.move(str(backup_file), str(original))
            logger.warning(f"Original file restored from backup after failure: {file_path}")

        # Cleanup temp file
        if tmp_file.exists():
            tmp_file.unlink()

        return False

