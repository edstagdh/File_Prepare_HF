import json
import hashlib
import os
import random
import re
import shutil
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from time import sleep
from loguru import logger
from Utilities import load_config, run_command


async def create_preview_tool(new_file_full_path, directory, new_filename_base_name):
    # Load Preview Config
    config, exit_code = await load_config("Preview_Config.json")
    if not config:
        exit(exit_code)
    else:
        create_webp_preview = config["CREATE_WEBP_PREVIEW"]
        create_webm_preview = config["CREATE_WEBM_PREVIEW"]
        create_gif_preview = config["CREATE_GIF_PREVIEW"]
        create_webp_preview_sheet = config["CREATE_WEBP_PREVIEW_SHEET"]
        create_webm_preview_sheet = config["CREATE_WEBM_PREVIEW_SHEET"]
        create_gif_preview_sheet = config["CREATE_GIF_PREVIEW_SHEET"]
        keep_temp_files = config["KEEP_TEMP_FILES"]
        add_black_bars = config["ADD_BLACK_BARS"]
        gif_preview_fps = config["GIF_PREVIEW_FPS"]
        number_of_segments_gif = config["NUMBER_OF_SEGMENTS_GIF"]
        timestamps_mode = config["TIMESTAMPS_MODE"]
        grid_width = config["GRID_WIDTH"]
        num_of_segments = config["NUM_OF_SEGMENTS"]
        segment_duration = config["SEGMENT_DURATION"]
        overwrite_existing = config["OVERWRITE_EXISTING"]
        print_cut_points = config["PRINT_CUT_POINTS"]
        blacklisted_cut_points = config["BLACKLISTED_CUT_POINTS"]
        excluded_files = config["EXCLUDED_FILES"]
        custom_output_path = config["CUSTOM_OUTPUT_PATH"]
        confirm_cut_points_required = config["CONFIRM_CUT_POINTS_REQUIRED"]
        last_cut_point = config["LAST_CUT_POINT"]

    if new_file_full_path in excluded_files:
        logger.warning(f"File {new_file_full_path} is in excluded files list and will be ignored - Special Case.")
        return True

    # Verify Segments and Grid values
    is_valid = await validate_preview_sheet_requirements(grid_width, num_of_segments, number_of_segments_gif, create_webp_preview_sheet, create_webm_preview_sheet,
                                                         create_gif_preview_sheet)
    if not is_valid:
        logger.error("Invalid configuration")
        return False

    # Start processing
    # logger.debug(f"processing previews for file: {new_file_full_path}")
    results = await process_video(new_file_full_path, directory, keep_temp_files, add_black_bars, create_webp_preview, create_webp_preview_sheet, segment_duration, num_of_segments,
                                  timestamps_mode, overwrite_existing, grid_width, create_gif_preview, gif_preview_fps, create_gif_preview_sheet, blacklisted_cut_points,
                                  custom_output_path, confirm_cut_points_required, create_webm_preview_sheet, create_webm_preview, print_cut_points, number_of_segments_gif,
                                  new_filename_base_name, last_cut_point)
    if not results:
        logger.error("Preview creation has failed, please check the log.")
        return False
    else:
        return True


async def validate_preview_sheet_requirements(grid_width: int, num_of_segments: int, number_of_segments_gif: int, create_webp_sheet: bool, create_gif_sheet: bool,
                                              create_webm_sheet: bool, ) -> bool:
    try:
        # Validate basic GIF segment constraints
        if number_of_segments_gif > num_of_segments or number_of_segments_gif <= 0:
            logger.error("Number of segments for GIF is invalid.")
            return False

        # Check if sheet creation is enabled
        if not (create_webp_sheet or create_gif_sheet or create_webm_sheet):
            return True  # No sheet creation needed, nothing to validate

        # Validate grid width and divisibility
        if not isinstance(num_of_segments, (int, float)):
            raise TypeError(f"Invalid input: {num_of_segments} is not a number")

        if grid_width == 3:
            if num_of_segments % 3 != 0:
                raise ValueError(f"{num_of_segments} is not divisible by 3")
            if num_of_segments < 9:
                raise ValueError(f"{num_of_segments} is too low for sheet creation.")
            if num_of_segments > 30:
                raise ValueError(f"{num_of_segments} is too high for sheet creation.")

        elif grid_width == 4:
            if num_of_segments % 4 != 0:
                raise ValueError(f"{num_of_segments} is not divisible by 4")
            if num_of_segments < 12:
                raise ValueError(f"{num_of_segments} is too low for sheet creation.")
            if num_of_segments > 28:
                raise ValueError(f"{num_of_segments} is too high for sheet creation.")

        else:
            logger.error(f"Unsupported grid setting: {grid_width}")
            return False

        return True

    except (TypeError, ValueError) as e:
        logger.error(e)
        return False


