import asyncio
import hashlib
import os
import random
import re
import shutil
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from loguru import logger
from fileprep.runtime import prompt
from pymediainfo import MediaInfo
from fileprep.media.probe import get_video_duration
from fileprep import paths, runtime
from fileprep.system import gpu
from fileprep.config.models import TRANSITIONS, VideoPreviewSettings
from fileprep.uploaders.service import upload_preview_asset
from fileprep.utils.process import run_command


async def process_video_preview(input_video_file_path, output_directory, new_filename_base_name, upload_previews: bool,
                                settings: VideoPreviewSettings | None = None):
    """
    Create the animated previews / preview sheets of one video.

    input_video_file_path   - full path of the video
    output_directory        - folder receiving the previews (unless ``custom_output_directory`` is set)
    new_filename_base_name  - base name of the generated files and of the image host result keys
    upload_previews         - upload the WebP/GIF previews and sheets to the job's preview hosts
    settings                - video preview settings (defaults to the running job's settings)
    """
    settings = settings or runtime.config().video_preview
    create_webp_preview = settings.create_webp_preview
    create_webm_preview = settings.create_webm_preview
    create_gif_preview = settings.create_gif_preview
    create_webp_preview_sheet = settings.create_webp_sheet
    create_webm_preview_sheet = settings.create_webm_sheet
    create_gif_preview_sheet = settings.create_gif_sheet
    keep_temp_files = settings.keep_temp_files
    add_black_bars = settings.add_black_bars
    gif_preview_fps = settings.gif_fps
    webp_preview_fps = settings.webp_fps
    avif = {"preview": settings.create_avif_preview, "sheet": settings.create_avif_sheet, "fps": settings.avif_fps,
            "crf": settings.avif_crf,
            "encoder": await avif_encoder() if settings.create_avif_preview or settings.create_avif_sheet else None}
    number_of_segments_gif = settings.gif_segment_count
    timestamps_mode = settings.timestamps_mode
    grid_width = settings.grid_columns
    num_of_segments = settings.segment_count
    segment_duration = settings.segment_duration
    overwrite_existing = settings.overwrite_existing
    print_cut_points = settings.log_cut_points
    blacklisted_cut_points = settings.blacklisted_cut_points
    excluded_files = settings.excluded_files
    custom_output_path = settings.custom_output_directory
    confirm_cut_points_required = settings.confirm_cut_points
    last_cut_point = settings.last_cut_point
    add_file_info = settings.include_file_info
    transition_mode = settings.transition.lower()
    available_transitions = TRANSITIONS
    transition_duration = settings.transition_duration
    fit_thumbs_in_less_rows = settings.compact_vertical_layout
    preview_quality_resolution = settings.resolution
    scene_threshold = settings.scene_change_threshold

    input_video_file_name = os.path.basename(input_video_file_path)
    input_video_full_path = input_video_file_path
    if input_video_file_name in excluded_files:
        logger.warning(f'File {input_video_full_path} is in excluded files list and will be ignored - Special Case.')
        return True

    font_path = paths.ffmpeg_filter_path(paths.resource(settings.font_file))  # used inside drawtext filters

    # Verify Segments and Grid values
    is_valid = await validate_preview_sheet_requirements(grid_width, num_of_segments, number_of_segments_gif, create_webp_preview_sheet, create_webm_preview_sheet,
                                                         create_gif_preview_sheet)
    if not is_valid:
        logger.error("Invalid configuration")
        return False

    # Start processing. The temporary segment folder is removed afterwards whatever happens (failure,
    # exception or cancellation) unless keep_temp_files is on.
    temp_folder = os.path.join(output_directory, f"{new_filename_base_name}{'_black_bars' if add_black_bars else ''}-temp")
    try:
        results = await process_video(input_video_full_path, output_directory, keep_temp_files, add_black_bars, create_webp_preview, create_webp_preview_sheet, segment_duration,
                                      num_of_segments,
                                      timestamps_mode, overwrite_existing, grid_width, create_gif_preview, gif_preview_fps, webp_preview_fps, create_gif_preview_sheet,
                                      blacklisted_cut_points,
                                      custom_output_path, confirm_cut_points_required, create_webm_preview_sheet, create_webm_preview, print_cut_points, number_of_segments_gif,
                                      new_filename_base_name, last_cut_point, font_path, upload_previews, add_file_info,
                                      transition_mode, available_transitions, transition_duration, fit_thumbs_in_less_rows, preview_quality_resolution, scene_threshold, avif=avif,
                                      webp_level=settings.webp_compression_level)
    finally:
        if not keep_temp_files and os.path.isdir(temp_folder):
            shutil.rmtree(temp_folder, ignore_errors=True)
            if os.path.isdir(temp_folder):
                logger.warning(f"Could not remove the temporary folder (a file may still be open): {temp_folder}")

    if not results:
        logger.error("Preview creation has failed, please check the log.")
        return False
    else:
        return True


async def avif_encoder() -> str:
    """AV1 encoder used for animated AVIF previews: SVT-AV1 (fast) when available, else libaom."""
    from fileprep.system.ffmpeg import check_ffmpeg
    encoders = (await check_ffmpeg()).encoders
    return "libaom-av1" if not encoders.get("libsvtav1") and encoders.get("libaom-av1") else "libsvtav1"


def avif_codec_args(avif: dict) -> str:
    crf = avif.get("crf", 35)
    if avif.get("encoder") == "libaom-av1":
        return f"-c:v libaom-av1 -cpu-used 6 -row-mt 1 -crf {crf} -b:v 0"
    return f"-c:v libsvtav1 -preset 8 -crf {crf}"


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
            raise TypeError(f'Invalid input: {num_of_segments} is not a number')

        if grid_width == 3:
            if num_of_segments % 3 != 0:
                raise ValueError(f'{num_of_segments} is not divisible by 3')
            if num_of_segments < 9:
                raise ValueError(f'{num_of_segments} is too low for sheet creation.')
            if num_of_segments > 30:
                raise ValueError(f'{num_of_segments} is too high for sheet creation.')

        elif grid_width == 4:
            if num_of_segments % 4 != 0:
                raise ValueError(f'{num_of_segments} is not divisible by 4')
            if num_of_segments < 12:
                raise ValueError(f'{num_of_segments} is too low for sheet creation.')
            if num_of_segments > 28:
                raise ValueError(f'{num_of_segments} is too high for sheet creation.')

        else:
            logger.error(f'Unsupported grid setting: {grid_width}')
            return False

        return True

    except (TypeError, ValueError) as e:
        logger.error(e)
        return False


