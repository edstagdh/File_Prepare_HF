import hashlib
import json
import os
import tempfile
from PIL import Image, ImageDraw, ImageFont
from loguru import logger
from Utilities import run_command, load_json_file
from Media_Processing import get_video_duration
import asyncio
import random
import time


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
            font_path = f"assets/{font_full_name}"
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


async def generate_contact_sheet(image_dir, thumb_width, columns, padding, output_path, timestamps, info_image_path, font_full_name, is_vertical, fit_thumbs_in_less_rows):
    """
    Generates a contact sheet from the extracted frames and saves it as an image.

    Args:
        image_dir (str): Directory containing the extracted frames.
        thumb_width (int): Width of each thumbnail.
        columns (int): Number of columns in the contact sheet.
        padding (int): Padding between the thumbnails.
        output_path (str): Path to save the generated contact sheet.
        timestamps (list): List of timestamps to add to the frames.
        info_image_path (str): Path of image with information.
        font_full_name: name of font to use
        fit_thumbs_in_less_rows (str): flag if number of thumbs should be doubled for vertical video
        is_vertical (bool): flag if video is vertical or not

    Raises:
        ValueError: If no thumbnails are found in the directory.
    """
    try:
        image_files = sorted(
            [os.path.join(image_dir, f) for f in os.listdir(image_dir) if f.endswith(".jpg")]
        )
        if not image_files:
            raise ValueError("No thumbnails found.")

        thumbs = []
        for i, file in enumerate(image_files):
            img = Image.open(file)
            # Resize the image to the desired thumb_width while maintaining aspect ratio
            ratio = thumb_width / img.width
            new_size = (thumb_width, int(img.height * ratio))
            img = img.resize(new_size)

            # Add timestamp to the top-right corner of each frame
            img = await add_timestamp_to_frame(img, timestamps[i], font_full_name)

            thumbs.append(img)

        if is_vertical and fit_thumbs_in_less_rows and len(timestamps) >= 6:
            rows = int(-(-len(thumbs) // columns / 2))
        else:
            rows = int(-(-len(thumbs) // columns))

        thumb_height = thumbs[0].height

        info_image = None
        info_width, info_height = 0, 0

        if info_image_path is not None and os.path.exists(info_image_path):
            info_image = Image.open(info_image_path)
            info_width, info_height = info_image.size

        # Ensure the contact sheet is at least as wide as the info image
        sheet_width = max((columns * thumb_width) + ((columns + 1) * padding), info_width)
        sheet_height = (rows * thumb_height) + ((rows + 1) * padding) + (info_height + padding if info_image else 0)

        contact_sheet = Image.new('RGB', (sheet_width, sheet_height), color=(0, 0, 0))

        # Paste info image at the top center if it exists
        if info_image:
            info_x = (sheet_width - info_width) // 2
            contact_sheet.paste(info_image, (info_x, padding))
            y_start = info_height + 2 * padding
        else:
            y_start = padding

        # Paste thumbnails
        for index, thumb in enumerate(thumbs):
            col = index % columns
            row = index // columns
            x = padding + col * (thumb_width + padding)
            y = y_start + row * (thumb_height + padding)
            contact_sheet.paste(thumb, (x, y))

        contact_sheet.save(output_path)
        logger.success(f"Contact sheet saved to {output_path}")

    except Exception as e:
        logger.exception(f"Error generating contact sheet from {image_dir}.")
        raise


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


async def create_info_image(metadata_table, temp_folder, filename, sheet_width):
    """Create an image displaying video metadata."""

    font_size = 18

    line_height = 30
    height = len(metadata_table) * line_height + 20

    img = Image.new("RGB", (sheet_width, height), color=(0, 0, 0))
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
        value_lines = str(value).split('\n')

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


async def break_string_at_char(s, break_char, char_break_line):
    if len(s) > char_break_line:
        # Find the position of the last space within the first 127 characters
        break_point = s.rfind(break_char, 0, char_break_line)

        # If there's a break char, replace it with a newline, otherwise, just return the string as is
        if break_point != -1:
            s = s[:break_point] + '\n' + s[break_point + 1:]
    return s


async def get_video_metadata(file_path, char_break_line, duration):
    """Extract video metadata using ffprobe."""
    filename = os.path.basename(file_path)
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

    size_bytes = int(format_info.get('size', '0'))
    size_mb = size_bytes / (1024 * 1024)
    size_gb = size_bytes / (1024 * 1024 * 1024)
    file_size = f"{size_gb:.2f} GB | {int(size_mb):,} MB"

    # Extract resolution and FPS
    width = video_info.get("width", "N/A")
    height = video_info.get("height", "N/A")
    resolution = f"{width}x{height}"

    # Convert timestamp to HH:MM:SS format
    hours, remainder = divmod(int(duration), 3600)
    minutes, seconds = divmod(remainder, 60)
    timestamp_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

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
        ["Duration", timestamp_str],
        ["A/V", f"Video: {video_details}, {resolution} | Audio: {audio_details}"],
        ["MD5", md5_hash.upper()]
    ]
    if add_lines != 0:
        for i in range(add_lines):
            info_table.append([" ", " "])  # Append an empty row with two empty strings to avoid overwriting last values in image
    return info_table, fps


async def output_file_exists(input_video_file_name, original_video_file_name, output_path, output_file_name_suffix, image_output_format):
    """
    Check if an output file already exists for the input or original video, and handle user input if both exist.
    """
    input_base_name, _ = os.path.splitext(input_video_file_name)
    original_base_name, _ = os.path.splitext(original_video_file_name)

    # Construct file paths for both original and input video files
    expected_input_output_file = os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}")
    expected_original_output_file = os.path.join(output_path, f"{original_base_name}_{output_file_name_suffix}.{image_output_format}")

    # Check if the original file exists
    original_exists = os.path.exists(expected_original_output_file)
    input_exists = os.path.exists(expected_input_output_file)

    if original_exists and input_exists and input_video_file_name.lower() != original_video_file_name.lower():
        # Ask user for input on how to handle both existing files
        logger.info(f"Both files '{expected_original_output_file}' and '{expected_input_output_file}' exist.")
        time.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep one of existing files or (R)egenerate? [K/R]: ").lower()

        if user_choice == "k":
            logger.info("User selected to keep one of the files, Which file would you like to keep? ")
            time.sleep(0.5)
            keep_file = input(f"(O)riginal '{expected_original_output_file}' or (I)nput '{expected_input_output_file}': ").lower()
            if keep_file == "o":
                os.remove(expected_input_output_file)
                os.rename(expected_original_output_file, os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}"))
                return True  # Renamed and kept the original
            elif keep_file == "i":
                os.remove(expected_original_output_file)
                os.rename(expected_input_output_file, os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}"))
                return True  # Renamed and kept the input
            else:
                logger.error("Invalid choice! Skipping file processing.")
                return False
        elif user_choice == "r":
            logger.info("User selected to regenerate the file.")
            # Regenerate output file
            return False  # Indicates regeneration needed
        else:
            logger.error("Invalid choice! Skipping file processing.")
            return False

    elif original_exists:
        # If only the original file exists, ask user whether to keep or regenerate
        logger.info(f"File '{expected_original_output_file}' exists.")
        time.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep it or (R)egenerate? [K/R]: ").lower()
        if user_choice == "k":
            os.rename(expected_original_output_file, os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}"))
            return True  # Renamed and kept the original
        elif user_choice == "r":
            os.remove(expected_original_output_file)
            logger.info("User selected to regenerate the file.")
            return False  # Regenerate the file
        else:
            logger.error("Invalid choice! Skipping file processing.")
            return False

    elif input_exists:
        # If only the input file exists, ask user whether to keep or regenerate
        logger.info(f"File '{expected_input_output_file}' exists.")
        time.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep it or (R)egenerate? [K/R]: ").lower()
        if user_choice == "k":
            os.rename(expected_input_output_file, os.path.join(output_path, f"{input_base_name}_{output_file_name_suffix}.{image_output_format}"))
            return True  # Renamed and kept the input
        elif user_choice == "r":
            os.remove(expected_input_output_file)
            logger.info("User selected to regenerate the file.")
            return False  # Regenerate the file
        else:
            logger.error("Invalid choice! Skipping file processing.")
            return False

    return False  # Neither file exists, so no issues to resolve


async def process_thumbnails(input_video_file_name, input_video_file_path, original_video_file_name, output_path, image_output_format, is_vertical):
    """
    Main function to process the video, generate thumbnails, and create a contact sheet.
    """
    try:
        input_video_file_base_name, _ = os.path.splitext(input_video_file_name)
        input_video_full_path = os.path.join(input_video_file_path, input_video_file_name)

        config, exit_code = await load_json_file("Configs/Config_Thumbnails.json")
        if not config:
            logger.error("Processing failed due to invalid configuration")
            return False
        else:
            num_thumbs = config["num_thumbs"]
            thumb_width = config["thumb_width"]
            columns = config["columns"]
            padding = config["padding"]
            output_file_name_suffix = config["output_file_name_suffix"]
            add_file_info = config["add_file_info"]
            font_full_name = config["font_full_name"]
            fit_thumbs_in_less_rows = config["fit_thumbs_in_less_rows"]
            force_regenerate = config["force_regenerate"]

        # Check if the output file already exists
        if await output_file_exists(input_video_file_name, original_video_file_name, output_path, output_file_name_suffix, image_output_format) and not force_regenerate:
            # logger.warning("Output file already exists. Skipping processing.")
            return True

        output_file_name_full = f"{input_video_file_base_name}_{output_file_name_suffix}.{image_output_format}"
        output_image_full_path = os.path.join(output_path, output_file_name_full)

        check_valid_numbers = await is_valid_integer_division(num_thumbs, columns)
        if not check_valid_numbers:
            logger.error("Processing failed due to invalid configuration: Number of thumbnails must be divisible by columns and greater than zero to avoid black thumbnails")
            return False

        # Vertical Adjustments
        if is_vertical and fit_thumbs_in_less_rows and num_thumbs >= 6:
            num_thumbs = int(num_thumbs * 2)
            columns = int(columns * 2)
            thumb_width = int(thumb_width / 2)
        char_break_line = 180
        if columns == 3:
            char_break_line = 130
        if columns == 4:
            char_break_line = 150

        duration = await get_video_duration(input_video_full_path)
        metadata_table, original_fps = await get_video_metadata(input_video_full_path, char_break_line, duration)

        timestamps = await generate_random_timestamps(duration, num_thumbs)

        with tempfile.TemporaryDirectory() as temp_dir:
            if add_file_info:
                sheet_width = int((columns * thumb_width) + ((columns + 1) * padding))
                info_image_path = await create_info_image(metadata_table, temp_dir, input_video_file_name, sheet_width)
            else:
                info_image_path = None

            await extract_frame_at_timestamps(input_video_full_path, timestamps, temp_dir)
            await generate_contact_sheet(temp_dir, thumb_width, columns, padding, output_image_full_path, timestamps, info_image_path, font_full_name, is_vertical,
                                         fit_thumbs_in_less_rows)

        return True

    except Exception as e:
        logger.exception(f"An error occurred during the video processing: {e}")
        return False


# Example usage
if __name__ == "__main__":
    asyncio.run(process_thumbnails("file_name.extension", r"path", "file_name.extension", r"path", "extension"))