async def process_video(video_path, directory, keep_temp_files, black_bars, create_webp_preview, create_webp_preview_sheet, segment_duration, num_of_segments, timestamps_mode,
                        ignore_existing, grid, create_gif_preview, gif_preview_fps, create_gif_preview_sheet, blacklisted_cut_points, custom_output_path,
                        confirm_cut_points_required, create_webm_preview_sheet, create_webm_preview, print_cut_points, number_of_segments_gif, new_filename_base_name,
                        last_cut_point):
    if black_bars:
        new_filename_base_name = f"{new_filename_base_name}_black_bars"

    temp_folder = os.path.join(directory, f"{new_filename_base_name}-temp")
    output_directory = custom_output_path if custom_output_path else directory
    output_webp = os.path.join(output_directory, f"{new_filename_base_name}_preview.webp")
    output_webm = os.path.join(output_directory, f"{new_filename_base_name}_preview.webm")
    output_gif = os.path.join(output_directory, f"{new_filename_base_name}_preview.gif")
    preview_sheet_gif = os.path.join(output_directory, f"{new_filename_base_name}_preview_sheet.gif")
    preview_sheet_webp = os.path.join(output_directory, f"{new_filename_base_name}_preview_sheet.webp")
    preview_sheet_webm = os.path.join(output_directory, f"{new_filename_base_name}_preview_sheet.webm")

    # Sample file checks
    file_checks = [
        (output_webp, create_webp_preview),
        (output_webm, create_webm_preview),
        (output_gif, create_gif_preview),
        (preview_sheet_webp, create_webp_preview_sheet),
        (preview_sheet_gif, create_gif_preview_sheet),
        (preview_sheet_webm, create_webm_preview_sheet),
    ]

    # Create a dictionary to track changes
    updated_create_flags = {
        'webp': create_webp_preview,
        'webm': create_webm_preview,
        'gif': create_gif_preview,
        'webp_sheet': create_webp_preview_sheet,
        'gif_sheet': create_gif_preview_sheet,
        'webm_sheet': create_webm_preview_sheet
    }

    sleep(0.5)

    # Prompt for user input and update `should_create`
    for idx, (filepath, should_create) in enumerate(file_checks):
        # Check if the file exists and `should_create` is True
        if os.path.exists(filepath) and should_create:
            result = await ask_delete_file(filepath, ignore_existing)
            if not result:
                # Update the corresponding value in `updated_create_flags`
                if filepath == output_webp:
                    updated_create_flags['webp'] = False
                elif filepath == output_webm:
                    updated_create_flags['webm'] = False
                elif filepath == output_gif:
                    updated_create_flags['gif'] = False
                elif filepath == preview_sheet_webp:
                    updated_create_flags['webp_sheet'] = False
                elif filepath == preview_sheet_gif:
                    updated_create_flags['gif_sheet'] = False
                elif filepath == preview_sheet_webm:
                    updated_create_flags['webm_sheet'] = False
            sleep(0.5)

    # After the loop, you can now use the updated flags in your processing logic
    create_webp_preview = updated_create_flags['webp']
    create_webm_preview = updated_create_flags['webm']
    create_gif_preview = updated_create_flags['gif']
    create_webp_preview_sheet = updated_create_flags['webp_sheet']
    create_gif_preview_sheet = updated_create_flags['gif_sheet']
    create_webm_preview_sheet = updated_create_flags['webm_sheet']

    # logger.debug(f"Preview flags: {updated_create_flags}")

    if any([create_webp_preview, create_gif_preview, create_webp_preview_sheet, create_gif_preview_sheet, create_webm_preview, create_webm_preview_sheet]):
        # Verify video file information
        try:
            # Get video resolution with deeper probing and rotation check
            ffprobe_cmd = (
                f'ffprobe -v 0 -select_streams v:0 '
                f'-show_entries stream_side_data=rotation '
                f'-of default=nw=1:nk=1 \""{video_path}"\"'
            )
            ffprobe_output, stderr, exit_code = await run_command(ffprobe_cmd)
            if exit_code == 0:
                # Check if rotation is detected
                if ffprobe_output:
                    if ffprobe_output.strip() != "0":
                        logger.error(f"Video has rotation metadata: {ffprobe_output.strip()} degrees, this would cause issues generating segments, skipping this file, "
                                     f"please fix rotation before trying to create previews for this file.")
                        return False  # Skip to the next file
                    else:
                        # logger.debug(f"No rotation detected for {video_path}. Proceeding with processing.")
                        pass

            # Proceed with codec and resolution checks
            ffprobe_cmd = (
                f'ffprobe -v error -select_streams v:0 '
                f'-show_entries stream=width,height,codec_name '
                f'-probesize 50M -analyzeduration 50M '  # Increase probing for .TS files
                f'-of csv=s=x:p=0 \""{video_path}"\"'
            )
            ffprobe_output, stderr, exit_code = await run_command(ffprobe_cmd)

            if exit_code == 0 and ffprobe_output.strip():
                try:
                    # Split by 'x' to get codec and resolution
                    parts = ffprobe_output.strip().split("x")

                    # If the split parts have the expected length, continue
                    if len(parts) == 3:
                        codec_name = parts[0]  # First part is codec
                        width, height = map(int, parts[1:3])  # Second and third parts are resolution
                        # Check if the codec is msmpeg4v3
                        if codec_name == "msmpeg4v3":
                            logger.error(f"Video uses unsupported codec: {codec_name}. Requires full re-encoding.")
                            return False
                    else:
                        # If the output is not in the expected format
                        logger.error(f"Unexpected ffprobe output format: {ffprobe_output}")
                        return False
                except (ValueError, IndexError) as e:
                    logger.error(f"Could not determine codec for {video_path}: error: {e}")
                    return False
            else:
                logger.error(f"Could not determine codec for {video_path}")
                return False

        except Exception as e:
            logger.exception()
            return False

        # Remove and recreate the temp folder
        if os.path.exists(temp_folder):
            shutil.rmtree(temp_folder)
        os.makedirs(temp_folder, exist_ok=True)

        # Determine if video is vertical
        is_vertical = width < height
        # logger.debug(f"Processing file: {video_path}, Resolution: {width}x{height}, Vertical: {is_vertical}")

        # Get video duration
        duration_output, stderr, exit_code = await run_command(
            f'ffprobe -v error -select_streams v:0 -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 -i "{video_path}"'
        )
        if not duration_output:
            logger.error(f"Failed to retrieve video duration: {video_path}")
            return False
        try:
            duration = float(duration_output)
        except ValueError:
            logger.error(f"Invalid duration format returned by ffprobe: {duration_output}")
            return False
        required_duration = 150
        if duration <= required_duration:
            logger.error(f"Video duration is too short. Minimum required duration is {required_duration} seconds.")
            return False
        # Determine if any preview sheet is required
        preview_sheet_required = any([
            create_webp_preview_sheet,
            create_gif_preview_sheet,
            create_webm_preview_sheet
        ])
        segment_cut_duration = segment_duration if segment_duration else 1.5
        temp_files_preview = await generate_cut_points(num_of_segments, blacklisted_cut_points, confirm_cut_points_required, duration, segment_cut_duration,
                                                       temp_folder, is_vertical, black_bars, timestamps_mode, preview_sheet_required, video_path, new_filename_base_name,
                                                       print_cut_points, last_cut_point)
        concat_list = os.path.join(temp_folder, "concat_list.txt")
        with open(concat_list, "w") as f:
            for temp_file in temp_files_preview:
                f.write(f"file '{temp_file}'\n")
        concat_list_preview = await filter_and_save_timestamped(concat_list, timestamps_mode, is_sheet=False)
        if num_of_segments != number_of_segments_gif:
            concat_list_preview_gif = await trim_concat_list_file(concat_list_preview, number_of_segments_gif)
        else:
            concat_list_preview_gif = concat_list_preview

        concat_output_file = os.path.join(temp_folder, f"{new_filename_base_name}_concatOutputfile.mp4")
        concat_command = f"ffmpeg -f concat -safe 0 -i \"{concat_list_preview}\" -c copy \"{concat_output_file}\" -y"
        stdout, stderr, exit_code = await run_command(concat_command)
        if exit_code != 0 or not os.path.exists(concat_output_file):
            logger.error(f"Failed to concatenate video segments concat file for WebP: {stderr}")
            return False
        if is_vertical:
            if not black_bars:
                scale_option = "scale=-2:480"
            else:
                scale_option = "scale=480:-2"
        else:
            scale_option = "scale=480:-2"

        # Create Preview files if selected
        # Create Preview WebP
        if create_webp_preview:
            webp_command = (
                f"ffmpeg -y -i \"{concat_output_file}\" "
                f"-vf \"fps=24,{scale_option}:flags=lanczos\" "
                f"-c:v libwebp -quality 80 -compression_level 6 -loop 0 -an -vsync 0 \"{output_webp}\""
            )

            stdout, stderr, exit_code = await run_command(webp_command)

            if exit_code == 0 and os.path.exists(output_webp):
                logger.success(f"Preview WebP created successfully: {output_webp}")
            else:
                logger.error(f"Failed to create WebP: {stderr}")
                return False
        # Create Preview WebM
        if create_webm_preview:
            webm_command = (
                f"ffmpeg -y -i \"{concat_output_file}\" "
                f"-c:v libvpx-vp9 -b:v 3M -vf \"scale=iw:ih:flags=lanczos\" "
                f"-crf 20 -deadline good -cpu-used 4 \"{output_webm}\""
            )
            stdout, stderr, exit_code = await run_command(webm_command)

            if exit_code == 0 and os.path.exists(output_webm):
                logger.success(f"Preview WebM created successfully: {output_webm}")
            else:
                logger.error(f"Failed to create WebM: {stderr}")
                return False
        # Create concat video file for gif if create_gif_preview is true
        if create_gif_preview:
            concat_output_file_gif = os.path.join(temp_folder, f"{new_filename_base_name}_concatOutputfile_gif.mp4")
            concat_command_gif = f"ffmpeg -f concat -safe 0 -i \"{concat_list_preview_gif}\" -c copy \"{concat_output_file_gif}\" -y"
            stdout, stderr, exit_code = await run_command(concat_command_gif)
            if exit_code != 0 or not os.path.exists(concat_output_file_gif):
                logger.error(f"Failed to concatenate video segments concat file for GIF: {stderr}")
                return False
        else:
            concat_output_file_gif = None
        # Create the preview gif if the concat output file has been created and its set to create gif
        if create_gif_preview and concat_output_file_gif:
            gif_command = (
                f"ffmpeg -y -i \"{concat_output_file_gif}\" -vf \"fps={gif_preview_fps},{scale_option}:flags=lanczos,"
                f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse\" -loop 0 \"{output_gif}\""
            )
            stdout, stderr, exit_code = await run_command(gif_command)

            if exit_code == 0 and os.path.exists(output_gif):
                logger.success(f"Preview GIF created successfully: {output_gif}")
            else:
                logger.error(f"Failed to create GIF: {stderr}")
                return False

        # Sheet Creation Segment
        concat_list_sheet = await filter_and_save_timestamped(concat_list, timestamps_mode, is_sheet=True)
        if create_webp_preview_sheet or create_gif_preview_sheet or create_webm_preview_sheet:
            await generate_and_run_ffmpeg_commands(concat_list_sheet, temp_folder, create_webp_preview_sheet, preview_sheet_webp, video_path, segment_cut_duration, grid,
                                                   is_vertical, black_bars, create_gif_preview_sheet, preview_sheet_gif, gif_preview_fps, create_webm_preview_sheet,
                                                   preview_sheet_webm)
        if keep_temp_files:
            # logger.debug("Keeping temp files")
            pass
        else:
            shutil.rmtree(temp_folder, ignore_errors=True)
            # logger.debug("Temporary files removed")
            pass

        logger.success(f"Finished processing file: {video_path}")
        sleep(0.5)
        return True
    else:
        logger.info("Nothing to create in preview tool")
        return True