async def concat_video_segments(concat_list_file, output_file, transition_mode, available_transitions, duration):
    """
    Concatenate video files with optional transitions (video only).

    concat_list_file: path to a text file containing lines like:
        file 'path/to/video1.mp4'
        file 'path/to/video2.mp4'
    output_file: path to save the final video
    transition_mode: 'none', 'random', or transition name
    available_transitions: list of transition names for 'random' mode
    duration: duration of each transition in seconds
    """

    if not os.path.exists(concat_list_file):
        logger.error(f'Concat list file does not exist: {concat_list_file}')
        return False, None

    # Determine temp file path (same folder as concat_list_file)
    temp_folder = os.path.dirname(concat_list_file)

    # Read the file paths from the concat list
    with open(concat_list_file, "r", encoding="utf-8") as f:
        segment_files = [
            line.strip()[6:-1]  # strip "file '" at start and "'" at end
            for line in f.readlines() if line.strip()
        ]

    if not segment_files:
        logger.error(f'No videos found in concat list: {concat_list_file}')
        return False, None

    # Only one video, just copy
    if len(segment_files) == 1:
        cmd = f'ffmpeg -i "{segment_files[0]}" -c copy "{output_file}" -y'
        stdout, stderr, code = await run_command(cmd)
        return (code == 0 and os.path.exists(output_file)), output_file if code == 0 else None

    # Mode: none → simple concat
    if transition_mode in ["none", "", None]:
        cmd = f'ffmpeg -hide_banner -f concat -safe 0 -i "{concat_list_file}" -c copy "{output_file}" -y'
        stdout, stderr, code = await run_command(cmd)
        if code != 0 or not os.path.exists(output_file):
            logger.error(f'Simple concat failed: {stderr}')
            return False, None
        return True, output_file

    # Mode: random or fixed transition
    elif transition_mode == "random":
        num_transitions = len(segment_files) - 1
        if len(available_transitions) < num_transitions:
            transitions = random.choices(available_transitions, k=num_transitions)
        else:
            transitions = random.sample(available_transitions, num_transitions)

        # Randomize the order of the transitions
        random.shuffle(transitions)
    else:
        transitions = [transition_mode] * (len(segment_files) - 1)

    # One ffmpeg run for the whole chain: every segment is decoded and the result encoded once. (Before 14.17 each
    # transition re-encoded everything joined so far - 15 growing encodes for 16 segments, plus 2 for the fades.)
    durations = await asyncio.gather(*(_media_duration(f) for f in segment_files))
    if not all(durations):
        logger.error("Could not read the duration of every preview segment")
        return False, None
    duration = min(duration, min(durations) / 2)  # a transition can never be longer than half a segment
    width, height, rate = await _video_format(segment_files[0])
    inputs = " ".join(f'-i "{f}"' for f in segment_files)
    # xfade needs identical size, frame rate, time base and pixel format on both inputs: segments made by a GPU
    # encoder can differ in these, so every input is normalised to the first segment. fps must come after setpts:
    # since ffmpeg 7 setpts marks the frame rate as unknown (1/0), which xfade rejects.
    chains = [f"[{i}:v]scale={width}:{height},setsar=1,settb=AVTB,setpts=PTS-STARTPTS,fps={rate},format=yuv420p[s{i}]"
              for i in range(len(segment_files))]
    fades = transition_mode in ["fade", "fadeblack"]
    previous, offset = "s0", 0.0
    if fades:
        chains.append(f"[s0]fade=t=in:st=0:d={duration:.3f}[f0]")
        previous = "f0"
    for i, transition in enumerate(transitions, start=1):
        offset += durations[i - 1] - duration
        chains.append(f"[{previous}][s{i}]xfade=transition={transition}:duration={duration:.3f}:offset={offset:.3f}[x{i}]")
        previous = f"x{i}"
    total = offset + durations[-1]
    if fades:
        chains.append(f"[{previous}]fade=t=out:st={max(total - duration, 0):.3f}:d={duration:.3f}[vout]")
        previous = "vout"
    cmd = (f'ffmpeg -hide_banner {inputs} -filter_complex "{";".join(chains)}" -map "[{previous}]" '
           f'{INTERMEDIATE_ENCODE} -an "{output_file}" -y')
    stdout, stderr, code = await run_command(cmd)
    if code == 0 and os.path.exists(output_file):
        return True, output_file
    logger.warning(f"Transitions failed ({ffmpeg_error(stderr)}) - joining the segments without transitions")
    cmd = (f'ffmpeg -hide_banner {inputs} -filter_complex "{";".join(chains[:len(segment_files)])};'
           f'{"".join(f"[s{i}]" for i in range(len(segment_files)))}concat=n={len(segment_files)}:v=1:a=0[v]" '
           f'-map "[v]" {INTERMEDIATE_ENCODE} -an "{output_file}" -y')
    stdout, stderr, code = await run_command(cmd)
    if code != 0 or not os.path.exists(output_file):
        logger.error(f"Joining the preview segments failed: {ffmpeg_error(stderr)}")
        return False, None
    return True, output_file


def ffmpeg_error(stderr: str) -> str:
    """The lines of ffmpeg's output that explain a failure (not the progress / summary lines at the end)."""
    keys = ("error", "invalid", "not match", "do not match", "failed", "no such", "cannot", "unable", "mismatch")
    lines = [l.strip() for l in (stderr or "").splitlines() if any(k in l.lower() for k in keys)]
    return " | ".join(dict.fromkeys(lines))[:800] or (stderr or "").strip()[-300:]


async def _video_format(path: str) -> tuple[int, int, str]:
    """Width, height and frame rate (as ffmpeg writes it, e.g. 30000/1001) of the first video stream."""
    stdout, _, code = await run_command(f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate '
                                        f'-of default=nw=1 "{path}"')
    values = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line) if code == 0 else {}
    rate = values.get("r_frame_rate", "")
    if not rate or rate.startswith("0"):
        rate = "30"
    return int(values.get("width") or 1280), int(values.get("height") or 720), rate


