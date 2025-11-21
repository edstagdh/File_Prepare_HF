import importlib
import os
import asyncio
import re
import shutil
import sys
from datetime import datetime
from loguru import logger
from pathlib import Path
from Utilities import verify_ffmpeg_and_ffprobe, load_json_file, pre_process_files, validate_date, format_performers, sanitize_site_filename_part, rename_file, \
    generate_mediainfo_file, generate_template_video, is_supported_major_minor, clean_filename
from TPDB_API_Processing import get_data_from_api
from Media_Processing import get_existing_title, get_existing_description, cover_image_download_and_conversion, \
    generate_performer_profile_picture, re_encode_video, update_metadata, get_video_fps, get_video_resolution_and_orientation, get_video_codec, has_unwanted_metadata, \
    reset_all_metadata
from Generate_Video_Preview import process_video_preview
from Generate_Thumbnails_Sheet import process_thumbnails
from Image_Uploaders.Upload_IMGBOX import imgbox_upload_single_image
from Image_Uploaders.Upload_IMGBB import imgbb_upload_single_image
from Image_Uploaders.Upload_Hamster import hamster_upload_single_image
from Tracker_Uploader import process_upload_to_tracker


async def process_files():
    # Load Config file
    config, exit_code = await load_json_file("Configs/Config.json")
    if not config:
        exit(exit_code)
    else:
        # Working Mod, By default scenes is used
        jav_api_mode = config.get("jav_only_api_mode")

        # Generate flags, Note - HF Template generation will not work if mediainfo file is set to not generate
        create_cover_image = config["create_cover_image"]
        create_thumbnails = config["create_thumbnails"]
        create_video_preview = config["create_video_preview"]
        create_face_portrait_pic = config["create_face_portrait_pic"]
        create_mediainfo = config["create_mediainfo"]
        create_template_file = config["create_template_file"]

        # Additional Configuration:
        directory = config["working_path"]
        manual_mode = config["manual_mode"]
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
        template_file_name = config["template_name"]
        re_encode_downscale = config["re_encode_downscale"]
        limit_cpu_usage = config["limit_cpu_usage"]
        remove_chapters = config["remove_chapters"]
        python_min_version_supported = tuple(config["python_min_version_supported"])
        python_max_version_supported = tuple(config["python_max_version_supported"])
        code_version = config["Code_Version"]
        bad_words = config["bad_words"]
        use_title = config["use_title"]
        performer_image_output_format = config["performer_image_output_format"].lower()
        font_full_name = config["font_full_name"]
        ignore_list = config["ignore_list"]
        filename_ignore_part_x = config["filename_ignore_part_x"]
        filename_ignore_performer_ID = config["filename_ignore_performer_ID"]
        free_string_parse = config["free_string_parse"]
        create_sub_folder = config["create_sub_folder"]
        cover_regeneration_mode = config["cover_regeneration_mode"]

        # Tracker Upload Configuration
        upload_mode = config["upload_mode"]
        tracker_mode = config["tracker_mode"]
        upload_to_tracker = config["upload_to_tracker"]
        remove_e_files = config["remove_extra_files_after_tracker_upload"]

        # Upload Configuration
        imgbox_upload_cover = config["imgbox_upload_cover"]
        imgbox_upload_thumbnails = config["imgbox_upload_thumbnails"]
        imgbb_upload_cover = config["imgbb_upload_cover"]
        imgbb_upload_previews = config["imgbb_upload_previews"]
        imgbb_upload_thumbnails = config["imgbb_upload_thumbnails"]
        imgbb_upload_headless_mode = config["imgbb_upload_headless_mode"]
        hamster_upload_cover = config["hamster_upload_cover"]
        hamster_upload_previews = config["hamster_upload_previews"]
        hamster_upload_thumbnails = config["hamster_upload_thumbnails"]
        image_output_format = config["image_output_format"].lower()

        # Notifiers Configuration
        use_notifier = config["use_notifier"]
        notifier_name = config["notifier_name"]

    if await is_supported_major_minor(python_min_version_supported, python_max_version_supported):
        logger.debug(f"✅ Python {sys.version.split()[0]} is within supported range {python_min_version_supported} to {python_max_version_supported}.")
    else:
        logger.error(f"❌ Python {sys.version.split()[0]} is NOT within supported range {python_min_version_supported} to {python_max_version_supported}.")
        exit(36)

    if create_face_portrait_pic:
        from mtcnn import MTCNN
    else:
        MTCNN = None
    template_file_full_path = None

    if use_notifier:
        # Directory where this script resides
        base_dir = os.path.dirname(os.path.abspath(__file__))
        notifiers_dir = os.path.join(base_dir, "Notifiers")

        # Add Notifiers folder to sys.path so Python can import from it
        if notifiers_dir not in sys.path:
            sys.path.insert(0, notifiers_dir)

        if not notifier_name or notifier_name.strip() == "":
            logger.error("Notifier name is empty while notifier usage is enabled")
            exit(44)

        full_notifier_path = os.path.join(notifiers_dir, notifier_name)

        if not os.path.exists(full_notifier_path):
            logger.error(f"Notifier({notifier_name}) code file not found")
            exit(45)

        try:
            module_name = notifier_name.replace(".py", "")
            notifier_module = importlib.import_module(module_name)
            send_notification = notifier_module.send_notification

        except Exception as e:
            logger.error(f"Failed to import notifier '{notifier_name}': {e}")
            exit(46)
    else:
        send_notification = None

    if image_output_format not in ["webp", "jpeg", "jpg", "bmp", "png"]:
        logger.error(f"image output format not valid")
        exit(39)

    if upload_to_tracker:
        if not create_sub_folder:
            exit(40)
        elif upload_mode.lower() != "single":
            exit(41)
        elif not create_template_file:
            exit(42)
        elif not hamster_upload_cover or not hamster_upload_thumbnails or not hamster_upload_previews:
            exit(43)

    if create_template_file:
        if template_file_name != "":
            # Add "Resources" folder before the filename
            template_file_full_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "Resources",
                template_file_name
            )
            if not os.path.exists(template_file_full_path):
                logger.error(f"Invalid template file path: {template_file_full_path}")
                exit(35)
        elif not create_mediainfo:
            logger.error("Conflict in configuration, in order to generate HFtemplate file, generating media info file is a must")
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
    pre_process_results, exit_code = await pre_process_files(directory, bad_words, free_string_parse, mode=1)
    if not pre_process_results:
        logger.error("An error has occurred during preprocessing, please review input files.")
        exit(exit_code)
    # logger.debug(f"Finish pre processing in directory: {directory}")

    # Start Processing files
    logger.info("-" * 100)
    logger.info(f"Start processing in directory: {directory}")

    total_files, processed_files = 0, 0
    failed_files, successful_files, partial_files = [], [], []

    # First pass: Count only .mp4 files in the given directory (no sub_folders)
    total_files = len([
        f for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f)) and f.lower().endswith(".mp4")
    ])
    logger.info(f"Total amount of files: {total_files}")

    # Second pass: Get the list of .mp4 files in the same directory (not sub_folders)
    mp4_files = [
        f for f in Path(directory).iterdir()
        if f.is_file()
           and f.suffix.lower() == ".mp4"
           and not f.name.lower().endswith("_old.mp4")
           and not f.name.lower().endswith("_temp.mp4")
           and f.name not in ignore_list
    ]
    mp4_files = sorted(mp4_files, key=lambda f: f.name.lower())
    for file in mp4_files:
        force_regen_thumbs = False
        await asyncio.sleep(0.1)
        file_full_name = str(file.name)  # Get the full file_full_name (with extension)
        file_base_name = str(file.stem)  # Get the file_full_name without extension
        file_extension = str(file.suffix)
        if not Path(file).exists():
            logger.error(f"Failed to find file: {file_full_name}, moving to next file")
            logger.error(f"End file: {file_full_name}")
            failed_files.append(file_full_name)
            continue  # Skip to the next file

        try:
            if create_sub_folder:
                output_directory = os.path.join(directory, file_base_name)
                os.makedirs(output_directory, exist_ok=True)
            else:
                output_directory = directory
            logger.info(f"Start file: {file}, file {processed_files + 1} out of {total_files}")

            # Define flags and initialize them
            flag_names = ["vr2normal", "upscaled", "bts", "pov", "vertical", "trailer"]
            file_flags = {flag: False for flag in flag_names}

            # Prepare lowercase filename and split by '.'
            file_lower = str(file).lower()
            file_parts = file_lower.split(".")

            # Detect flags based on exact token matches
            for flag in flag_names:
                if flag in file_parts:
                    file_flags[flag] = True

            # Build cleaned filename without flag tokens
            clean_tpdb_check_filename = ".".join(
                part for part in file_parts if part not in flag_names
            )

            # Unpack flags
            vr2normal, upscaled, bts_video, pov, vertical, trailer = (
                file_flags[flag] for flag in flag_names
            )

            # Continue with your logic
            if free_string_parse:
                if any(file_flags.values()):
                    file_base_name = clean_tpdb_check_filename
                # Query scene data from API
                new_title, performers_names, image_url, slug, scene_url, tpdb_image_url, tpdb_site, site_studio, scene_description, scene_date, scene_tags = await get_data_from_api(
                    file_base_name,
                    None,
                    None,
                    tpdb_scenes_url,
                    None,
                    create_template_file,
                    jav_api_mode,
                    filename_ignore_performer_ID,
                    send_notification,
                    mode=1
                )

            else:

                # Check for Part in file base name
                part_match = re.search(r"\.part\.\d+", file_base_name, re.IGNORECASE)
                part_number = part_match.group(0) if part_match else ""

                # Split file_full_name into parts
                parts = file_base_name.split('.')

                # Start working on file_full_name
                if len(parts) < 4:
                    logger.error(f"Invalid file_full_name format: {file_base_name}, moving to next file")
                    logger.warning(f"End file: {file_full_name}")
                    failed_files.append(file_full_name)
                    continue  # Skip to the next file

                year, month, day = parts[1], parts[2], parts[3]
                is_valid, exit_code = await validate_date(year, month, day)

                if not is_valid:
                    logger.error(f"Invalid date in file_full_name: {file_base_name}, moving to next file")
                    logger.warning(f"End file: {file_full_name}")
                    failed_files.append(file_full_name)
                    continue  # Skip to the next file

                # Convert to 4-digit year for scene identification
                year_full = f"20{year}"
                scene_api_date = f"{year_full}-{month}-{day}"

                # Query scene data from API
                new_title, performers_names, image_url, slug, scene_url, tpdb_image_url, tpdb_site, site_studio, scene_description, scene_date, scene_tags = await get_data_from_api(
                    clean_tpdb_check_filename,
                    scene_api_date,
                    manual_mode,
                    tpdb_scenes_url,
                    part_match,
                    create_template_file,
                    jav_api_mode,
                    filename_ignore_performer_ID,
                    send_notification,
                    mode=2
                )

            # Prepare critical fields dictionary
            critical_fields = {
                "new_title": new_title,
                "performers": performers_names,
                "image_url": image_url,
                "slug": slug,
                "scene_url": scene_url,
                "tpdb_image_url": tpdb_image_url,
                "tpdb_site": tpdb_site,
                "site_studio": site_studio,
                "scene_description": scene_description
            }

            # List of sites to ignore for tpdb_site being None
            ignored_tpdb_sites = ["onlyfans", "manyvids", "fansly"]

            none_fields = {}
            for key, value in critical_fields.items():
                if value is None:
                    # Skip site_studio if tpdb_site contains one of the ignored keywords
                    if key == "site_studio" and tpdb_site and any(keyword in tpdb_site.lower() for keyword in ignored_tpdb_sites):
                        continue
                    none_fields[key] = value

            if none_fields:
                logger.warning(
                    f"Missing metadata for {file_full_name}: "
                    f"{', '.join([f'{k}=None' for k in none_fields.keys()])}"
                )

            if all(value is None for value in critical_fields.values()):
                logger.error(f"Failed to find a match via TPDB for file: {file_full_name}")
                logger.warning(f"End file: {file_full_name}")
                failed_files.append(file_full_name)
                continue  # Skip to the next file

            # Regex: match 'Part' (case-insensitive), optional spaces, then capture digits
            match = re.search(r"\bPart\s*(\d+)\b", new_title, re.IGNORECASE)
            if match and not filename_ignore_part_x:
                part_number = match.group(1)  # the number after 'Part'
                pre_suffix = f"Part.{part_number}"
                logger.info(f"Detected Part in title: {pre_suffix}")
            else:
                pre_suffix = None

            # Adjust year/month/day so scene date will always come from database
            year_full, month, day = scene_date.split("-")
            year = year_full[-2:]

            # Provide fallback for missing description
            if not scene_description:
                scene_description = "Scene description not found"

            # Format month as full name and prepare scene date string
            month_name = datetime.strptime(month, "%m").strftime("%B")
            scene_pretty_date = f"{year_full}-{month_name}-{day}"

            # Construct scene URL and error prefix
            tpdb_scene_url = f"{tpdb_scenes_url}{slug}"
            error_prefix = f"File: {file_full_name} - Failed to get metadata via API"

            # Validate title
            if not new_title or new_title == "Multiple results":
                logger.error(f"{error_prefix} - missing or ambiguous title")
                raise ValueError(f"Unable to find a valid title for {file_full_name}")

            if new_title.endswith(" -"):
                new_title = new_title[:-2]
            if new_title.endswith(" - "):
                new_title = new_title[:-3]

            # Validate performers (only if not using title as fallback)
            if (not performers_names or performers_names == "Invalid") and not use_title:
                logger.error(f"{error_prefix} - missing or invalid performers")
                raise ValueError(f"Unable to find valid performers for {file_full_name}")

        except Exception as e:
            logger.error(f"Error in API data for file: {file} - {str(e)}")
            logger.warning(f"End file: {file_full_name}")
            failed_files.append(file_full_name)
            continue  # Skip to the next file

        # Sanitize site name
        formatted_site = await sanitize_site_filename_part(tpdb_site)

        # Determine the suffix based on video type
        if vr2normal:
            suffix = "VR2Normal"
        elif bts_video:
            suffix = "BTS"
        elif upscaled:
            suffix = "Upscaled"
        elif vertical:
            suffix = "Vertical"
        elif pov:
            suffix = "Pov"
        elif trailer:
            suffix = "Trailer"
        else:
            suffix = ""

        # Sanitize and format performers
        formatted_filename_performers_names = await format_performers(performers_names, 2)

        # Sanitize title
        safe_title = await clean_filename(new_title, bad_words, mode=2)

        # Compose potential folder names
        temp_filename_check = f"{formatted_site}.{year}.{month}.{day}.{formatted_filename_performers_names}.{pre_suffix}" if pre_suffix else \
            f"{formatted_site}.{year}.{month}.{day}.{formatted_filename_performers_names}"
        new_filename = f"{formatted_site}.{year}.{month}.{day}.{safe_title}.{pre_suffix}" if pre_suffix else \
            f"{formatted_site}.{year}.{month}.{day}.{safe_title}"

        # Decide whether to use title-based naming
        use_title_mode = use_title or len(temp_filename_check) > 200
        try:
            if use_title_mode:
                if create_sub_folder:
                    # Use title-based folder name
                    new_folder_name = f"{new_filename}.{suffix}" if suffix else new_filename
                    new_folder_full_path = os.path.join(directory, new_folder_name)

                    if not os.path.exists(output_directory):
                        logger.error(f"The folder '{output_directory}' does not exist.")
                    else:
                        shutil.rmtree(output_directory)
                        os.makedirs(new_folder_full_path, exist_ok=True)
                        logger.success(f"Folder successfully renamed to: '{new_folder_full_path}'")
                        output_directory = new_folder_full_path
            else:
                if create_sub_folder:
                    # Use performer-based folder name while adding suffix back
                    if pre_suffix:
                        new_folder_name = f"{temp_filename_check}.{pre_suffix}.{suffix}" if suffix else temp_filename_check
                    else:
                        new_folder_name = f"{temp_filename_check}.{suffix}" if suffix else temp_filename_check
                    title_folder_full_path = os.path.join(directory, new_folder_name)

                    if not os.path.exists(title_folder_full_path):
                        os.rename(output_directory, title_folder_full_path)
                        logger.success(f"Folder successfully renamed to: '{title_folder_full_path}'")
                        output_directory = title_folder_full_path
                new_filename = temp_filename_check

        except Exception as e:
            logger.error(f"Failed to handle folder creation/renaming: {e}")
            logger.warning(f"End file: {file_full_name}")
            failed_files.append(file_full_name)
            continue  # Skip to the next file

        # Format performer names for display/use
        formatted_names = await format_performers(performers_names, mode=1)

        # Format performers names for images matching
        performers_images_names = await format_performers(performers_names, mode=3)

        # Construct new title
        if tpdb_site != site_studio and site_studio:
            studio_info = f"{tpdb_site}({site_studio})"
            studio_tag = [tpdb_site, site_studio]
        else:
            studio_info = tpdb_site
            studio_tag = [tpdb_site]
        new_title_parts = [studio_info, scene_pretty_date, new_title, formatted_names]
        # Remove Performers object if its None to avoid excess " - " in title
        if new_title_parts and (new_title_parts[-1] is None or new_title_parts[-1] == ""):
            create_face_portrait_pic = False
            new_title_parts.pop()
        if suffix:
            new_title_parts.append(suffix)
        new_title = " - ".join(new_title_parts)

        # Rename existing file to new file_full_name if needed
        new_full_filename = f"{new_filename}.{suffix}{file_extension}" if suffix else f"{new_filename}{file_extension}"
        new_file_full_path = os.path.join(directory, new_full_filename)

        if str(file) != str(new_file_full_path):
            rename_result, error_msg = await rename_file(str(file), new_full_filename)
            if not rename_result:
                # logger.error(f"An error has occurred while attempting to rename the file: {error_msg}")
                logger.warning(f"End file: {file_full_name}")
                failed_files.append(file_full_name)
                continue  # Skip to the next file

        try:
            contains_unwanted_metadata = await has_unwanted_metadata(new_file_full_path)
            description = f"TPDB URL: {tpdb_scene_url} | Scene URL: {scene_url}"

            # Always check metadata — but only *apply* it now if not re-encoding
            existing_title = await get_existing_title(new_file_full_path)
            existing_description = await get_existing_description(new_file_full_path)

            metadata_mismatch = (
                    existing_title != new_title or
                    existing_description != description or
                    contains_unwanted_metadata
            )

            if not re_encode_hevc:
                if not metadata_mismatch:
                    # logger.debug(f"File: {file.name} - Metadata is up to date.")
                    pass
                else:
                    # logger.debug(f"File: {file.name} - Metadata differs or unwanted metadata detected.")
                    if contains_unwanted_metadata:
                        remove_metadata_result = await reset_all_metadata(new_file_full_path)
                        if not remove_metadata_result:
                            logger.error(f"Failed to strip unwanted metadata for: {new_full_filename}")
                            failed_files.append(new_file_full_path)
                            continue
                    results_metadata = await update_metadata(new_file_full_path, new_title, description)
                    if not results_metadata:
                        logger.error(f"Failed to update metadata for: {new_full_filename}")
                        failed_files.append(new_file_full_path)
                        continue
                    force_regen_thumbs = True
            else:
                # If we will re-encode, just log if metadata mismatch exists (for debugging)
                if metadata_mismatch:
                    # logger.debug(f"File: {file.name} - Metadata mismatch detected will be reapplied.")
                    results_metadata = await update_metadata(new_file_full_path, new_title, description)
                    if not results_metadata:
                        logger.error(f"Failed to update metadata for: {new_full_filename}")
                        failed_files.append(new_file_full_path)
                        continue
                    force_regen_thumbs = True

            new_filename_base_name, extension = os.path.splitext(new_full_filename)
            fps = await get_video_fps(new_file_full_path)
            resolution, is_vertical = await get_video_resolution_and_orientation(new_file_full_path)
            codec = await get_video_codec(new_file_full_path)

            # Disable uploading to imgbox
            if imgbox_upload_thumbnails or imgbox_upload_cover:
                if image_output_format not in ["png", "jpg"]:
                    imgbox_upload_cover = False
                    imgbox_upload_thumbnails = False
                    logger.warning(f"upload to imgbox failed due to unsupported image output format on their side")
            if not create_cover_image:
                imgbox_upload_cover = False
                imgbb_upload_cover = False
                hamster_upload_cover = False
            if not create_thumbnails:
                imgbox_upload_thumbnails = False
                imgbb_upload_thumbnails = False
                hamster_upload_thumbnails = False

            cover_file_name = f"{new_filename}.{suffix}.{image_output_format}" if suffix else f"{new_filename}.{image_output_format}"
            cover_file_path = os.path.join(output_directory, cover_file_name)
            thumbnails_file_name = f"{new_filename}.{suffix}_thumbnails.{image_output_format}" if suffix else f"{new_filename}_thumbnails.{image_output_format}"
            thumbnails_file_path = os.path.join(output_directory, thumbnails_file_name)

            imgbox_file_path = os.path.join(output_directory, f"{new_filename_base_name}_imgbox.txt") if imgbox_upload_cover or imgbox_upload_thumbnails else ""
            imgbb_file_path = os.path.join(output_directory, f"{new_filename_base_name}_imgbb.txt") if imgbb_upload_cover or imgbb_upload_thumbnails else ""
            hamster_file_path = os.path.join(output_directory, f"{new_filename_base_name}_hamster.txt") if hamster_upload_cover or hamster_upload_thumbnails else ""

            if imgbox_upload_cover or imgbox_upload_thumbnails or imgbb_upload_cover or imgbb_upload_thumbnails or hamster_upload_cover or hamster_upload_thumbnails:
                fill_img_urls = True
                # Check if the imgbox file exists and delete it
                if os.path.exists(imgbox_file_path):
                    os.remove(imgbox_file_path)
                # Check if the imgbb file exists and delete it
                if os.path.exists(imgbb_file_path):
                    os.remove(imgbb_file_path)
                # Check if the hamster file exists and delete it
                if os.path.exists(hamster_file_path):
                    os.remove(hamster_file_path)
            else:
                fill_img_urls = False

            # Define all optional steps and their corresponding conditions and functions
            optional_steps = [
                (re_encode_hevc, re_encode_video, [new_full_filename, directory, keep_original_file, is_vertical, re_encode_downscale, limit_cpu_usage, remove_chapters,
                                                   contains_unwanted_metadata]),

                # runs only if re-encoding is enabled, to re-fetch and update metadata
                (re_encode_hevc, update_metadata, [new_file_full_path, new_title, description]),

                # Create Cover Image
                (create_cover_image, cover_image_download_and_conversion, [image_url, tpdb_image_url, new_full_filename, file_full_name, directory, image_output_format,
                                                                           create_sub_folder, output_directory, cover_regeneration_mode]),
                (imgbox_upload_cover, imgbox_upload_single_image, [cover_file_path, new_filename_base_name, "cover"]),
                (imgbb_upload_cover, imgbb_upload_single_image, [cover_file_path, new_filename_base_name, imgbb_upload_headless_mode, image_output_format, "cover"]),
                (hamster_upload_cover, hamster_upload_single_image, [cover_file_path, new_filename_base_name, "cover"]),

                # Create Thumbnails Image
                (create_thumbnails, process_thumbnails, [new_full_filename, directory, file_full_name, output_directory, image_output_format, is_vertical, create_sub_folder,
                                                         force_regen_thumbs]),

                (imgbox_upload_thumbnails, imgbox_upload_single_image, [thumbnails_file_path, new_filename_base_name, "thumbnails"]),
                (imgbb_upload_thumbnails, imgbb_upload_single_image, [thumbnails_file_path, new_filename_base_name, imgbb_upload_headless_mode, image_output_format, "thumbnails"]),
                (hamster_upload_thumbnails, hamster_upload_single_image, [thumbnails_file_path, new_filename_base_name, "thumbnails"]),

                # Create Preview Images
                (create_video_preview, process_video_preview, [new_file_full_path, output_directory, new_filename_base_name, imgbb_upload_previews, imgbb_upload_headless_mode,
                                                               hamster_upload_previews]),

                # Create Mediainfo File
                (create_mediainfo, generate_mediainfo_file, [new_file_full_path, output_directory]),

                (create_face_portrait_pic, generate_performer_profile_picture,
                 [performers_names, directory, tpdb_performer_url, target_size, zoom_factor, blur_kernel_size, posters_limit, MTCNN, performer_image_output_format, font_full_name]),
                (create_template_file, generate_template_video,
                 [new_title, scene_pretty_date, scene_description, performers_names, fps, resolution, is_vertical, codec, extension, output_directory, new_filename_base_name,
                  template_file_full_path, code_version, scene_tags, studio_tag, image_output_format, fill_img_urls, imgbox_file_path, imgbb_file_path, hamster_file_path, suffix]),
            ]
            failed = False
            where_failed = None
            # Run each enabled optional step
            for flag, func, args in optional_steps:
                if flag:
                    result = await func(*args)
                    if not result:
                        failed = True
                        where_failed = f"{func}"
                        break  # Exit inner loop
                    else:
                        # logger.debug(f"function {func} success")
                        pass
            if failed:
                logger.error(f"Processing failed for file: {new_file_full_path}, error in: {where_failed}")
                logger.warning(f"End file: {new_file_full_path}")
                failed_files.append(new_file_full_path)
                continue  # Skip to the next file
            if create_sub_folder:
                try:
                    # Check if source file exists
                    if os.path.exists(new_file_full_path):
                        # Move the file
                        shutil.move(new_file_full_path, output_directory)
                        logger.success(f"File moved successfully from {new_file_full_path} to {output_directory}")
                        new_file_full_path = os.path.join(output_directory, new_full_filename)
                    else:
                        logger.error(f"Source file {new_file_full_path} does not exist.")
                        raise
                except Exception as e:
                    logger.error(f"Error moving file: {e}")
                    logger.warning(f"End file: {new_file_full_path}")
                    failed_files.append(file_full_name)
                    continue  # Skip to the next file

            if scene_description == "Scene description not found" and create_template_file:
                if send_notification:
                    result = await send_notification("Warning - Scene description not found")
                    if not result:
                        logger.warning("Notifier failed to send user input request.")
                    await asyncio.sleep(0.5)
                logger.warning("Scene description not found, please update manually in template")
            if upload_to_tracker:
                trackers_len = len(tracker_mode)
                for i, tracker_name in enumerate(tracker_mode):
                    try:
                        if not (isinstance(tracker_name, str) and tracker_name.strip()):
                            logger.error(f"Invalid tracker name: {tracker_name}")
                            raise ValueError("Invalid tracker name")

                        is_last_tracker = (i == trackers_len - 1)  # ✅ True if this is the last tracker
                        tracker_result = await process_upload_to_tracker(
                            tracker_name,
                            new_filename_base_name,
                            output_directory,
                            template_file_full_path,
                            new_title,
                            hamster_file_path,
                            directory,
                            remove_e_files,
                            resolution,
                            codec,
                            is_last_tracker
                        )

                        if not tracker_result:
                            raise ValueError("Tracker upload failed")

                    except Exception as e:
                        logger.error(f"Error uploading to tracker {tracker_name}: {e}")
                        logger.warning(f"End file: {new_file_full_path}")
                        failed_files.append(file_full_name)
                        break

            processed_files += 1
            logger.info(f"End file: {new_file_full_path}")
            successful_files.append(new_file_full_path)
        except Exception as e:
            logger.exception(f"Error in Data manipulation for file: {new_file_full_path} - {str(e)}")
            logger.warning(f"End file: {new_file_full_path}")
            failed_files.append(file_full_name)
            continue  # Skip to the next file

    # Finished processing
    logger.info("-" * 100)
    logger.info(f"Finish processing in directory: {directory}")
    for success in successful_files:
        logger.info(f"Successful file: {success}")
    for partial in partial_files:
        logger.info(f"Partial file: {partial}")
    for failed in failed_files:
        logger.warning(f"Failed file: {failed}")


if __name__ == "__main__":
    logger.add("Logs/App_Log_{time:YYYY.MMMM}.log", rotation="30 days", backtrace=True, enqueue=False, catch=True)  # Load Logger
    ffmpeg_ffprobe_results, ff_exit_code = asyncio.run(verify_ffmpeg_and_ffprobe())
    if not ffmpeg_ffprobe_results:
        exit(ff_exit_code)
    asyncio.run(process_files())