async def format_time_filename(seconds):
    """Convert seconds to HH.MM.SS format, required for filename, since ":" is not valid in filename"""
    return datetime.utcfromtimestamp(seconds).strftime('%H.%M.%S')


async def format_duration(seconds):
    """Convert seconds into HH:MM:SS format."""
    try:
        seconds = int(float(seconds))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    except Exception as e:
        logger.error(f"Error formatting duration: {e}")
        return "00:00:00"


async def ask_delete_file(file_path, ignore_existing):
    """Asks the user whether to delete a file or skip."""
    try:
        if ignore_existing:
            choice = "yes"
        else:
            choice = input(f"Do you want to delete existing file: '{file_path}'? (yes/no): ").strip().lower()

        if choice in ["yes", "y"]:
            os.remove(file_path)
            # logger.debug(f"Deleted existing file: {file_path}")
            return True
        elif choice in ["no", "n"]:
            # logger.debug(f"Skipped file: {file_path}")
            return False
        else:
            logger.warning("Invalid input. Skipping file by default.")
            return False
    except Exception as e:
        logger.error(f"Error while processing file: {e}")
        return False


async def generate_cut_points(
        num_of_segments,
        blacklisted_cut_points,
        confirm_cut_points_required,
        duration,
        segment_cut_duration,
        temp_folder,
        is_vertical,
        black_bars,
        timestamps_mode,
        preview_sheet_required,
        video_path,
        filename_without_ext,
        print_cut_points,
        last_cut_point
):
    """Generate unique evenly spaced cut points with random variations."""
    calc_failed_counter = 0
    while True:
        if calc_failed_counter % 50 == 0 and calc_failed_counter > 0:
            if segment_cut_duration <= 0.1:
                logger.error("Segment duration too short to reduce")
                break
            else:
                segment_cut_duration = round(segment_cut_duration - 0.1, 2)
                logger.debug(f"cut point generation has failed {calc_failed_counter} times, reducing segment cut duration by 0.1 to accommodate, updated value:"
                             f" {segment_cut_duration}")

        start_point = round(random.uniform(0.02, 0.08), 3)
        if last_cut_point == 0:
            end_point = round(random.uniform(0.975, 0.99), 3)
        else:
            end_point = last_cut_point / duration
        cut_points = {start_point, end_point}
        num_cuts = num_of_segments - 1
        step = (end_point - start_point) / num_cuts
        for i in range(1, num_cuts):
            next_point = round(start_point + step * i + random.uniform(-0.02, 0.02), 3)
            if start_point < next_point < end_point and next_point not in blacklisted_cut_points:
                cut_points.add(next_point)
        if len(cut_points) != num_of_segments:
            logger.error(f'not enough cut points generated: {len(cut_points)}, cut points: {cut_points}')
            continue
        sorted_points = sorted(cut_points)
        if confirm_cut_points_required:
            logger.debug("Generated cut points with timestamp breakdown:")
            for i, pct in enumerate(sorted_points, start=1):
                time_in_seconds = pct * duration
                formatted_time = await format_duration(time_in_seconds)
                logger.debug(f"Segment {i}: {pct:.2%} of video | Time: {formatted_time}")
            sleep(0.5)
            confirmation = input("Do you want to use these cut points? (yes/no): ").strip().lower()
            if confirmation != "yes":
                logger.debug("Regenerating cut points...\n")
                continue
        elif print_cut_points:
            logger.debug("Generated cut points with timestamp breakdown:")
            for i, pct in enumerate(sorted_points, start=1):
                time_in_seconds = pct * duration
                formatted_time = await format_duration(time_in_seconds)
                logger.debug(f"Segment {i}: {pct:.2%} of video | Time: {formatted_time}({time_in_seconds})")

        # Convert percentages to absolute seconds
        cut_points_seconds = [round(duration * pct) for pct in sorted_points]

        # Check for scene changes at each cut point
        scene_change_found = False
        for i, ts in enumerate(cut_points_seconds, start=1):
            scene_at_cut = await check_scene_changes_at_timestamp(video_path, ts, segment_cut_duration)
            if scene_at_cut:
                # logger.debug(f"Scene change detected at cut point {i}: {ts:.2f} seconds. Regenerating cut points...")
                scene_change_found = True
                break

        if scene_change_found:
            calc_failed_counter += 1
            continue

        temp_files_preview = await generate_video_segments(
            video_path,
            filename_without_ext,
            cut_points_seconds,
            segment_cut_duration,
            duration,
            temp_folder,
            is_vertical,
            black_bars,
            timestamps_mode,
            preview_sheet_required
        )

        if not temp_files_preview:
            # Clean up temp folder and retry
            for filename in os.listdir(temp_folder):
                file_path = os.path.join(temp_folder, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            continue

        return temp_files_preview
    if not temp_files_preview:
        logger.error("Failed to generate cut points")


async def generate_video_segments(video_path, filename_without_ext, cut_points, segment_cut_duration, duration, temp_folder, is_vertical, black_bars, timestamps_mode,
                                  preview_sheet_required):
    """Generates video segments from a given video and overlays timestamps on them."""
    temp_files_webp = []

    while len(temp_files_webp) < 15:
        for index, start in enumerate(cut_points, start=1):
            if start >= duration:
                continue

            cut_duration = min(segment_cut_duration, duration - start)
            start_time_formatted = await format_time_filename(start)
            temp_file = os.path.join(
                temp_folder,
                f"{filename_without_ext}_start-{start_time_formatted}_cutpoint-{index}_position-{start:.2f}.mp4"
            )

            # Choose FFmpeg command based on aspect ratio settings
            if is_vertical and black_bars:
                vf_filter = "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2"
            elif is_vertical:
                vf_filter = "scale=270:480"
            else:
                vf_filter = "scale=480:270"

            ffmpeg_segment_command = (
                f"ffmpeg -ss {start} -i \"{video_path}\" -map 0:v:0 -c:v libx264 -crf 23 -preset slow "
                f"-map_metadata -1 -map_chapters -1 -dn -sn -an -t {cut_duration} "
                f"-vf \"{vf_filter}\" \"{temp_file}\" -y"
            )

            stdout, stderr, exit_code = await run_command(ffmpeg_segment_command)

            if exit_code != 0 or not os.path.exists(temp_file):
                logger.error(f"Failed to extract segment {index} at {start} seconds")
                if temp_file in temp_files_webp:
                    temp_files_webp.remove(temp_file)  # Remove failed segment from list
                continue
            if timestamps_mode in [1, 2]:

                temp_files_webp.append(temp_file)
                if preview_sheet_required:
                    return_file = await overlay_timestamp(temp_folder, temp_file)
                    temp_files_webp.append(return_file)
            else:
                temp_files_webp.append(temp_file)

        if not temp_files_webp:
            logger.error("No segments extracted successfully.")
            return []
    return temp_files_webp  # Return list of processed segments


async def check_scene_changes_at_timestamp(video_path, timestamp, segment_cut_duration):
    """
    Check for a scene change around a specific timestamp in a video.
    Looks at a short window to detect abrupt changes, including frames that aren't keyframes.

    :param segment_cut_duration: Duration of the segment to check for scene changes.
    :param video_path: Path to the video file.
    :param timestamp: Time in seconds to check for scene change.
    :return: True if scene change detected, else False.
    """
    scene_threshold = 0.2
    try:
        # Run FFmpeg command to get the frame information
        probe_command = (
            f'ffmpeg -hide_banner -ss {max(timestamp - 0.1, 0)} -t {segment_cut_duration + 0.1} -i "{video_path}" '
            f'-vf "select=\'gt(scene,{scene_threshold})\',showinfo" -an -f null -'
        )

        # logger.debug(f"Running command: {probe_command}")
        stdout, stderr, exit_code = await run_command(probe_command)

        # Check for errors in stderr
        if "Error" in stderr:
            logger.error(f"Error in ffmpeg execution: {stderr}")
            return False

        # Log stdout and stderr for debugging
        # logger.debug(f"stderr: {stderr}")

        # Check for scene change in the output
        scene_change_detected = False
        for line in stderr.splitlines():
            if "pts_time" in line:  # Look for the frame's timestamp
                pts_time = float(line.split('pts_time:')[1].split()[0])  # Extract pts_time
                change_time = timestamp + pts_time
                scene_change_detected = True
                # logger.debug(f"Scene change detected at exact time: {change_time:.2f}s, Regenerating...")
                break

        return scene_change_detected

    except Exception as e:
        logger.exception(f"Failed to check scene at timestamp {timestamp:.2f}s: {e}")
        return False


async def overlay_timestamp(temp_folder, video_path):
    """Extracts timestamp from filename and overlays it on the video."""
    match = re.search(r'start-(\d{2}\.\d{2}\.\d{2})', video_path)
    if not match:
        logger.error(f"Could not extract timestamp from {video_path}")
        return None

    timestamp = match.group(1).replace(".", r"\:")  # Escape colons for FFmpeg
    output_path = f"timestamped_{os.path.basename(video_path)}"
    full_video_path = os.path.join(temp_folder, video_path)
    full_output_path = os.path.join(temp_folder, output_path)

    # Correct font path syntax for FFmpeg
    font_file = r"C\\:/Windows/Fonts/arial.ttf"  # Use the correct syntax for font path

    # FFmpeg command with correct font file path
    ffmpeg_cmd = (
        f"ffmpeg -i \"{full_video_path}\" "
        f"-vf \"drawtext=text='{timestamp}':fontfile={font_file}:fontcolor=white:fontsize=20:"
        f"x=(w-text_w)-10:y=10:box=1:boxcolor=black@0.4:boxborderw=5|2 \" "
        f"-c:v libx264 -crf 23 -preset slow -c:a copy \"{full_output_path}\" -y"
    )

    stdout, stderr, exit_code = await run_command(ffmpeg_cmd)

    if exit_code == 0 and os.path.exists(full_output_path):
        # logger.debug(f"Timestamp overlay added: {full_output_path}")
        return full_output_path  # Return the final path
    else:
        # Log the error message from stderr if FFmpeg fails
        logger.error(f"Failed to overlay timestamp on {video_path}, Exit Code: {exit_code}, Error: {stderr}")
        return None


async def trim_concat_list_file(original_file: str, target_line_count) -> str:
    """
    Copies a concat list file to 'concat_list_edited_gif.txt' in the same directory,
    trims it to a target number of lines by randomly removing lines (excluding first 2 and last 2),
    then sorts all lines by the numeric value in 'cutpoint-<number>'.

    :param original_file: Full path to the original concat list file.
    :param target_line_count: Total number of lines to keep (default is 8).
    :return: Full path to the newly created and trimmed file.
    """
    # Determine new file path
    directory = os.path.dirname(original_file)
    new_file = os.path.join(directory, "concat_list_edited_gif.txt")

    # Read original content
    with open(original_file, "r") as src:
        lines = src.readlines()

    # Trim lines if needed
    if len(lines) > target_line_count:
        first_two = lines[:2]
        last_two = lines[-2:]
        middle = lines[2:-2]

        num_to_keep = target_line_count - len(first_two) - len(last_two)
        if num_to_keep < 0:
            raise ValueError("Target line count too small to preserve first and last 2 lines.")

        trimmed_middle = random.sample(middle, num_to_keep)
        final_lines = first_two + trimmed_middle + last_two
    else:
        logger.info(f"'{new_file}' already has {len(lines)} lines or fewer, no trimming needed.")
        final_lines = lines

    # Sort lines by the number in 'cutpoint-<n>'
    def extract_cut_point_number(line):
        match = re.search(r'cutpoint-(\d+)', line)
        return int(match.group(1)) if match else float('inf')

    final_lines.sort(key=extract_cut_point_number)

    # Write the new sorted file
    with open(new_file, "w") as dst:
        dst.writelines(final_lines)

    return new_file


async def filter_and_save_timestamped(file_path, timestamps_mode, is_sheet):
    # Read the original file
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
    except UnicodeDecodeError:
        # Handle possible decoding errors
        with open(file_path, 'r', encoding='ISO-8859-1') as file:
            lines = file.readlines()

    # Filter the lines based on the timestamps_mode
    filtered_lines = []
    for line in lines:
        line = line.strip()
        if timestamps_mode == 1:
            if "timestamped_" in line:
                filtered_lines.append(line)
        elif timestamps_mode == 2 and is_sheet:
            if "timestamped_" in line:
                filtered_lines.append(line)
        else:
            if "timestamped_" not in line:
                filtered_lines.append(line)

    # Create a new file with the "_timestamped" suffix
    base_name, ext = os.path.splitext(file_path)
    if timestamps_mode in [1, 2]:
        new_file_path = f"{base_name}_edited{ext}"
    elif timestamps_mode == 3:
        return file_path
    else:
        raise "Error, no timestamp mode selected."

    # Write the filtered lines to the new file
    with open(new_file_path, 'w', encoding='utf-8') as new_file:
        for line in filtered_lines:
            new_file.write(f"{line}\n")

    return new_file_path


async def generate_and_run_ffmpeg_commands(concat_file_path, temp_folder, create_webp_preview_sheet, preview_sheet_webp, file_path, segment_duration, grid, is_vertical,
                                           add_black_bars,
                                           create_gif_preview_sheet, preview_sheet_gif, gif_preview_fps, create_webm_preview_sheet, preview_sheet_webm):
    """Generates stacked video sheet, adds preview sheets (WebP, WebM, GIF), and renames files as needed."""
    try:
        # Read the concat_list.txt file
        with open(concat_file_path, 'r') as file:
            lines = file.readlines()

        # Extract video file paths from the concat list
        video_files = [line.strip().split('\'')[1] for line in lines]

        # Group the video files in batches of 3 or 4
        if grid == 3:
            video_groups = [video_files[i:i + 3] for i in range(0, len(video_files), 3)]
        elif grid == 4:
            video_groups = [video_files[i:i + 4] for i in range(0, len(video_files), 4)]
        else:
            logger.error(f"Invalid grid value: {grid}. Only 3 or 4 are allowed.")
            return

        # List to store the output of intermediate stacked videos
        intermediate_files = []

        # Determine char_break_line based on layout
        if grid == 3:
            char_break_line = 75 if is_vertical and not add_black_bars else 110
        elif grid == 4:
            char_break_line = 105 if is_vertical and not add_black_bars else 130
        else:
            grid == 0

        metadata_table, filename, original_fps = await get_video_metadata(file_path, char_break_line)
        info_image_path = await create_info_image(metadata_table, temp_folder, filename, grid, is_vertical, add_black_bars)

        # Create the video from the info image (same resolution as image)
        final_image_video_path = os.path.join(temp_folder, filename + '_image_video.mp4')
        await create_video_from_image(info_image_path, final_image_video_path, fps=original_fps, duration=segment_duration)

        # Process each group of videos and stack them horizontally
        for index, group in enumerate(video_groups):
            input_files = ' '.join([f'-i "{file}"' for file in group])
            output_file = os.path.join(temp_folder, f"stacked_{index + 1}.mp4")
            intermediate_files.append(output_file)

            if grid == 3:
                filter_complex = "[0:v][1:v][2:v]hstack=inputs=3[v]"
            elif grid == 4:
                filter_complex = "[0:v][1:v][2:v][3:v]hstack=inputs=4[v]"

            command = f"ffmpeg {input_files} -filter_complex \"{filter_complex}\" -map \"[v]\" -y \"{output_file}\""
            stdout, stderr, exit_code = await run_command(command)
            if exit_code != 0:
                logger.error(f"Error running ffmpeg command for stacked video {index + 1}: {stdout}\n{stderr}\nCommand: {command}")
                continue

        # Stack all intermediate videos vertically with info image at top
        final_output = os.path.join(temp_folder, "final_thumbnail_sheet.mp4")
        downscaled_output = os.path.join(temp_folder, "downscaled_thumbnail_sheet.mp4")
        image_scaled = f"-i \"{final_image_video_path}\""
        input_files = f"{image_scaled} " + ' '.join([f"-i \"{file}\"" for file in intermediate_files])

        filter_complex = f"[0:v]" + ''.join([f"[{i + 1}:v]" for i in range(len(intermediate_files))]) + f"vstack=inputs={len(intermediate_files) + 1}[v]"
        final_command = f"ffmpeg {input_files} -filter_complex \"{filter_complex}\" -map \"[v]\" -r {original_fps} -y \"{final_output}\""
        stdout, stderr, exit_code = await run_command(final_command)
        if exit_code != 0:
            logger.error(f"Error running final stacking command: {stdout}\n{stderr}")
            return

        # Add scale if grid is 4
        if grid == 4:
            if not is_vertical or (is_vertical and add_black_bars):
                downscale_filter = "scale=1890:trunc(ih/2)*2"
                downscale_command = f'ffmpeg -i "{final_output}" -filter_complex "{downscale_filter}" -y "{downscaled_output}"'
                # logger.debug(downscale_command)
                stdout, stderr, exit_code = await run_command(downscale_command)
                if exit_code != 0:
                    logger.error(f"Error running downscaling stacking command: {stdout}\n{stderr}")
                    return
                # Renaming the final output with the "_og" suffix
                base, ext = os.path.splitext(final_output)
                final_output_og = f"{base}_og{ext}"
                if not os.path.exists(final_output_og):
                    os.rename(final_output, final_output_og)
                else:
                    logger.warning(f"The file {final_output_og} already exists, skipping renaming.")

                # Replace the final output with the downscaled version
                os.rename(downscaled_output, final_output)

        # Generate previews (WebP, WebM, GIF)
        results = ""
        # WebP Preview
        if create_webp_preview_sheet:
            webp_command = (
                f"ffmpeg -y -i \"{final_output}\" -vf \"scale=iw:ih:flags=lanczos\" "
                f"-c:v libwebp -quality 80 -lossless 0 -loop 0 -an -vsync 0 \"{preview_sheet_webp}\""
            )
            stdout, stderr, exit_code = await run_command(webp_command)
            if exit_code != 0:
                logger.error(f"Error creating WebP preview: {stdout}\n{stderr}")
            else:
                results += f"WebP preview saved: {preview_sheet_webp}\n"

        # WebM Preview
        if create_webm_preview_sheet:
            webm_command = (
                f"ffmpeg -y -i \"{final_output}\" -c:v libvpx-vp9 -b:v 3M -vf \"scale=iw:ih:flags=lanczos\" "
                f"-crf 20 -deadline good -cpu-used 4 \"{preview_sheet_webm}\""
            )
            stdout, stderr, exit_code = await run_command(webm_command)
            if exit_code != 0:
                logger.error(f"Error creating WebM preview: {stdout}\n{stderr}")
            else:
                results += f"WebM preview saved: {preview_sheet_webm}\n"

        # GIF Preview
        if create_gif_preview_sheet:
            gif_command = (
                f"ffmpeg -y -i \"{final_output}\" -vf \"scale=iw:ih:flags=lanczos,fps={gif_preview_fps}\" "
                f"\"{preview_sheet_gif}\""
            )
            stdout, stderr, exit_code = await run_command(gif_command)
            if exit_code != 0:
                logger.error(f"Error creating GIF preview: {stdout}\n{stderr}")
            else:
                results += f"GIF preview saved: {preview_sheet_gif}\n"

        # logger.info("Thumbnail sheet generation completed successfully.")
        # logger.info(results)

    except Exception as e:
        logger.exception(f"Exception occurred in generate_and_run_ffmpeg_commands: {str(e)}")


async def get_video_metadata(file_path, char_break_line):
    """Extract video metadata using ffprobe."""
    filename = os.path.basename(file_path)
    base_filename = os.path.splitext(filename)[0]
    file_dir = os.path.dirname(file_path)

    try:

        # Get video metadata
        cmd = f'ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,width,height,r_frame_rate,bit_rate -of json \"{file_path}\"'
        video_info_json, stderr, exit_code = await run_command(cmd)

        cmd = f'ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,bit_rate,profile -of json \"{file_path}\"'
        audio_info_json, stderr, exit_code = await run_command(cmd)

        cmd = f'ffprobe -v error -show_entries format=duration,size:format_tags=title -of json \"{file_path}\"'
        format_info_json, stderr, exit_code = await run_command(cmd)

        # Parse JSON outputs
        video_info = json.loads(video_info_json).get("streams", [{}])[0]
        audio_info = json.loads(audio_info_json).get("streams", [{}])[0]
        format_info = json.loads(format_info_json).get("format", {})
        fps = video_info.get("r_frame_rate", "N/A")
        try:
            num, denom = map(int, fps.split("/"))
            fps = round(num / denom, 2)
        except Exception as e:
            fps = "N/A"

    except Exception as e:
        logger.error(f"Error extracting metadata: {e}")
        return [], file_dir, None

    # Extract and format video information
    video_codec = video_info.get('codec_name', 'N/A').upper()
    video_profile = video_info.get('profile', 'N/A')
    video_bitrate = round(int(video_info.get('bit_rate', 0)) / 1000) if 'bit_rate' in video_info else 0
    video_details = f"{video_codec} ({video_profile}) @ {video_bitrate} kbps, {fps} fps"

    # Extract and format audio information
    audio_codec = audio_info.get('codec_name', 'N/A').upper()
    audio_channels = audio_info.get('channels', 'N/A')
    audio_bitrate = round(int(audio_info.get('bit_rate', 0)) / 1000) if 'bit_rate' in audio_info else 0

    if audio_codec == "AAC" and "LC" in audio_info.get('profile', '').upper():
        audio_codec += " (LC)"

    audio_details = f"{audio_codec} ({audio_channels}ch) @ {audio_bitrate} kbps"
    add_lines = 0
    # Extract other metadata
    title = format_info.get("tags", {}).get("title", "N/A")
    if len(title) > char_break_line:
        title = await break_string_at_char(title, " ", char_break_line)
        add_lines += 1
    if len(filename) > char_break_line:
        filename = await break_string_at_char(filename, ".", char_break_line) or \
                   await break_string_at_char(filename, " ", char_break_line) or \
                   await break_string_at_char(filename, "-", char_break_line)
        add_lines += 1
    duration = await format_duration(format_info.get('duration', '0'))
    file_size = f"{int(format_info.get('size', '0')) / (1024 * 1024):.2f} MB"

    # Extract resolution and FPS
    width = video_info.get("width", "N/A")
    height = video_info.get("height", "N/A")
    resolution = f"{width}x{height}"

    # Calculate MD5 checksum
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        md5_hash = hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"Error computing MD5 hash: {e}")
        md5_hash = "N/A"

    info_table = [
        ["File Name", filename],
        ["Title", title],
        ["File Size", file_size],
        ["Resolution", resolution],
        ["Duration", duration],
        ["Video", video_details],
        ["Audio", audio_details],
        ["MD5", md5_hash]
    ]
    if add_lines != 0:
        for i in range(add_lines):
            info_table.append([" ", " "])  # Append an empty row with two empty strings to avoid overwriting last values in image
    return info_table, base_filename, fps


