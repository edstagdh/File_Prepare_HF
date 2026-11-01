"""MP4 container metadata: inspection, chapters, title/description/TPDB uuid tags and metadata reset."""
import os
import re
import shutil
import asyncio
import tempfile
from typing import List, Dict
from loguru import logger
from mutagen.mp4 import MP4
from pymediainfo import MediaInfo
from pathlib import Path
from fileprep.media.probe import get_video_duration
from fileprep.utils.process import run_command


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
