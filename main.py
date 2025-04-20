import os
import asyncio
import re
import sys
from datetime import datetime
from loguru import logger
from pathlib import Path
from Utilities import verify_ffmpeg_and_ffprobe, load_config, pre_process_files, validate_date, format_performers, sanitize_site_filename_part, rename_file, \
    generate_mediainfo_file, generate_template_video, is_version_between
from TPDB_API_Processing import get_data_from_api
from Media_Processing import get_existing_title, get_existing_description, image_download_and_conversion, generate_scorp_thumbnails_and_conversion, \
    generate_performer_profile_picture, re_encode_video, update_metadata, get_video_fps, get_video_resolution_and_orientation, get_video_codec
from Preview_Tool import create_preview_tool


async def is_supported_major_minor(min_major_minor, max_major_minor) -> bool:
    current_major, current_minor = sys.version_info[:2]
    min_major, min_minor = min_major_minor
    max_major, max_minor = max_major_minor

    return (min_major, min_minor) <= (current_major, current_minor) <= (max_major, max_minor)


async def process_files():
    # Load Config file
    config, exit_code = await load_config("Config.json")
    if not config:
        exit(exit_code)
    else:
        generate_scorp_thumbnails = config["generate_scorp_thumbnails"]
        scorp_exe_path = config["scorp_thumbnails_maker_path"]
        download_cover_image = config["download_cover_image"]
        generate_mediainfo = config["generate_mediainfo"]
        generate_preview = config["generate_preview"]
        mediaarea_mediainfo_path = config["mediaarea_mediainfo_path"]
        directory = config["working_path"]
        manual_mode = config["manual_mode"]
        generate_face_portrait_pic = config["generate_face_portrait_pic"]
        tpdb_performer_url = config["tpdb_performer_url"]
        tpdb_scenes_url = config["tpdb_scenes_url"]
        target_size_width = config["target_size_width"]
        target_size_height = config["target_size_height"]
        target_size = (target_size_width, target_size_height)
        zoom_factor = config["zoom_factor"]
        blur_kernel_size = config["blur_kernel_size"]
        re_encode_hevc = config["re_encode_hevc"]
        keep_original_file = config["keep_original_file"]
        posters_limit = config["posters_limit"]
        generate_hf_template = config["generate_hf_template"]
        template_file_name = config["template_name"]
        re_encode_downscale = config["re_encode_downscale"]
        python_min_version_supported = tuple(config["python_min_version_supported"])
        python_max_version_supported = tuple(config["python_max_version_supported"])
        code_version = config["Code_Version"]
        bad_words = config["bad_words"]

    if await is_supported_major_minor(python_min_version_supported, python_max_version_supported):
        logger.debug(f"✅ Python {sys.version.split()[0]} is within supported range {python_min_version_supported} to {python_max_version_supported}.")
    else:
        logger.error(f"❌ Python {sys.version.split()[0]} is NOT within supported range {python_min_version_supported} to {python_max_version_supported}.")
        exit(36)

    if generate_face_portrait_pic:
        from mtcnn import MTCNN
    else:
        MTCNN = None
    template_file_full_path = None
    if generate_hf_template:
        if template_file_name != "":
            template_file_full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), template_file_name)
            if not os.path.exists(template_file_full_path):
                logger.error(f"Invalid template file path: {template_file_full_path}")
                exit(35)
        elif not generate_mediainfo:
            logger.error("Conflict in configration, in order to generate HFtemplate file, generating media info file is a must")
            exit(37)
        else:
            logger.error(f"Invalid template file name: {template_file_name}")
            exit(34)

    # Verify working path
    if not os.path.isdir(directory):
        logger.error("Please enter a valid directory path.")
        exit(27)

    # Start Pre Processing files
    logger.info("-" * 100)
    logger.info(f"Start pre processing in directory: {directory}")
    pre_process_results, exit_code = await pre_process_files(directory, bad_words)
    if not pre_process_results:
        logger.error("An error has occurred during preprocessing, please review input files.")
        exit(exit_code)
    # logger.debug(f"Finish pre processing in directory: {directory}")

    # Start Processing files
    logger.info("-" * 100)
    logger.info(f"Start processing in directory: {directory}")

    total_files, processed_files = 0, 0
    failed_files, successful_files = [], []

    # First pass: Count only .mp4 files in the given directory (no sub_folders)
    total_files = len([
        f for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f)) and f.lower().endswith(".mp4")
    ])
    logger.info(f"Total amount of files: {total_files}")

    # Second pass: Get the list of .mp4 files in the same directory (not sub_folders)
    mp4_files = [
        f for f in Path(directory).iterdir()
        if f.is_file() and f.suffix.lower() == ".mp4"
    ]
    for file in mp4_files:
        try:
            filename = str(file.name)  # Get the full filename (with extension)
            filename_base_name = str(file.stem)  # Get the filename without extension
            extension = str(file.suffix)  # Get the file extension (including the dot)
            file_path = str(file.parent)  # Get the directory path
            logger.info(f"Start file: {file}, file {processed_files + 1} out of {total_files}")

            # Initialize flags
            vr2normal = upscaled = bts_video = False

            # Define a dictionary for easier mapping
            file_flags = {
                "vr2normal": False,
                "upscaled": False,
                "bts": False
            }

            # Check for the flags in the filename and clean the name accordingly
            clean_tpdb_check_filename = filename_base_name
            for flag in file_flags.keys():
                if f".{flag.lower()}" in str(file).lower():
                    file_flags[flag] = True
                    clean_tpdb_check_filename = re.sub(re.escape(flag), "", clean_tpdb_check_filename, flags=re.IGNORECASE)

            # Assign the flags based on the results
            vr2normal, upscaled, bts_video = file_flags["vr2normal"], file_flags["upscaled"], file_flags["bts"]

            # Check for Part in file base name
            match = re.search(r"\.part\.\d+", filename_base_name, re.IGNORECASE)
            if match:
                part_number = match.group(0)
            else:
                part_number = ""

            # Split filename into parts
            parts = filename_base_name.split('.')

            # Start working on filename
            if len(parts) >= 4:
                year = parts[1]
                month = parts[2]
                day = parts[3]
                is_valid, exit_code = await validate_date(year, month, day)
                if not is_valid:
                    logger.error(f"Invalid date in filename: {filename_base_name}, moving to next file")
                    continue  # Skip to the next file
            else:
                logger.error(f"Invalid filename format: {filename_base_name}, moving to next file")
                continue  # Skip to the next file

            year_name = "20" + year  # Convert to 4 digit for scene identification purposes
            # Convert month to Name

            scene_api_date = f"{year_name}-{month}-{day}"

            new_title, performers, image_url, slug, scene_url, tpdb_image_url, tpdb_site, site_studio, scene_description, scene_date = await get_data_from_api(
                clean_tpdb_check_filename, scene_api_date, manual_mode, tpdb_scenes_url)
            if all(value is None for value in (new_title, performers, image_url, slug, scene_url, tpdb_image_url, tpdb_site, site_studio, scene_description)):
                # All values are None
                failed_files.append(filename)
                continue
            if scene_date != scene_api_date:
                year_name, month, day = scene_date.split("-")
                year = year_name[-2:]

            if scene_description is None:
                scene_description = "Scene description not found"

            month_name = datetime.strptime(month, "%m").strftime("%B")
            scene_pretty_date = f"{year_name}-{month_name}-{day}"
            tpdb_scene_url = tpdb_scenes_url + slug
            error_prefix = f"File: {filename} - Failed to get metadata via API"

            if not new_title or new_title == "Multiple results":
                logger.error(f"{error_prefix} - missing or ambiguous title")
                raise ValueError(f"Unable to find a valid title for {filename}")

            if not performers or performers == "Invalid":
                logger.error(f"{error_prefix} - missing or invalid performers")
                raise ValueError(f"Unable to find valid performers for {filename}")
        except Exception as e:
            logger.error(f"Error in API data for file: {file} - {str(e)}")
            logger.info(f"End file: {filename}")
            failed_files.append(str(file))
            continue  # Skip to the next file

        formatted_filename_performers_names = await format_performers(performers, 2)  # This includes sanitization of performer names
        formatted_site = await sanitize_site_filename_part(tpdb_site)

        # Determine the suffix based on video type
        if vr2normal:
            suffix = "VR2Normal"
        elif bts_video:
            suffix = "BTS"
        elif upscaled:
            suffix = "Upscaled"
        else:
            suffix = ""

        # Construct new filename
        new_filename = f"{formatted_site}.{year}.{month}.{day}.{formatted_filename_performers_names}{part_number}"
        if suffix:
            new_filename += f".{suffix}"
        new_filename += extension

        # Format performer names
        formatted_names = await format_performers(performers, 1)

        # Construct new title
        studio_info = f"{tpdb_site}({site_studio})" if tpdb_site != site_studio else tpdb_site
        new_title_parts = [studio_info, scene_pretty_date, new_title, formatted_names]
        if suffix:
            new_title_parts.append(suffix)
        new_title = " - ".join(new_title_parts)

        # Rename existing file to new filename if needed
        new_file_full_path = os.path.join(directory, new_filename)
        if str(file) != str(new_file_full_path):
            await rename_file(str(file), new_filename)

        try:
            # Check existing metadata
            existing_title = await get_existing_title(new_file_full_path)
            existing_description = await get_existing_description(new_file_full_path)
            description = f"TPDB URL: {tpdb_scene_url} | Scene URL: {scene_url}"
            if existing_title == new_title and existing_description == description:
                # logger.debug(f"File: {file.name} - Title and Description already exist and are identical, no need to rename")
                pass
            else:
                # logger.info(f"File: {file.name} - Title and Description already exist and are identical")
                results_metadata = await update_metadata(new_file_full_path, new_title, description, re_encode_hevc)
                if not results_metadata:
                    logger.error(f"Failed to modify file: {new_filename}")
                    failed_files.append(new_file_full_path)
                    continue  # Skip to the next file

            new_filename_base_name, extension = os.path.splitext(new_filename)
            fps = await get_video_fps(new_file_full_path)
            resolution, is_vertical = await get_video_resolution_and_orientation(new_file_full_path)
            codec = await get_video_codec(new_file_full_path)

            # Define all optional steps and their corresponding conditions and functions
            optional_steps = [
                (re_encode_hevc, re_encode_video, [new_filename, directory, keep_original_file, is_vertical, re_encode_downscale]),
                (download_cover_image, image_download_and_conversion, [image_url, tpdb_image_url, filename_base_name, new_filename_base_name, file_path]),
                (generate_scorp_thumbnails, generate_scorp_thumbnails_and_conversion, [new_filename_base_name, directory, filename_base_name, scorp_exe_path]),
                (generate_mediainfo, generate_mediainfo_file, [new_file_full_path, mediaarea_mediainfo_path]),
                (generate_preview, create_preview_tool, [new_file_full_path, directory, new_filename_base_name]),
                (generate_face_portrait_pic, generate_performer_profile_picture,
                 [performers, directory, tpdb_performer_url, target_size, zoom_factor, blur_kernel_size, posters_limit, MTCNN]),
                (generate_hf_template, generate_template_video,
                 [new_title, scene_pretty_date, scene_description, formatted_names, fps, resolution, is_vertical, codec, extension, directory, new_filename_base_name,
                  template_file_full_path, code_version]),
            ]

            # Run each enabled optional step
            for flag, func, args in optional_steps:
                if flag:
                    result = await func(*args)
                    if not result:
                        logger.info(f"End file: {filename}")
                        if new_file_full_path not in failed_files:
                            failed_files.append(new_file_full_path)
                        continue  # Skip to the next file
            logger.info(f"End file: {filename}")
            successful_files.append(new_file_full_path)
        except Exception as e:
            logger.exception(f"Error in Data manipulation for file: {new_file_full_path} - {str(e)}")
            logger.info(f"End file: {filename}")
            failed_files.append(str(file))
            continue  # Skip to the next file

    # Finished processing
    logger.info("-" * 100)
    logger.info(f"Finish processing in directory: {directory}")
    for success in successful_files:
        logger.info(f"Successful file: {success}")
    for failed in failed_files:
        logger.warning(f"Failed file: {failed}")


if __name__ == "__main__":
    logger.add("App_Log_{time:YYYY.MMMM}.log", rotation="30 days", backtrace=True, enqueue=False, catch=True)  # Load Logger
    ffmpeg_ffprobe_results, ff_exit_code = asyncio.run(verify_ffmpeg_and_ffprobe())
    if not ffmpeg_ffprobe_results:
        exit(ff_exit_code)
    asyncio.run(process_files())