async def break_string_at_char(s, break_char, char_break_line):
    if len(s) > char_break_line:
        # Find the position of the last space within the first 127 characters
        break_point = s.rfind(break_char, 0, char_break_line)

        # If there's a break char, replace it with a newline, otherwise, just return the string as is
        if break_point != -1:
            s = s[:break_point] + '\n' + s[break_point + 1:]
    return s


async def create_info_image(metadata_table, temp_folder, filename, grid, is_vertical, add_black_bars):
    """Create an image displaying video metadata."""

    font_size = 18
    if grid == 3:
        if is_vertical and not add_black_bars:
            width = 810
            font_size = 16
        else:
            width = 1440
    elif grid == 4:
        if is_vertical and not add_black_bars:
            width = 1080
            font_size = 16
        else:
            width = 1920
    else:
        logger.error("Unsupported Grid size")
        return

    line_height = 30
    height = len(metadata_table) * line_height + 20

    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        logger.warning("Arial font not found, using default font.")
        font = ImageFont.load_default()

    y_offset = 10
    for row in metadata_table:
        key, value = row

        # Check if the value is a coroutine and await it
        if callable(value):  # If value is a coroutine (function), await it
            value = await value  # Correctly await the coroutine

        # Split value by newline for multiline content
        value_lines = value.split('\n')

        # Print the key and the first line of the value
        if len(key) > 1:
            draw.text((20, y_offset), key + " :", font=font, fill=(255, 255, 255))
        else:
            draw.text((20, y_offset), key + "  ", font=font, fill=(255, 255, 255))
        draw.text((150, y_offset), value_lines[0], font=font, fill=(255, 255, 255))
        y_offset += line_height

        # Print the subsequent lines (continuation of the value without the key)
        for line in value_lines[1:]:
            draw.text((150, y_offset), line, font=font, fill=(255, 255, 255))
            y_offset += line_height

    output_image_name = filename + "_info.png"
    output_image_path = os.path.join(temp_folder, output_image_name)
    try:
        img.save(output_image_path)
        # logger.debug(f"Image saved as {output_image_path}")
    except Exception as e:
        logger.error(f"Error saving image: {e}")

    return output_image_path