# Intermediate clips (segments with time stamps, joined preview, sheet) only feed the WebP/AVIF/GIF encoders:
# fastest x264 settings at near-lossless quality.
INTERMEDIATE_ENCODE = "-c:v libx264 -preset ultrafast -crf 16 -pix_fmt yuv420p"
SEGMENT_JOBS = 3  # segments cut at the same time (GPU sessions / disk seeks)
OVERLAY_JOBS = max(2, (os.cpu_count() or 2) // 2)


async def _media_duration(path: str) -> float:
    stdout, _, code = await run_command(f'ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "{path}"')
    try:
        return float(stdout.strip()) if code == 0 else 0.0
    except ValueError:
        return 0.0


async def process_video(video_path, directory, keep_temp_files, black_bars, create_webp_preview, create_webp_preview_sheet, segment_duration, num_of_segments, timestamps_mode,
                        ignore_existing, grid, create_gif_preview, gif_preview_fps, webp_preview_fps, create_gif_preview_sheet, blacklisted_cut_points, custom_output_path,
                        confirm_cut_points_required, create_webm_preview_sheet, create_webm_preview, print_cut_points, number_of_segments_gif, new_filename_base_name,
                        last_cut_point, font_path, upload_previews, add_file_info, transition_mode,
                        available_transitions, transition_duration, fit_thumbs_in_less_rows, preview_quality_resolution, scene_threshold, avif=None, webp_level=4):
    avif = avif or {"preview": False, "sheet": False}
    if black_bars:
        new_filename_base_name = f'{new_filename_base_name}_black_bars'

    temp_folder = os.path.join(directory, f'{new_filename_base_name}-temp')
    output_directory = custom_output_path if custom_output_path else directory
    output_webp = os.path.join(output_directory, f'{new_filename_base_name}_preview.webp')
    output_webm = os.path.join(output_directory, f'{new_filename_base_name}_preview.webm')
    output_gif = os.path.join(output_directory, f'{new_filename_base_name}_preview.gif')
    preview_sheet_gif = os.path.join(output_directory, f'{new_filename_base_name}_preview_sheet.gif')
    preview_sheet_webp = os.path.join(output_directory, f'{new_filename_base_name}_preview_sheet.webp')
    preview_sheet_webm = os.path.join(output_directory, f'{new_filename_base_name}_preview_sheet.webm')
    output_avif = os.path.join(output_directory, f'{new_filename_base_name}_preview.avif')
    preview_sheet_avif = os.path.join(output_directory, f'{new_filename_base_name}_preview_sheet.avif')

    # Sample file checks
    file_checks = [
        (output_webp, create_webp_preview),
        (output_webm, create_webm_preview),
        (output_gif, create_gif_preview),
        (preview_sheet_webp, create_webp_preview_sheet),
        (preview_sheet_gif, create_gif_preview_sheet),
        (preview_sheet_webm, create_webm_preview_sheet),
        (output_avif, avif["preview"]),
        (preview_sheet_avif, avif["sheet"]),
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

    await asyncio.sleep(0.5)

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
                elif filepath == output_avif:
                    avif = {**avif, "preview": False}
                elif filepath == preview_sheet_avif:
                    avif = {**avif, "sheet": False}
            await asyncio.sleep(0.5)

    # After the loop, you can now use the updated flags in your processing logic
    create_webp_preview = updated_create_flags['webp']
    create_webm_preview = updated_create_flags['webm']
    create_gif_preview = updated_create_flags['gif']
    create_webp_preview_sheet = updated_create_flags['webp_sheet']
    create_gif_preview_sheet = updated_create_flags['gif_sheet']
    create_webm_preview_sheet = updated_create_flags['webm_sheet']

    # logger.debug(f'Preview flags: {updated_create_flags}")
    skip_video = False
    codec_name = None
    width = None
    height = None

    if any([create_webp_preview, create_gif_preview, create_webp_preview_sheet, create_gif_preview_sheet, create_webm_preview, create_webm_preview_sheet,
            avif["preview"], avif["sheet"]]):
        # Verify video file information
        try:
            # Get video resolution with deeper probing and rotation check
            ffprobe_cmd = (
                f'ffprobe -v 0 -select_streams v:0 '
                f'-show_entries stream_side_data=rotation '
                f'-of default=nw=1:nk=1 "{video_path}"'
            )
            ffprobe_output, stderr, exit_code = await run_command(ffprobe_cmd)
            if exit_code == 0:
                # Check if rotation is detected
                if ffprobe_output:
                    if ffprobe_output.strip() != "0":
                        logger.error(f'Video has rotation metadata: {ffprobe_output.strip()} degrees, this would cause issues generating segments, skipping this file, '
                                     f'please fix rotation before trying to create previews for this file.')
                        return False  # Skip to the next file
                    else:
                        # logger.debug(f'No rotation detected for {video_path}. Proceeding with processing.")
                        pass

            # Proceed with codec and resolution checks
            ffprobe_cmd = (
                f'ffprobe -v error -select_streams v:0 '
                f'-show_entries stream=width,height,codec_name '
                f'-probesize 50M -analyzeduration 50M '  # Increase probing for .TS files
                f'-of csv=s=x:p=0 "{video_path}"'
            )
            ffprobe_output, stderr, exit_code = await run_command(ffprobe_cmd)

            if exit_code == 0 and ffprobe_output.strip():
                try:
                    parts = ffprobe_output.strip().split("x")
                    if len(parts) == 3:
                        codec_name = parts[0]
                        width, height = map(int, parts[1:3])
                        if codec_name == "msmpeg4v3":
                            logger.error(f'Video uses unsupported codec: {codec_name}. Requires full re-encoding.')
                            skip_video = True
                    else:
                        logger.error(f'Unexpected ffprobe output format: {ffprobe_output}')
                        skip_video = True
                except (ValueError, IndexError) as e:
                    logger.error(f'Could not determine codec for {video_path}: error: {e}')
                    skip_video = True

                if skip_video:
                    return False
            else:
                logger.error(f'Could not determine codec for {video_path}\nFFProbe Output: {ffprobe_output}\nError: {stderr}\nExit Code: {exit_code}')
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
        # logger.debug(f'Processing file: {video_path}, Resolution: {width}x{height}, Vertical: {is_vertical}")

        # Get video duration
        duration_output, stderr, exit_code = await run_command(
            f'ffprobe -v error -select_streams v:0 -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 -i "{video_path}"'
        )
        if not duration_output:
            logger.error(f'Failed to retrieve video duration: {video_path}')
            return False
        try:
            duration = float(duration_output)
        except ValueError:
            logger.error(f'Invalid duration format returned by ffprobe: {duration_output}')
            return False
        required_duration = 150
        if duration <= required_duration:
            logger.error(f'Video duration is too short. Minimum required duration is {required_duration} seconds.')
            return False
        # Determine if any preview sheet is required
        preview_sheet_required = any([
            create_webp_preview_sheet,
            avif["sheet"],
            create_gif_preview_sheet,
            create_webm_preview_sheet
        ])
        segment_cut_duration = segment_duration if segment_duration else 1.5
        temp_files_preview = await generate_cut_points(num_of_segments, blacklisted_cut_points, confirm_cut_points_required, duration, segment_cut_duration,
                                                       temp_folder, is_vertical, black_bars, timestamps_mode, preview_sheet_required, video_path, new_filename_base_name,
                                                       print_cut_points, last_cut_point, font_path, width, height, preview_quality_resolution, scene_threshold)
        concat_list = os.path.join(temp_folder, "concat_list.txt")
        with open(concat_list, "w") as f:
            for temp_file in temp_files_preview:
                f.write(f"file '{temp_file}'\n")
        concat_list_preview = await filter_and_save_timestamped(concat_list, timestamps_mode, is_sheet=False)
        if num_of_segments != number_of_segments_gif:
            concat_list_preview_gif = await trim_concat_list_file(concat_list_preview, number_of_segments_gif)
        else:
            concat_list_preview_gif = concat_list_preview

        # Concat the videos into 1 before continuing
        concat_output_file = os.path.join(temp_folder, f'{new_filename_base_name}_concatOutputfile.mp4')

        concat_result, concat_result_path = await concat_video_segments(concat_list_preview, concat_output_file, transition_mode, available_transitions, transition_duration)
        if not concat_result or not concat_result_path:
            logger.error(f'Failed to concatenate video segments concat file')
            return False

        if is_vertical:
            if not black_bars:
                scale_option = "scale=-2:650"
            else:
                scale_option = "scale=650:-2"
        else:
            scale_option = "scale=650:-2"

        # Create Preview files if selected
        # Create Preview WebP
        if create_webp_preview:
            webp_command = (
                f'ffmpeg -hide_banner -y -i "{concat_result_path}" '
                f'-vf "fps={webp_preview_fps},{scale_option}:flags=lanczos" '
                f'-c:v libwebp -quality 80 -lossless 0 -compression_level {webp_level} -loop 0 -an -fps_mode passthrough "{output_webp}"'
            )
            stdout, stderr, exit_code = await run_command(webp_command)
            if exit_code == 0 and os.path.exists(output_webp):
                logger.success(f'Preview WebP created successfully: {output_webp}')
                if upload_previews:
                    await upload_preview_asset(output_webp, new_filename_base_name, "Preview WebP")
            else:
                logger.error(f'Failed to create WebP: {stderr}')
                return False
        # Create Preview AVIF (animated, AV1)
        if avif["preview"]:
            avif_command = (
                f'ffmpeg -hide_banner -y -i "{concat_result_path}" '
                f'-vf "fps={avif["fps"]},{scale_option}:flags=lanczos,format=yuv420p" {avif_codec_args(avif)} -an -loop 0 -f avif "{output_avif}"'
            )
            stdout, stderr, exit_code = await run_command(avif_command)
            if exit_code == 0 and os.path.exists(output_avif):
                logger.success(f'Preview AVIF created successfully: {output_avif}')
                if upload_previews:
                    await upload_preview_asset(output_avif, new_filename_base_name, "Preview AVIF")
            else:
                logger.error(f'Failed to create AVIF: {stderr}')
                return False
        # Create Preview WebM
        if create_webm_preview:
            webm_command = (
                f'ffmpeg -hide_banner -y -i "{concat_result_path}" '
                f'-c:v libvpx-vp9 -b:v 3M -vf "scale=iw:ih:flags=lanczos" '
                f'-crf 20 -deadline good -cpu-used 4 "{output_webm}"'
            )
            stdout, stderr, exit_code = await run_command(webm_command)
            if exit_code == 0 and os.path.exists(output_webm):
                logger.success(f'Preview WebM created successfully: {output_webm}')
            else:
                logger.error(f'Failed to create WebM: {stderr}')
                return False
        # Create concat video file for gif if create_gif_preview is true
        if create_gif_preview:
            concat_output_file_gif = os.path.join(temp_folder, f'{new_filename_base_name}_concatOutputfile_gif.mp4')
            concat_result_gif, concat_result_gif_path = await concat_video_segments(concat_list_preview_gif, concat_output_file_gif, transition_mode, available_transitions,
                                                                                    transition_duration)
            if not concat_result_gif or not concat_result_gif_path:
                logger.error(f'Failed to concatenate video segments concat file')
                return False
        # Create the preview gif if the concat output file has been created and its set to create gif
        if create_gif_preview and concat_result_gif_path:
            gif_command = (
                f'ffmpeg -hide_banner -y -i "{concat_result_gif_path}" -vf "fps={gif_preview_fps},{scale_option}:flags=lanczos,'
                f'split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 "{output_gif}"'
            )
            stdout, stderr, exit_code = await run_command(gif_command)
            if exit_code == 0 and os.path.exists(output_gif):
                logger.success(f'Preview GIF created successfully: {output_gif}')
                if upload_previews:
                    await upload_preview_asset(output_gif, new_filename_base_name, "Preview GIF")
            else:
                logger.error(f'Failed to create GIF: {stderr}')
                return False

        # Sheet Creation Segment
        concat_list_sheet = await filter_and_save_timestamped(concat_list, timestamps_mode, is_sheet=True)
        if create_webp_preview_sheet or create_gif_preview_sheet or create_webm_preview_sheet or avif["sheet"]:
            await generate_and_run_ffmpeg_commands(concat_list_sheet, temp_folder, create_webp_preview_sheet, preview_sheet_webp, video_path, segment_cut_duration, grid,
                                                   is_vertical, black_bars, create_gif_preview_sheet, preview_sheet_gif, gif_preview_fps, webp_preview_fps, create_webm_preview_sheet,
                                                   preview_sheet_webm, upload_previews, new_filename_base_name, add_file_info, font_path,
                                                   num_of_segments, fit_thumbs_in_less_rows,
                                                   avif=avif, preview_sheet_avif=preview_sheet_avif, webp_level=webp_level)
        if keep_temp_files:
            # logger.debug("Keeping temp files")
            pass
        else:
            shutil.rmtree(temp_folder, ignore_errors=True)
            # logger.debug("Temporary files removed")
            pass

        # logger.debug(f'Finished processing file: {video_path}")
        await asyncio.sleep(0.5)
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
        return f'{hours:02}:{minutes:02}:{seconds:02}'
    except Exception as e:
        logger.error(f'Error formatting duration: {e}')
        return "00:00:00"


async def ask_delete_file(file_path, ignore_existing):
    """Asks the user whether to delete a file or skip."""
    try:
        if ignore_existing:
            choice = "yes"
        else:
            choice = (await prompt(f'Do you want to delete existing file: "{file_path}"? (yes/no): ')).strip().lower()

        if choice in ["yes", "y"]:
            os.remove(file_path)
            # logger.debug(f'Deleted existing file: {file_path}")
            return True
        elif choice in ["no", "n"]:
            # logger.debug(f'Skipped file: {file_path}")
            return False
        else:
            logger.warning("Invalid input. Skipping file by default.")
            return False
    except Exception as e:
        logger.error(f'Error while processing file: {e}')
        return False


async def generate_cut_points(
        num_of_segments, blacklisted_cut_points, confirm_cut_points_required, duration, segment_cut_duration, temp_folder, is_vertical, black_bars,
        timestamps_mode, preview_sheet_required, video_path, filename_without_ext, print_cut_points, last_cut_point, font_path, width, height,
        preview_quality_resolution, scene_threshold):
    """Generate unique evenly spaced cut points with random variations."""
    temp_files_preview = None
    calc_failed_counter = 0
    max_failures = 500

    while True:
        # Safety stop
        if calc_failed_counter > max_failures:
            logger.error("Too many failed attempts to generate valid cut points. Aborting.")
            break

        # Reduce duration step-by-step if failing
        if calc_failed_counter % 50 == 0 and calc_failed_counter > 0:
            if segment_cut_duration <= 0.1:
                logger.error("Segment duration too short to reduce")
                break
            segment_cut_duration = round(segment_cut_duration - 0.1, 2)
            logger.debug(
                f'cut point generation has failed {calc_failed_counter} times, '
                f'reducing segment cut duration by 0.1 to {segment_cut_duration}'
            )

        # Start / end percentages
        start_point = round(random.uniform(0.02, 0.08), 3)
        end_point = (
            round(random.uniform(0.975, 0.99), 3)
            if last_cut_point == 0
            else last_cut_point / duration
        )

        # Validate bounds
        if end_point <= start_point:
            calc_failed_counter += 1
            continue

        num_cuts = num_of_segments - 1
        if num_cuts <= 0:
            logger.error("num_of_segments must be >= 2")
            break

        step = (end_point - start_point) / num_cuts

        points = [start_point, end_point]

        for i in range(1, num_cuts):
            next_point = round(start_point + step * i + random.uniform(-0.02, 0.02), 3)

            if not (start_point < next_point < end_point):
                continue

            # float-safe blacklist check
            if any(abs(next_point - b) < 0.001 for b in blacklisted_cut_points):
                continue

            points.append(next_point)

        # Uniqueness check AFTER generation
        unique_points = sorted(set(points))

        if len(unique_points) != num_of_segments:
            logger.error(f'not enough cut points generated: {len(unique_points)}, cut points: {unique_points}')
            calc_failed_counter += 1
            continue

        # Convert percentages to absolute seconds (float for accuracy)
        cut_points_seconds = [duration * pct for pct in unique_points]

        # Check scene changes
        scene_change_found = False
        for ts in cut_points_seconds:
            if await check_scene_changes_at_timestamp(video_path, ts, segment_cut_duration, scene_threshold):
                # logger.debug(f'Scene change detected at cut point {i}: {ts:.2f} seconds. Regenerating cut points...")
                scene_change_found = True
                break

        if scene_change_found:
            calc_failed_counter += 1
            continue

        # Logging
        if confirm_cut_points_required or print_cut_points:
            logger.debug("Generated cut points with timestamp breakdown:")
            for i, pct in enumerate(unique_points, start=1):
                time_in_seconds = pct * duration
                formatted_time = await format_duration(time_in_seconds)
                logger.debug(
                    f'Segment {i}: {pct:.2%} | Time: {formatted_time} ({time_in_seconds})'
                )

            if confirm_cut_points_required:
                await asyncio.sleep(0.5)
                confirmation = (await prompt("Do you want to keep and use these cut points? ([y]es/[n]o): ")).strip().lower()
                if confirmation in ["no", "n"]:
                    logger.debug("Regenerating cut points...\n")
                    calc_failed_counter += 1
                    continue

        # Generate segments
        temp_files_preview = await generate_video_segments(
            video_path,
            filename_without_ext,
            cut_points_seconds,
            segment_cut_duration, duration, temp_folder, is_vertical, black_bars, timestamps_mode, preview_sheet_required, font_path, width, height, preview_quality_resolution
        )

        if not temp_files_preview:
            # Clean up temp folder and retry
            for filename in os.listdir(temp_folder):
                file_path = os.path.join(temp_folder, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            calc_failed_counter += 1
            continue

        return temp_files_preview

    logger.error("Failed to generate cut points")
    return None



async def generate_video_segments(video_path, filename_without_ext, cut_points, segment_cut_duration, duration, temp_folder, is_vertical, black_bars, timestamps_mode,
                                  preview_sheet_required, font_path, width, height, preview_quality_resolution):
    """Generates video segments from a given video and overlays timestamps on them."""
    temp_files_webp = []

    if preview_quality_resolution == "480p":
        h_scale = "854:480" if height >= 480 and width >= 854 else f'{width}:{height}'
    elif preview_quality_resolution == "720p" and height >= 720 and width >= 1280:
        h_scale = "1280:720"
    elif preview_quality_resolution == "1080p" and height >= 1080 and width >= 1920:
        h_scale = "1920:1080"
    else:
        if height < 720 and width < 1280:
            h_scale = f'{width}:{height}'
        else:
            h_scale = "1280:720"

    if preview_quality_resolution == "480p":
        v_scale = "480:854" if height >= 854 and width >= 480 else f'{width}:{height}'
    elif preview_quality_resolution == "720p" and height >= 1280 and width >= 720:
        v_scale = "720:1280"
    elif preview_quality_resolution == "1080p" and height >= 1920 and width >= 1080:
        v_scale = "1080:1920"
    else:
        if height < 1280 and width < 720:
            v_scale = f'{width}:{height}'
        else:
            v_scale = "720:1280"

    enc_pre, enc_args, enc_vf = await gpu.segment_encode_args()
    if is_vertical and black_bars:
        vf_filter = f'scale={h_scale}:force_original_aspect_ratio=decrease,pad={h_scale}:(ow-iw)/2:(oh-ih)/2'
    elif is_vertical:
        vf_filter = f'scale={v_scale}'
    else:
        vf_filter = f'scale={h_scale}'

    async def cut(index, start):
        """One segment (GPU decode/encode when enabled), plus its time-stamped copy for the sheet."""
        cut_duration = min(segment_cut_duration, duration - start)
        start_time_formatted = await format_time_filename(start)
        temp_file = os.path.join(
            temp_folder,
            f'{filename_without_ext}_start-{start_time_formatted}_cutpoint-{index}_position-{start:.2f}.mp4'
        )

        def ffmpeg_segment_command(hw):
            # hw = GPU decode flags; the CPU retry ("") also uses the CPU H.264 encoder
            pre, codec, vf_extra = (enc_pre, enc_args, enc_vf) if hw else ("", gpu.CPU_SEGMENT_ENCODE, "")
            return (f'ffmpeg -hide_banner {pre}-ss {start} {hw}-i "{video_path}" -map 0:v:0 {codec} '
                    f'-map_metadata -1 -map_chapters -1 -dn -sn -an -t {cut_duration} '
                    f'-vf "{vf_filter}{vf_extra}" "{temp_file}" -y')

        async with segment_slots:
            stdout, stderr, exit_code = await gpu.run_with_cpu_fallback("previews", ffmpeg_segment_command)
        if exit_code != 0 or not os.path.exists(temp_file):
            logger.error(f'Failed to extract segment {index} at {start} seconds')
            return []
        if timestamps_mode in [1, 2] and preview_sheet_required:
            async with overlay_slots:
                return [temp_file, await overlay_timestamp(temp_folder, temp_file, font_path, is_vertical, preview_quality_resolution)]
        return [temp_file]

    # segments are cut a few at a time (and time-stamped while the next ones are cut); the list keeps the
    # cut point order: segment 1, its time-stamped copy, segment 2, ...
    segment_slots, overlay_slots = asyncio.Semaphore(SEGMENT_JOBS), asyncio.Semaphore(OVERLAY_JOBS)
    results = await asyncio.gather(*(cut(index, start) for index, start in enumerate(cut_points, start=1) if start < duration))
    temp_files_webp = [f for files in results for f in files if f]
    if not temp_files_webp:
        logger.error("No segments extracted successfully.")
        return []
    return temp_files_webp  # Return list of processed segments


async def check_scene_changes_at_timestamp(video_path, timestamp, segment_cut_duration, scene_threshold):
    """
    Check for a scene change around a specific timestamp in a video.
    Looks at a short window to detect abrupt changes, including frames that aren't keyframes.

    :param segment_cut_duration: Duration of the segment to check for scene changes.
    :param video_path: Path to the video file.
    :param timestamp: Time in seconds to check for scene change.
    :return: True if scene change detected, else False.
    """
    # scene_threshold = 0.4
    try:
        # Run FFmpeg command to get the frame information
        vf = f"select='gt(scene,{scene_threshold})',showinfo"
        # NOTE: this command has no output target, so ffmpeg stops before decoding and no scene change is ever
        # reported (legacy behaviour, kept on purpose - adding "-f null -" would enable the check).
        probe_command = (
            f'ffmpeg -hide_banner -ss {max(timestamp - 0.1, 0)} -t {segment_cut_duration + 0.1} -i "{video_path}" '
            f'-vf "{vf}"'
        )
        stdout, stderr, exit_code = await run_command(probe_command)

        # Check for errors in stderr
        if "Error" in stderr:
            logger.error(f'Error in ffmpeg execution: {stderr}')
            return False

        # Log stdout and stderr for debugging
        # logger.debug(f'stderr: {stderr}")

        # Check for scene change in the output
        scene_change_detected = False
        for line in stderr.splitlines():
            if "pts_time" in line:  # Look for the frame's timestamp
                pts_time = float(line.split('pts_time:')[1].split()[0])  # Extract pts_time
                change_time = timestamp + pts_time
                scene_change_detected = True
                # logger.debug(f'Scene change detected at exact time: {change_time:.2f}s, Regenerating...")
                break

        return scene_change_detected

    except Exception as e:
        logger.exception(f'Failed to check scene at timestamp {timestamp:.2f}s: {e}')
        return False


async def overlay_timestamp(temp_folder, video_path, font_path, is_vertical, preview_quality_resolution):
    """Extracts timestamp from filename and overlays it on the video with shadow and spacing using configured font."""
    try:
        match = re.search(r'start-(\d{2}\.\d{2}\.\d{2})', video_path)
        if not match:
            logger.error(f"Could not extract timestamp from {video_path}")
            return None

        timestamp = match.group(1).replace(".", r"\:")  # Escape colons for FFmpeg
        output_path = f"timestamped_{os.path.basename(video_path)}"
        full_video_path = os.path.join(temp_folder, video_path)
        full_output_path = os.path.join(temp_folder, output_path)

        if not os.path.exists(font_path):
            logger.error(f"Font file not found at expected path: {font_path}")
            return None

        font_expr = f"fontfile='{font_path.replace('/', '//')}'"

        drawtext_filters = []
        shadow_offsets = [(-2, -2), (-2, 0), (-2, 2),
                          (0, -2), (0, 2),
                          (2, -2), (2, 0), (2, 2)]

        # define font size for vertical/horizontal videos
        if is_vertical:
            font_size = 40 if preview_quality_resolution == "480p" else 60
        else:
            if preview_quality_resolution == "480p":
                font_size = 80
            elif preview_quality_resolution == "720p":
                font_size = 120
            elif preview_quality_resolution == "1080p":
                font_size = 160
            else:
                font_size = 60

        for dx, dy in shadow_offsets:
            drawtext_filters.append(
                f"drawtext=text='{timestamp}':{font_expr}:fontcolor=black@1.0:fontsize={font_size}:"
                f"x=(w-text_w)-10+{dx}:y=10+{dy}:"
                f"alpha=1"
            )

        drawtext_filters.append(
            f"drawtext=text='{timestamp}':{font_expr}:fontcolor=white:fontsize={font_size}:"
            f"x=(w-text_w)-10:y=10:"
            f"borderw=0:alpha=1"
        )

        vf_filters = ",".join(drawtext_filters)

        ffmpeg_cmd = (
            f"ffmpeg -hide_banner -i \"{full_video_path}\" "
            f"-vf \"{vf_filters}\" "
            f"{INTERMEDIATE_ENCODE} -c:a copy \"{full_output_path}\" -y"
        )

        stdout, stderr, exit_code = await run_command(ffmpeg_cmd)

        if exit_code == 0 and os.path.exists(full_output_path):
            return full_output_path
        else:
            logger.error(f"Failed to overlay timestamp on {video_path}, Exit Code: {exit_code}, Error: {stderr}")
            return None

    except Exception as e:
        logger.exception(f"Exception occurred while overlaying timestamp on {video_path}: {e}")
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
        logger.info(f'"{new_file} already has {len(lines)} lines or fewer, no trimming needed.')
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
        new_file_path = f'{base_name}_edited{ext}'
    elif timestamps_mode == 3:
        return file_path
    else:
        raise ValueError("Error, no timestamp mode selected.")

    # Write the filtered lines to the new file
    with open(new_file_path, 'w', encoding='utf-8') as new_file:
        for line in filtered_lines:
            new_file.write(f'{line}\n')

    return new_file_path


async def generate_and_run_ffmpeg_commands(concat_file_path, temp_folder, create_webp_preview_sheet, preview_sheet_webp, file_path, segment_duration, grid, is_vertical,
                                           add_black_bars, create_gif_preview_sheet, preview_sheet_gif, gif_preview_fps, webp_preview_fps, create_webm_preview_sheet, preview_sheet_webm,
                                           upload_previews, new_filename_base_name, add_file_info, font_path,
                                           num_of_segments, fit_thumbs_in_less_rows, avif=None, preview_sheet_avif=None, webp_level=4):
    """Generates stacked video sheet, adds preview sheets (WebP, AVIF, WebM, GIF), and renames files as needed."""
    avif = avif or {"sheet": False}
    try:
        # Read the concat_list.txt file
        with open(concat_file_path, 'r') as file:
            lines = file.readlines()

        # Extract video file paths from the concat list
        video_files = [line.strip().split('\'')[1] for line in lines]

        # Group the video files in batches of 3 or 4
        if grid == 3:
            if is_vertical and fit_thumbs_in_less_rows and len(lines) >= 6:
                video_groups = [video_files[i:i + 3*2] for i in range(0, len(video_files), 3*2)]
            else:
                video_groups = [video_files[i:i + 3] for i in range(0, len(video_files), 3)]
        elif grid == 4:
            if is_vertical and fit_thumbs_in_less_rows and len(lines) >= 8:
                video_groups = [video_files[i:i + 4*2] for i in range(0, len(video_files), 4*2)]
            else:
                video_groups = [video_files[i:i + 4] for i in range(0, len(video_files), 4)]
        else:
            logger.error(f'Invalid grid value: {grid}. Only 3 or 4 are allowed.')
            return

        # Determine char_break_line based on layout
        if grid == 3:
            char_break_line = 75 if is_vertical and not add_black_bars else 110
        elif grid == 4:
            char_break_line = 105 if is_vertical and not add_black_bars else 130
        else:
            grid = 0

        duration, fps = await get_video_duration(file_path)
        duration = int(duration)
        metadata_table, original_fps = await get_video_metadata(file_path, char_break_line, duration)
        if add_file_info:
            info_image_path = await create_info_image(metadata_table, temp_folder, new_filename_base_name, grid, is_vertical, add_black_bars, font_path)

            # Create the video from the info image (same resolution as image)
            final_image_video_path = os.path.join(temp_folder, new_filename_base_name + '_image_video.mp4')
            await create_video_from_image(info_image_path, final_image_video_path, fps=original_fps, duration=segment_duration)

        # One ffmpeg run builds the whole sheet: each row hstack-ed, the rows (under the info header) vstack-ed and,
        # with 4 columns, scaled to 1890 px wide. (Before 14.17: one encode per row, one for the stack, one to scale.)
        final_output = os.path.join(temp_folder, "final_thumbnail_sheet.mp4")
        inputs, chains, rows = [], [], []
        if add_file_info:
            inputs.append(f'-i "{final_image_video_path}"')
            rows.append("[0:v]")
        for row, group in enumerate(video_groups):
            first = len(inputs)
            inputs.extend(f'-i "{file}"' for file in group)
            tags = "".join(f"[{first + i}:v]" for i in range(len(group)))
            chains.append(f"{tags}hstack=inputs={len(group)}[r{row}]" if len(group) > 1 else f"{tags}null[r{row}]")
            rows.append(f"[r{row}]")
        chains.append(f"{''.join(rows)}vstack=inputs={len(rows)}[sheet]" if len(rows) > 1 else f"{rows[0]}null[sheet]")
        last = "sheet"
        if grid == 4:
            chains.append(f"[sheet]scale=1890:{(num_of_segments / grid) * 270}[scaled]")
            last = "scaled"
        command = (f'ffmpeg -hide_banner {" ".join(inputs)} -filter_complex "{";".join(chains)}" -map "[{last}]" '
                   f'{INTERMEDIATE_ENCODE} -an -y "{final_output}"')
        stdout, stderr, exit_code = await run_command(command)
        if exit_code != 0:
            logger.error(f'Building the preview sheet failed: {ffmpeg_error(stderr)}')
            return

        # Generate previews (WebP, WebM, GIF)
        results = ""
        # WebP Preview
        if create_webp_preview_sheet:
            webp_command = (
                f'ffmpeg -hide_banner -y -i "{final_output}" -vf "fps={webp_preview_fps},scale=iw:ih:flags=lanczos" '
                f'-c:v libwebp -quality 80 -lossless 0 -compression_level {webp_level} -loop 0 -an -fps_mode passthrough "{preview_sheet_webp}"'
            )
            stdout, stderr, exit_code = await run_command(webp_command)
            if exit_code != 0:
                logger.error(f'Error creating WebP preview: {stdout}\n{stderr}')
            else:
                results += f'WebP preview saved: {preview_sheet_webp}\n'
                if upload_previews:
                    await upload_preview_asset(preview_sheet_webp, new_filename_base_name, "Preview Sheet WebP")

        # AVIF Preview sheet
        if avif["sheet"] and preview_sheet_avif:
            avif_command = (
                f'ffmpeg -hide_banner -y -i "{final_output}" -vf "fps={avif["fps"]},scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,format=yuv420p" '
                f'{avif_codec_args(avif)} -an -loop 0 -f avif "{preview_sheet_avif}"'
            )
            stdout, stderr, exit_code = await run_command(avif_command)
            if exit_code != 0:
                logger.error(f'Error creating AVIF preview sheet: {stdout}\n{stderr}')
            else:
                results += f'AVIF preview saved: {preview_sheet_avif}\n'
                if upload_previews:
                    await upload_preview_asset(preview_sheet_avif, new_filename_base_name, "Preview Sheet AVIF")

        # WebM Preview
        if create_webm_preview_sheet:
            webm_command = (
                f'ffmpeg -hide_banner -y -i "{final_output}" -c:v libvpx-vp9 -b:v 3M -vf "scale=iw:ih:flags=lanczos" '
                f'-crf 20 -deadline good -cpu-used 4 "{preview_sheet_webm}"'
            )
            stdout, stderr, exit_code = await run_command(webm_command)
            if exit_code != 0:
                logger.error(f'Error creating WebM preview: {stdout}\n{stderr}')
            else:
                results += f'WebM preview saved: {preview_sheet_webm}\n'

        # GIF Preview
        if create_gif_preview_sheet:
            gif_command = (
                f'ffmpeg -hide_banner -y -i "{final_output}" -vf "scale=iw:ih:flags=lanczos,fps={gif_preview_fps}" '
                f'"{preview_sheet_gif}"'
            )
            stdout, stderr, exit_code = await run_command(gif_command)
            if exit_code != 0:
                logger.error(f'Error creating GIF preview: {stdout}\n{stderr}')
            else:
                results += f'GIF preview saved: {preview_sheet_gif}\n'
                if upload_previews:
                    await upload_preview_asset(preview_sheet_gif, new_filename_base_name, "Preview Sheet GIF")

        logger.info(f'Thumbnail sheet generation completed successfully.')
        logger.info(results)

    except Exception as e:
        logger.exception(f'Exception occurred in generate_and_run_ffmpeg_commands: {str(e)}')


async def get_video_metadata(file_path, char_break_line, duration):
    """Extract video metadata using pymediainfo."""
    filename = os.path.basename(file_path)
    file_dir = os.path.dirname(file_path)
    add_lines = 0

    try:
        media_info = MediaInfo.parse(file_path)
    except Exception as e:
        logger.error(f'Error parsing media info for {file_path}: {e}')
        return [], None

    # Initialize tracks
    video_track = None
    audio_track = None
    general_track = None

    try:
        for track in media_info.tracks:
            ttype = getattr(track, "track_type", "").lower()
            # logger.debug(f'Found track: id={getattr(track,'track_id',None)}, type={ttype}")
            if ttype == "video" and video_track is None:
                video_track = track
            elif ttype == "audio" and audio_track is None:
                # Ensure it has meaningful properties
                if getattr(track, "format", None) or getattr(track, "channel_s", None):
                    audio_track = track
            elif ttype == "general" and general_track is None:
                general_track = track
    except Exception as e:
        logger.error(f'Error iterating tracks for {file_path}: {e}')
        return [], None

    # Video properties
    try:
        video_codec = (video_track.format or "N/A").upper() if video_track else "N/A"
        video_profile = getattr(video_track, "format_profile", "N/A") if video_track else "N/A"
        video_bitrate = round(int(video_track.bit_rate or 0) / 1000) if video_track and video_track.bit_rate else 0
        width = video_track.width if video_track and video_track.width else "N/A"
        height = video_track.height if video_track and video_track.height else "N/A"
        resolution = f'{width}x{height}'

        # FPS
        try:
            fps = round(float(video_track.frame_rate), 2) if video_track and video_track.frame_rate else "N/A"
        except Exception as e:
            logger.error(f'Error parsing FPS: {e}')
            fps = "N/A"

        # CRF
        crf_value = "N/A"
        encoding_settings = getattr(video_track, "encoding_settings", "") if video_track else ""
        if encoding_settings and "crf=" in encoding_settings:
            try:
                crf_raw = encoding_settings.split("crf=")[1].split(" ")[0].replace("/", "").strip()
                crf_value = str(int(round(float(crf_raw))))
            except Exception as e:
                logger.error(f'Error parsing CRF: {e}')

        video_details = f'{video_codec} ({video_profile}) @ {video_bitrate} kbps, {fps} fps, CRF {crf_value}'
    except Exception as e:
        logger.error(f'Error extracting video properties: {e}')
        video_details = "N/A"
        resolution = "N/A"
        fps = None

    # Audio properties
    try:
        if audio_track:
            # logger.debug(f'Audio track raw data: {audio_track}")

            # Format / codec
            audio_codec = getattr(audio_track, "format", None)
            if audio_codec:
                audio_codec = str(audio_codec).upper()
            else:
                audio_codec = "N/A"

            # Channels
            audio_channels = getattr(audio_track, "channel_s", None)
            if audio_channels is None:
                audio_channels = "N/A"
            else:
                audio_channels = str(audio_channels)

            # Bitrate
            bit_rate = getattr(audio_track, "bit_rate", None)
            if bit_rate is None:
                audio_bitrate = 0
            else:
                try:
                    audio_bitrate = round(int(bit_rate) / 1000)
                except Exception as e:
                    logger.error(f'Error converting audio bit_rate "{bit_rate}" to int: {e}')
                    audio_bitrate = 0

            # Profile
            profile = getattr(audio_track, "format_profile", None)
            if profile:
                profile = str(profile)
                if audio_codec == "AAC" and "LC" in profile.upper():
                    audio_codec += " (LC)"

        else:
            audio_codec = "N/A"
            audio_channels = "N/A"
            audio_bitrate = 0

        audio_details = f'{audio_codec} ({audio_channels}ch) @ {audio_bitrate} kbps'

    except Exception as e:
        logger.error(f'Error extracting audio properties: {e}')
        audio_details = "N/A"

    # General metadata
    try:
        title = getattr(general_track, "title", "N/A") if general_track else "N/A"
        if len(title) > char_break_line:
            title = await break_string_at_char(title, " ", char_break_line)
            add_lines += 1

        if len(filename) > char_break_line:
            filename = await break_string_at_char(filename, ".", char_break_line) or \
                       await break_string_at_char(filename, " ", char_break_line) or \
                       await break_string_at_char(filename, "-", char_break_line)
            add_lines += 1

        size_bytes = int(getattr(general_track, "file_size", 0)) if general_track else 0
        size_mb = size_bytes / (1024 * 1024)
        size_gb = size_bytes / (1024 * 1024 * 1024)
        file_size = f'{size_gb:.2f} GB | {int(size_mb):,} MB'
    except Exception as e:
        logger.error(f'Error extracting general metadata: {e}')
        file_size = "N/A"

    # Duration formatting
    try:
        hours, remainder = divmod(int(duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        timestamp_str = f'{hours:02d}:{minutes:02d}:{seconds:02d}'
    except Exception as e:
        logger.error(f'Error formatting duration: {e}')
        timestamp_str = "N/A"

    # MD5 checksum
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        md5_hash = hash_md5.hexdigest()
    except Exception as e:
        logger.error(f'Error computing MD5 hash: {e}')
        md5_hash = "N/A"

    # Build info table
    try:
        info_table = [
            ["File Name", filename],
            ["Title", title],
            ["File Size", file_size],
            ["Duration", timestamp_str],
            ["A/V", f'Video: {video_details}, {resolution} | Audio: {audio_details}'],
            ["MD5", md5_hash.upper()]
        ]
        if add_lines != 0:
            for _ in range(add_lines):
                info_table.append([" ", " "])
    except Exception as e:
        logger.error(f'Error building info table: {e}')
        return [], fps

    return info_table, fps


async def break_string_at_char(s, break_char, char_break_line):
    if len(s) > char_break_line:
        # Find the position of the last space within the first 127 characters
        break_point = s.rfind(break_char, 0, char_break_line)

        # If there's a break char, replace it with a newline, otherwise, just return the string as is
        if break_point != -1:
            s = s[:break_point] + '\n' + s[break_point + 1:]
    return s


async def create_info_image(metadata_table, temp_folder, filename, grid, is_vertical, add_black_bars, font_path):
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

    img = Image.new("RGB", (width, height), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Load custom font if provided, else fallback to Arial or default
    try:
        if font_path and os.path.exists(font_path):
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.truetype('arial.ttf', font_size)
    except IOError:
        logger.warning("Specified font not found, using default font.")
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

    output_image_name = filename + '_info.png'
    output_image_path = os.path.join(temp_folder, output_image_name)
    try:
        img.save(output_image_path)
        # logger.debug(f'Image saved as {output_image_path}")
    except Exception as e:
        logger.error(f'Error saving image: {e}')

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
            "-hide_banner",
            "-i", image_path,
            "-vframes", "1",
            "-f", "image2pipe",
            "-c:v", "png",
            "-"
        ]

        # Get image resolution to ensure the video has the same dimensions
        stdout, stderr, exit_code = await run_command(command)
        if exit_code != 0:
            logger.error(f'Error getting image resolution: {stderr}')
            return

        # Use FFmpeg to create the video from the image
        ffmpeg_command = [
            "ffmpeg",
            "-hide_banner",
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
            # logger.debug(f'Video created successfully: "{output_path}")
            pass
        else:
            logger.error(f'Error creating video: {stderr}')

    except Exception as e:
        logger.error(f'Exception occurred: {e}')
