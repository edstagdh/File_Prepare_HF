"""Audio track inspection and filtering (keep a single / selected audio track)."""
import os
from loguru import logger
from fileprep.runtime import prompt
from pymediainfo import MediaInfo
from fileprep.utils.process import run_command


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
        logger.info(f"Audio tracks found in: {os.path.basename(file_path)}")
        logger.info("-" * 90)
        for idx, track in enumerate(audio_tracks):
            language = track.language or "und"
            title = track.title or "N/A"
            codec = track.codec_id or track.format or "N/A"
            bitrate = track.bit_rate or "N/A"
            default = track.default or "No"
            logger.info(f"[{idx + 1}] Language: {language} | Title: {title} | Codec: {codec} | "
                        f"Bitrate: {bitrate} | Default: {default}")
        logger.info("-" * 90)
    except Exception:
        logger.exception(f"Error displaying audio tracks for {file_path}")
        return None

    # --- Get user selection ---
    try:
        user_input = (await prompt("Enter track number(s) to keep (e.g. '1', '1,3', or 'all'): ")).strip().lower()

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