async def create_video_from_image(image_path, output_path, fps, duration=1):
    """
    Create a video from an image at the given FPS and duration

    :param image_path: Path to the input image.
    :param output_path: Path to the output video file.
    :param fps: Frames per second for the output video.
    :param duration: Duration in seconds for the video.
    """
    try:
        # Get the resolution of the image (width x height)
        command = [
            "ffmpeg",
            "-i", image_path,
            "-vframes", "1",
            "-f", "image2pipe",
            "-c:v", "png",
            "-"
        ]

        # Get image resolution to ensure the video has the same dimensions
        stdout, stderr, exit_code = await run_command(command)
        if exit_code != 0:
            logger.error(f"Error getting image resolution: {stderr}")
            return

        # Use FFmpeg to create the video from the image
        ffmpeg_command = [
            "ffmpeg",
            "-loop", "1",  # Loop the image
            "-framerate", str(fps),  # Set the frame rate
            "-t", str(duration),  # Set the duration of the video
            "-i", image_path,  # Input image
            "-c:v", "libx264",  # Use x264 codec
            "-pix_fmt", "yuv420p",  # Set pixel format
            "-y",  # Overwrite output file without asking
            output_path  # Output video path
        ]
        # Run the FFmpeg command to create the video
        stdout, stderr, exit_code = await run_command(ffmpeg_command)
        if exit_code == 0:
            # logger.debug(f"Video created successfully: \"{output_path}\"")
            pass
        else:
            logger.error(f"Error creating video: {stderr}")

    except Exception as e:
        logger.error(f"Exception occurred: {e}")
