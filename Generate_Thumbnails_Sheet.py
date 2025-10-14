import hashlib
import os
import shutil
import asyncio
import random
from PIL import Image, ImageDraw, ImageFont
from loguru import logger
from Utilities import run_command, load_json_file
from Media_Processing import get_video_duration
from pymediainfo import MediaInfo


async def generate_random_timestamps(duration, count, preferred_min_gap=60, absolute_min_gap=5):
    """
    Generates a list of random timestamps within the video duration with a dynamic minimum gap between them.

    Args:
        duration (float): Duration of the video in seconds.
        count (int): Number of random timestamps to generate.
        preferred_min_gap (int): Preferred minimum gap in seconds between timestamps.
        absolute_min_gap (int): Absolute minimum allowable gap in seconds.

    Returns:
        list: A sorted list of random timestamps (float).
    """
    if count < 1:
        raise ValueError("Count must be at least 1.")
    if duration <= 1:
        raise ValueError("Duration must be greater than 1 second.")

    # Dynamically calculate minimum gap
    dynamic_gap = max(duration / (count * 1.5), absolute_min_gap)
    min_gap = min(dynamic_gap, preferred_min_gap)

    timestamps = []
    attempts = 0
    max_attempts = 1000

    while len(timestamps) < count and attempts < max_attempts:
        timestamp = random.uniform(1, duration - 1)

        if all(abs(timestamp - t) >= min_gap for t in timestamps):
            timestamps.append(timestamp)

        attempts += 1

    if len(timestamps) < count:
        raise RuntimeError(
            f"Failed to generate {count} timestamps with a gap of at least {min_gap:.2f} seconds within duration {duration}."
        )

    return sorted(timestamps)


async def extract_frame_at_timestamps(video_path, timestamps, output_dir):
    """
    Extracts frames at the given timestamps from the video and saves them as images.

    Args:
        video_path (str): Path to the video file.
        timestamps (list): List of timestamps (in seconds) where frames will be extracted.
        output_dir (str): Directory where the extracted frames will be saved.

    Raises:
        RuntimeError: If extracting any frame fails.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        tasks = []

        for i, ts in enumerate(timestamps, 1):
            output_file = os.path.join(output_dir, f"thumb_{i:04d}.jpg")
            command = (
                f'ffmpeg -hide_banner -loglevel error -ss {ts:.3f} -i "{video_path}" '
                f'-frames:v 1 "{output_file}"'
            )
            tasks.append(run_command(command))

        results = await asyncio.gather(*tasks)
        for idx, (_, stderr, code) in enumerate(results):
            if code != 0:
                raise RuntimeError(f"Failed to extract frame at {timestamps[idx]:.2f}s: {stderr}")

    except Exception as e:
        logger.exception(f"Error extracting frames from {video_path}: {str(e)}")
        raise


async def add_timestamp_to_frame(image, timestamp, font_full_name):
    """
    Adds a timestamp to the top-right corner of an image.

    Args:
        :param image: The image to which the timestamp will be added.
        :param timestamp: The timestamp to display on the image.
        :param font_full_name:

    Returns:
        PIL.Image.Image: The image with the added timestamp.

    """
    try:
        # Convert timestamp to HH:MM:SS format
        hours, remainder = divmod(int(timestamp), 3600)
        minutes, seconds = divmod(remainder, 60)
        timestamp_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        # Draw the timestamp in the top right corner
        draw = ImageDraw.Draw(image)

        try:
            font_path = f"{font_full_name}"
            font = ImageFont.truetype(font_path, size=32)  # Adjust size here
        except IOError:
            font = ImageFont.load_default()  # Fallback if font is not available

        # Use textbbox to get the size of the text
        text_bbox = draw.textbbox((0, 0), timestamp_str, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        # Add a glowing shadow (shadow from all directions)
        shadow_offset = 2  # Offset for the shadow around the text
        shadow_color = (0, 0, 0)  # Black shadow color

        # Adjust the starting position to move the text a bit more to the left
        # Let's decrease the padding from the right edge to prevent cropping
        current_x = image.width - text_width - 20  # Adjusted 10 pixels padding from the right
        current_y = 2  # 2 pixels padding from the top

        # Draw the shadow in multiple directions to create a glow effect
        for dx in range(-shadow_offset, shadow_offset + 1):
            for dy in range(-shadow_offset, shadow_offset + 1):
                if dx != 0 or dy != 0:  # Skip the center position to avoid overlapping
                    temp_x = current_x
                    for char in timestamp_str:
                        # Draw the shadow for each character
                        draw.text((temp_x + dx, current_y + dy), char, font=font, fill=shadow_color)
                        temp_x += font.getlength(char) + 2  # Adjusted spacing between characters

        # Draw the main timestamp (white text) with extra spacing between characters
        current_x = image.width - text_width - 20  # Reset x-coordinate to the adjusted left position

        # Adjust letter spacing by drawing each character individually with custom spacing
        for char in timestamp_str:
            draw.text((current_x, current_y), char, font=font, fill=(255, 255, 255))  # White text
            current_x += font.getlength(char) + 2  # Move the x-coordinate for the next character

        return image

    except Exception as e:
        logger.exception(f"Error adding timestamp {timestamp} to frame.")
        raise


async def generate_thumbnails_sheet(image_dir, thumb_width, columns, padding, output_path, timestamps,
                                 info_image_path, font_full_name, is_vertical, fit_thumbs_in_less_rows,
                                 alternate_layout=False):
    """
    Generates a Thumbnails Sheet from the extracted frames and saves it as an image.

    Args:
        image_dir (str): Directory containing the extracted frames.
        thumb_width (int): Width of each thumbnail.
        columns (int): Number of columns in the Thumbnails Sheet.
        padding (int): Padding between the thumbnails.
        output_path (str): Path to save the generated Thumbnails Sheet.
        timestamps (list): List of timestamps to add to the frames.
        info_image_path (str): Path of image with information.
        font_full_name: name of font to use
        fit_thumbs_in_less_rows (str): flag if number of thumbs should be doubled for vertical video
        is_vertical (bool): flag if video is vertical or not
        alternate_layout (bool): flag to enable alternate layout with one enlarged image

    Raises:
        ValueError: If no thumbnails are found in the directory.
    """
    try:
        image_files = sorted(
            [os.path.join(image_dir, f) for f in os.listdir(image_dir) if f.endswith(".jpg")]
        )
        if not image_files:
            raise ValueError("No thumbnails found.")

        total_slots = columns * columns if alternate_layout else None
        thumbs = []

        # Adjust image count for alternate layout
        if alternate_layout:
            if len(image_files) < 2:
                raise ValueError("Need at least 2 frames for alternate layout.")

            # First image will be enlarged
            first_image_file = image_files[0]
            remaining_images = image_files[1:]

            # Calculate number of images to keep
            target_frame_count = columns * columns - 3  # 3 slots used by the enlarged image

            if len(remaining_images) > target_frame_count:
                last_index = len(remaining_images) - 1
                num_needed = target_frame_count - 1  # reserve 1 slot for the last frame

                if num_needed < 0:
                    raise ValueError("Target frame count too small to keep last frame.")

                # Randomly sample from the rest, excluding last
                random_indexes = sorted(random.sample(range(last_index), num_needed))
                random_indexes.append(last_index)  # always include last frame

                remaining_images = [remaining_images[i] for i in random_indexes]

            # Reassemble image list
            image_files = [first_image_file] + remaining_images
            timestamps = [timestamps[0]] + [timestamps[i + 1] for i in random_indexes]

        for i, file in enumerate(image_files):
            img = Image.open(file)
            ratio = thumb_width / img.width
            new_size = (thumb_width, int(img.height * ratio))
            img = img.resize(new_size)

            img = await add_timestamp_to_frame(img, timestamps[i], font_full_name)
            thumbs.append(img)

        if is_vertical and fit_thumbs_in_less_rows and len(timestamps) >= 6:
            rows = int(-(-len(thumbs) // columns / 2))
        else:
            rows = int(-(-len(thumbs) // columns))

        thumb_height = thumbs[0].height

        info_image = Image.open(info_image_path)
        sheet_width = columns * thumb_width + (columns + 1) * padding
        sheet_height = info_image.height + padding + rows * (thumb_height + padding)

        thumbnails_sheet = Image.new('RGB', (sheet_width, sheet_height), color=(0, 0, 0))
        thumbnails_sheet.paste(info_image, (padding, padding))

        y_offset = info_image.height + 2 * padding

        index = 0
        for row in range(rows):
            for col in range(columns):
                if alternate_layout and index == 0:
                    # Paste the large image in top-left 2x2 grid
                    large_img = thumbs[0]
                    large_width = 2 * thumb_width + padding
                    large_height = 2 * thumb_height + padding
                    large_img_resized = large_img.resize((large_width, large_height))
                    thumbnails_sheet.paste(large_img_resized, (padding, y_offset))
                    index += 1
                    continue

                if alternate_layout and row < 2 and col < 2:
                    continue  # Skip 2x2 area used by large image

                if index >= len(thumbs):
                    break

                x = col * (thumb_width + padding) + padding
                y = y_offset + row * (thumb_height + padding)
                thumbnails_sheet.paste(thumbs[index], (x, y))
                index += 1

        thumbnails_sheet.save(output_path)
        logger.success(f"Thumbnails Sheet saved to: {output_path}")

    except Exception as e:
        logger.error(f"Error generating Thumbnails Sheet: {e}")


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


async def is_valid_integer_division(numerator, denominator):
    """
    Returns True if:
    - Both numerator and denominator are > 0
    - Numerator >= Denominator
    - Numerator is divisible by denominator without a remainder
    Otherwise returns False
    """
    try:
        if numerator <= 0 or denominator <= 0:
            return False
        if numerator < denominator:
            return False
        if denominator < 3:
            return False
        return numerator % denominator == 0
    except ZeroDivisionError:
        return False


async def create_info_image(metadata_table, temp_folder, filename, sheet_width, font_path=None):
    """Create an image displaying video metadata."""

    font_size = 18
    line_height = 30
    height = len(metadata_table) * line_height + 20

    img = Image.new("RGB", (sheet_width, height), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Load custom font if provided, else fallback to Arial or default
    try:
        if font_path and os.path.exists(font_path):
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        logger.warning("Specified font not found, using default font.")
        font = ImageFont.load_default()

    y_offset = 10
    for row in metadata_table:
        key, value = row

        # Check if the value is a coroutine and await it
        if callable(value):
            value = await value

        # Split value by newline for multiline content
        value_lines = str(value).split('\n')

        # Print the key and the first line of the value
        if len(key) > 1:
            draw.text((20, y_offset), key + " :", font=font, fill=(255, 255, 255))
        else:
            draw.text((20, y_offset), key + "  ", font=font, fill=(255, 255, 255))
        draw.text((150, y_offset), value_lines[0], font=font, fill=(255, 255, 255))
        y_offset += line_height

        # Print the subsequent lines
        for line in value_lines[1:]:
            draw.text((150, y_offset), line, font=font, fill=(255, 255, 255))
            y_offset += line_height

    output_image_name = filename + "_info.png"
    output_image_path = os.path.join(temp_folder, output_image_name)
    try:
        img.save(output_image_path)
    except Exception as e:
        logger.error(f"Error saving image: {e}")

    return output_image_path


async def break_string_at_char(s, break_char, char_break_line):
    if len(s) > char_break_line:
        # Find the position of the last space within the first 127 characters
        break_point = s.rfind(break_char, 0, char_break_line)

        # If there's a break char, replace it with a newline, otherwise, just return the string as is
        if break_point != -1:
            s = s[:break_point] + '\n' + s[break_point + 1:]
    return s


async def get_video_metadata(file_path, char_break_line, duration):
    """Extract video metadata using pymediainfo."""
    filename = os.path.basename(file_path)
    file_dir = os.path.dirname(file_path)
    add_lines = 0

    try:
        media_info = MediaInfo.parse(file_path)
    except Exception as e:
        logger.error(f"Error parsing media info for {file_path}: {e}")
        return [], file_dir, None

    # Initialize tracks
    video_track = None
    audio_track = None
    general_track = None

    try:
        for track in media_info.tracks:
            ttype = getattr(track, "track_type", "").lower()
            # logger.debug(f"Found track: id={getattr(track,'track_id',None)}, type={ttype}")
            if ttype == "video" and video_track is None:
                video_track = track
            elif ttype == "audio" and audio_track is None:
                # Ensure it has meaningful properties
                if getattr(track, "format", None) or getattr(track, "channel_s", None):
                    audio_track = track
            elif ttype == "general" and general_track is None:
                general_track = track
    except Exception as e:
        logger.error(f"Error iterating tracks for {file_path}: {e}")
        return [], file_dir, None

    # Video properties
    try:
        video_codec = (video_track.format or "N/A").upper() if video_track else "N/A"
        video_profile = getattr(video_track, "format_profile", "N/A") if video_track else "N/A"
        video_bitrate = round(int(video_track.bit_rate or 0) / 1000) if video_track and video_track.bit_rate else 0
        width = video_track.width if video_track and video_track.width else "N/A"
        height = video_track.height if video_track and video_track.height else "N/A"
        resolution = f"{width}x{height}"

        # FPS
        try:
            fps = round(float(video_track.frame_rate), 2) if video_track and video_track.frame_rate else "N/A"
        except Exception as e:
            logger.error(f"Error parsing FPS: {e}")
            fps = "N/A"

        # CRF
        crf_value = "N/A"
        encoding_settings = getattr(video_track, "encoding_settings", "") if video_track else ""
        if encoding_settings and "crf=" in encoding_settings:
            try:
                crf_raw = encoding_settings.split("crf=")[1].split(" ")[0].replace("/", "").strip()
                crf_value = str(int(round(float(crf_raw))))
            except Exception as e:
                logger.error(f"Error parsing CRF: {e}")

        video_details = f"{video_codec} ({video_profile}) @ {video_bitrate} kbps, {fps} fps, CRF {crf_value}"
    except Exception as e:
        logger.error(f"Error extracting video properties: {e}")
        video_details = "N/A"
        resolution = "N/A"
        fps = None

    # Audio properties
    try:
        if audio_track:
            # logger.debug(f"Audio track raw data: {audio_track}")

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
                    logger.error(f"Error converting audio bit_rate '{bit_rate}' to int: {e}")
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

        audio_details = f"{audio_codec} ({audio_channels}ch) @ {audio_bitrate} kbps"

    except Exception as e:
        logger.error(f"Error extracting audio properties: {e}")
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
        file_size = f"{size_gb:.2f} GB | {int(size_mb):,} MB"
    except Exception as e:
        logger.error(f"Error extracting general metadata: {e}")
        file_size = "N/A"

    # Duration formatting
    try:
        hours, remainder = divmod(int(duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        timestamp_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception as e:
        logger.error(f"Error formatting duration: {e}")
        timestamp_str = "N/A"

    # MD5 checksum
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        md5_hash = hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"Error computing MD5 hash: {e}")
        md5_hash = "N/A"

    # Build info table
    try:
        info_table = [
            ["File Name", filename],
            ["Title", title],
            ["File Size", file_size],
            ["Duration", timestamp_str],
            ["A/V", f"Video: {video_details}, {resolution} | Audio: {audio_details}"],
            ["MD5", md5_hash.upper()]
        ]
        if add_lines != 0:
            for _ in range(add_lines):
                info_table.append([" ", " "])
    except Exception as e:
        logger.error(f"Error building info table: {e}")
        return [], fps

    return info_table, fps


async def output_file_exists(input_video_file_name,
                             original_video_file_name,
                             output_path,
                             output_file_name_suffix,
                             image_output_format,
                             use_sub_folder,
                             sub_folder_path,
                             regeneration_mode):
    """
    Check if an output file already exists for the input or original video,
    and handle behavior depending on regeneration_mode:
        - "user input": prompt user interactively (default)
        - "force regenerate": delete any existing files and regenerate
        - "force keep": keep existing files and skip regeneration
    """
    input_base_name, _ = os.path.splitext(input_video_file_name)
    original_base_name, _ = os.path.splitext(original_video_file_name)

    expected_input_output_file = os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}")
    expected_original_output_file = os.path.join(output_path, f"{original_base_name}_{output_file_name_suffix}.{image_output_format}")

    sub_input_file = os.path.join(sub_folder_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}") if use_sub_folder and sub_folder_path else None
    sub_original_file = os.path.join(sub_folder_path, f"{original_base_name}_{output_file_name_suffix}.{image_output_format}") if use_sub_folder and sub_folder_path else None

    def find_existing_file(*paths):
        for p in paths:
            if p and os.path.exists(p):
                return p
        return None

    def safe_move(src, dst_dir):
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

    existing_input = find_existing_file(expected_input_output_file, sub_input_file)
    existing_original = find_existing_file(expected_original_output_file, sub_original_file)

    # ==============================================================
    # MODE: FORCE REGENERATE
    # ==============================================================
    if regeneration_mode.lower() == "force regenerate":
        for existing_file in filter(None, [existing_input, existing_original]):
            try:
                os.remove(existing_file)
                logger.info(f"[Force Regenerate] mode, Removed existing file: {existing_file}")
            except Exception as e:
                logger.error(f"Failed to remove {existing_file}: {e}")
        return False  # Always regenerate

    # ==============================================================
    # MODE: FORCE KEEP
    # ==============================================================
    if regeneration_mode.lower() == "force keep":
        kept_file = existing_input or existing_original
        if kept_file:
            logger.info(f"[Force Keep] mode, Keeping existing file: {kept_file}")
            final_path = os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}")
            if os.path.exists(final_path):
                # logger.debug(f"File already correctly named: {final_path}")
                pass
            else:
                os.rename(kept_file, final_path)
                logger.info(f"Renamed existing file to: {final_path}")

            if use_sub_folder and sub_folder_path:
                safe_move(final_path, sub_folder_path)
            return True  # Keep and skip regeneration
        else:
            logger.info("[Force Keep] mode, No existing file found — proceeding to regenerate.")
            return False  # Nothing to keep → regenerate

    # ==============================================================
    # MODE: USER INPUT
    # ==============================================================
    if existing_original and existing_input and input_video_file_name.lower() != original_video_file_name.lower():
        logger.info(f"Both files '{existing_original}' and '{existing_input}' exist.")
        await asyncio.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep one or (R)egenerate? [K/R]: ").lower()

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

            final_path = os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}")
            os.rename(kept_path, final_path)
            logger.info(f"Kept and renamed file: {final_path}")

            if use_sub_folder and sub_folder_path:
                safe_move(final_path, sub_folder_path)
            return True

        elif user_choice == "r":
            logger.info("User selected to regenerate.")
            return False
        else:
            logger.error("Invalid choice! Skipping.")
            return False

    for existing_file in [existing_original, existing_input]:
        if existing_file:
            logger.info(f"File '{existing_file}' exists.")
            await asyncio.sleep(0.5)
            user_choice = input(f"Would you like to (K)eep or (R)egenerate? [K/R]: ").lower()

            if user_choice == "k":
                final_path = os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}")
                os.rename(existing_file, final_path)
                logger.info(f"Kept and renamed file: {final_path}")

                if use_sub_folder and sub_folder_path:
                    safe_move(final_path, sub_folder_path)
                return True
            elif user_choice == "r":
                os.remove(existing_file)
                logger.info("User selected to regenerate file.")
                return False
            else:
                logger.error("Invalid choice! Skipping.")
                return False

    return False


async def process_thumbnails(input_video_file_name,
                             input_video_file_path,
                             original_video_file_name,
                             output_path,
                             image_output_format,
                             is_vertical,
                             use_sub_folder):
    """
    Main function to process the video, generate thumbnails, and create a Thumbnails Sheet.
    """
    try:
        input_video_file_base_name, _ = os.path.splitext(input_video_file_name)
        input_video_full_path = os.path.join(input_video_file_path, input_video_file_name)

        config, exit_code = await load_json_file("Configs/Config_Thumbnails.json")
        if not config:
            logger.error("Processing failed due to invalid configuration")
            return False

        num_thumbs = config["num_thumbs"]
        thumb_width = config["thumb_width"]
        columns = config["columns"]
        padding = config["padding"]
        output_file_name_suffix = config["output_file_name_suffix"]
        add_file_info = config["add_file_info"]
        font_full_name = config["font_full_name"]
        fit_thumbs_in_less_rows = config["fit_thumbs_in_less_rows"]
        regeneration_mode = config["regeneration_mode"]
        alternate_layout = config["alternate_layout"]

        # Check if output file already exists
        exists = await output_file_exists(
            input_video_file_name,
            original_video_file_name,
            input_video_file_path,
            output_file_name_suffix,
            image_output_format,
            use_sub_folder,
            output_path,
            regeneration_mode
        )

        # 🔹 If file exists and mode is "force keep" or user kept it, skip regeneration
        if exists and regeneration_mode.lower() != "force regenerate":
            return True

        output_file_name_full = f"{input_video_file_base_name}_{output_file_name_suffix}.{image_output_format}"
        output_image_full_path = os.path.join(output_path, output_file_name_full)

        if use_sub_folder and output_path:
            os.makedirs(output_path, exist_ok=True)
            output_image_full_path = os.path.join(output_path, output_file_name_full)

        check_valid_numbers = await is_valid_integer_division(num_thumbs, columns)
        if not check_valid_numbers:
            logger.error("Invalid configuration: num_thumbs must be divisible by columns.")
            return False

        # Adjust layout for vertical videos
        if is_vertical and fit_thumbs_in_less_rows and num_thumbs >= 6:
            num_thumbs = int(num_thumbs * 2)
            columns = int(columns * 2)
            thumb_width = int(thumb_width / 2)

        char_break_line = 180
        if columns == 3:
            char_break_line = 130
        if columns == 4:
            char_break_line = 150

        duration, fps = await get_video_duration(input_video_full_path)
        duration = int(duration)
        metadata_table, original_fps = await get_video_metadata(input_video_full_path, char_break_line, duration)
        if not metadata_table or not original_fps:
            logger.error("Failed to extract video file metadata for thumbnails.")
            return False

        timestamps = await generate_random_timestamps(duration, num_thumbs)
        font_path = f"Resources\\{font_full_name}"

        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            if add_file_info:
                sheet_width = int((columns * thumb_width) + ((columns + 1) * padding))
                info_image_path = await create_info_image(metadata_table, temp_dir, input_video_file_name, sheet_width, font_path)
            else:
                info_image_path = None

            await extract_frame_at_timestamps(input_video_full_path, timestamps, temp_dir)
            await generate_thumbnails_sheet(
                temp_dir, thumb_width, columns, padding, output_image_full_path,
                timestamps, info_image_path, font_path, is_vertical,
                fit_thumbs_in_less_rows, alternate_layout
            )

        logger.info(f"Thumbnail sheet created at: {output_image_full_path}")
        return True

    except Exception as e:
        logger.exception(f"An error occurred during thumbnail processing: {e}")
        return False
