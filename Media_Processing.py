import subprocess
import cv2
import numpy as np
import os
import re
import requests
import shutil
import textwrap
import time
from io import BytesIO
from pathlib import Path
from loguru import logger
from mutagen.mp4 import MP4
from Utilities import run_command
from TPDB_API_Processing import get_performer_profile_picture
from PIL import Image, ImageDraw, ImageFont


async def get_existing_title(input_file):
    try:
        # Load the MP4 file using Mutagen
        audio = MP4(input_file)

        # Check if tags exist before trying to access them
        if audio.tags is not None:
            title = audio.tags.get('©nam')
            if title:
                return title[0].strip()  # Mutagen stores values as a list, take the first element
            else:
                # logger.warning(f"No title found in {input_file}")
                return None
        else:
            # logger.warning(f"No title found in {input_file}")
            return None
    except Exception:
        logger.exception(f"Error retrieving title from {input_file}")
        return None


async def get_existing_description(input_file):
    try:
        # Load the MP4 file using Mutagen
        audio = MP4(input_file)

        # Check if tags exist before trying to access them
        if audio.tags is not None:
            description = audio.tags.get('©cmt')
            if description:
                return description[0].strip()  # Mutagen stores values as a list, take the first element
            else:
                # logger.warning(f"No description found in {input_file}")
                return None
        else:
            # logger.warning(f"No description found in {input_file}")
            return None
    except Exception:
        logger.exception(f"Error retrieving description from {input_file}")
        return None


async def image_download_and_conversion(image_url: str,
                                        alt_image_url: str,
                                        original_name: str,
                                        new_name: str,
                                        target_dir: str) -> bool:
    try:
        os.makedirs(target_dir, exist_ok=True)
        target_path = Path(target_dir)

        original_jpg_path = target_path / f"{original_name}.jpg"
        final_webp_path = target_path / f"{new_name}.webp"

        # Step 1: If the final .webp image already exists, return True
        if final_webp_path.exists():
            return True

        # Step 2: If original .jpg exists, convert it to .webp
        if original_jpg_path.exists():
            try:
                if await convert_image_to_webp(original_jpg_path, output_name=new_name):
                    return True
                else:
                    logger.error(f"Conversion failed for {original_jpg_path}")
                    return False
            except Exception:
                logger.exception(f"Exception while converting {original_jpg_path} to webp")
                return False

        # Step 3: Function to download the image
        def download_image(url):
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '')

            if not content_type.startswith('image/'):
                logger.warning(f"No or unexpected Content-Type for URL: {url} (Got: '{content_type}')")
                # Try to validate by attempting to open as image
                try:
                    img = Image.open(BytesIO(response.content))
                    img.verify()  # Verifies it's an image but doesn't keep it open
                    # logger.debug("Image verified via content inspection.")
                except Exception as e:
                    raise ValueError(f"URL does not contain a valid image: {url}") from e

            return response

        # Step 4: Try downloading from image_url, then alt_image_url
        response = None
        try:
            response = download_image(image_url)
        except Exception as e:
            logger.error(f"Failed to download from primary URL: {image_url}, error: {e}")
            try:
                response = download_image(alt_image_url)
            except Exception as e:
                logger.error(f"Failed to download from alternative URL: {alt_image_url}, error: {e}")
                return False

        # Step 5: Save downloaded image as .jpg
        temp_jpg_path = target_path / f"{new_name}.jpg"
        with open(temp_jpg_path, "wb") as f:
            f.write(response.content)

        # Step 6: Convert downloaded .jpg to .webp
        try:
            if await convert_image_to_webp(temp_jpg_path, output_name=new_name):
                return True
            else:
                logger.error(f"Conversion failed for {temp_jpg_path}")
                return False
        except Exception:
            logger.exception(f"Exception while converting downloaded image to webp")
            return False

    except Exception:
        logger.exception("Unexpected error in image_download_and_conversion")
        return False


async def generate_scorp_thumbnails_and_conversion(filename, directory, original_filename, scorp_exe_path):
    """
    Generate a thumbnail for the given file and convert it to WebP format.

    Args:
        filename (str): New file name (without extension).
        directory (str): Directory containing the files.
        original_filename (str): Original file name (without extension).
        scorp_exe_path (str): Path to the executable used to generate thumbnails.
    """
    if not os.path.isfile(scorp_exe_path):
        logger.error(f"Invalid path to thumbnails_maker executable: {scorp_exe_path}")
        return False

    base_name = filename
    og_base_name = original_filename

    jpg_thumb_path = os.path.join(directory, f"{base_name}_thumbnails.jpg")
    webp_thumb_path = os.path.join(directory, f"{base_name}_thumbnails.webp")
    if base_name == og_base_name:
        if os.path.isfile(webp_thumb_path):
            logger.info(f"WebP thumbnail already exists: {webp_thumb_path}")
            return True
        if os.path.isfile(jpg_thumb_path):
            logger.info(f"JPG thumbnail exists: {jpg_thumb_path}. Converting to WebP...")
            try:
                success = await convert_image_to_webp(Path(jpg_thumb_path))
                if not success:
                    logger.error(f"Failed to convert JPG to WebP for: {jpg_thumb_path}")
                    return False
                else:
                    return True
            except Exception as e:
                logger.error(f"Exception during conversion to WebP: {e}")
                return False

        # Generate thumbnail
        full_video_path = os.path.join(directory, f"{filename}.mp4")
        command = f'"{scorp_exe_path}" "{full_video_path}" /silent'
        try:
            _, _, _ = await run_command(command)
        except Exception as e:
            logger.error(f"Failed to execute thumbnail generator: {e}")
            return False

        if os.path.isfile(jpg_thumb_path):
            try:
                success = await convert_image_to_webp(Path(jpg_thumb_path))
                if success:
                    logger.info(f"Thumbnail generated and converted successfully for {filename}")
                    return True
                else:
                    logger.error(f"Thumbnail generated but conversion to WebP failed for {filename}")
                    return False
            except Exception as e:
                logger.error(f"Exception during WebP conversion: {e}")
                return False
        else:
            logger.error(f"Thumbnail generation failed, JPG not found: {jpg_thumb_path}")
            return False
    else:
        # Case: filename != original_filename
        og_jpg_path = os.path.join(directory, f"{og_base_name}_thumbnails.jpg")
        og_webp_path = os.path.join(directory, f"{og_base_name}_thumbnails.webp")

        if os.path.isfile(og_webp_path):
            new_webp_path = os.path.join(directory, f"{base_name}_thumbnails.webp")
            try:
                os.rename(og_webp_path, new_webp_path)
                logger.info(f"Renamed existing WebP thumbnail from {og_webp_path} to {new_webp_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to rename WebP thumbnail: {e}")
                return False
        elif os.path.isfile(og_jpg_path):
            new_jpg_path = os.path.join(directory, f"{base_name}_thumbnails.jpg")
            try:
                os.rename(og_jpg_path, new_jpg_path)
                logger.info(f"Renamed existing JPG thumbnail from {og_jpg_path} to {new_jpg_path}")
                try:
                    success = await convert_image_to_webp(Path(new_jpg_path))
                    if success:
                        logger.info(f"Converted renamed JPG to WebP: {new_jpg_path}")
                        return True
                    else:
                        logger.error(f"Failed to convert renamed JPG to WebP: {new_jpg_path}")
                        return False
                except Exception as e:
                    logger.error(f"Exception during conversion of renamed JPG: {e}")
                    return False
            except Exception as e:
                logger.error(f"Failed to rename JPG thumbnail: {e}")
                return False
        else:
            # Generate thumbnail
            full_video_path = os.path.join(directory, f"{filename}.mp4")
            command = f'"{scorp_exe_path}" "{full_video_path}" /silent'
            try:
                _, _, _ = await run_command(command)
            except Exception as e:
                logger.error(f"Failed to execute thumbnail generator: {e}")
                return False

            if os.path.isfile(jpg_thumb_path):
                try:
                    success = await convert_image_to_webp(Path(jpg_thumb_path))
                    if success:
                        logger.info(f"Thumbnail generated and converted successfully for {filename}")
                        return True
                    else:
                        logger.error(f"Thumbnail generated but conversion to WebP failed for {filename}")
                        return False
                except Exception as e:
                    logger.error(f"Exception during WebP conversion: {e}")
                    return False
            else:
                logger.error(f"Thumbnail generation failed, JPG not found: {jpg_thumb_path}")
                return False


async def convert_image_to_webp(source_path: Path, output_name: str = None, quality: int = 85) -> bool:
    """
    Converts the given image file to WebP format and removes the original.

    Args:
        source_path (Path): The path to the original image file.
        output_name (str): Optional new name for the output WebP file (without extension).
        quality (int): Compression quality for WebP.

    Returns:
        bool: True if conversion succeeds, False otherwise.
    """
    try:
        if output_name:
            webp_path = source_path.with_name(f"{output_name}.webp")
        else:
            webp_path = source_path.with_suffix(".webp")

        with Image.open(source_path) as img:
            img.save(webp_path, format="WEBP", quality=quality, method=6)
        source_path.unlink()  # Remove original file
        logger.success(f"Converted to WebP: {webp_path}")
        return True
    except Exception:
        logger.exception(f"Failed to convert {source_path} to WebP")
        return False


async def generate_performer_profile_picture(performers, directory, tpdb_performer_url, target_size, zoom_factor, blur_kernel_size, posters_limit, MTCNN):
    """
        Creates a folder named 'faces' in the specified directory and processes performer pictures.

        :param posters_limit:
        :param target_size: Set the desired output size (X, Y)
        :param zoom_factor: Set the zoom factor for cropping
        :param blur_kernel_size: Kernel size for the Gaussian blur (adjust as needed)
        :param tpdb_performer_url: for debug purposes
        :param performers: List of tuples, where the second item in each tuple is a performer ID.
        :param directory: Path to the base directory where the 'faces' folder will be created.
        """
    try:
        faces_dir = os.path.join(directory, "faces")
        os.makedirs(faces_dir, exist_ok=True)
        logger.success(f"Created/verified directory: {faces_dir}")
    except Exception:
        logger.exception(f"Failed to create directory in: {directory}")
        return False

    for data in performers:
        try:
            if len(data) < 2:
                logger.warning(f"Skipping invalid tuple: {data}")
                continue
            performer_name = data[0]
            performer_id = data[1]
            logger.debug(f"Processing performer {performer_name}, ID: {performer_id}")
            performer_posters, performer_slug = await get_performer_profile_picture(performer_name, performer_id, posters_limit)
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
                await process_detection(file, faces_dir, zoom_factor, target_size, blur_kernel_size, performer_name, font_size, text_color, position_percentage, MTCNN)

        except Exception:
            logger.exception(f"Error processing performer {performer_name}, ID: {performer_id}")
            return False
    return True


async def download_poster_images(poster_urls: list[str], faces_dir: str, performer_slug: str, posters_limit: int):
    """
    Downloads up to the first N successful performer poster images, saves them as webp format.

    :param poster_urls: List of poster image URLs
    :param faces_dir: Directory to save the downloaded images
    :param performer_slug: Slug to include in the saved filename
    :param posters_limit: Max number of images to download
    :return: List of successfully saved poster file paths, or ["already downloaded"] if they exist, or False if none were saved
    """
    os.makedirs(faces_dir, exist_ok=True)
    downloaded_files = []

    # Check if images already exist for the performer
    existing_files = [
        f for f in os.listdir(faces_dir)
        if f.startswith(performer_slug) and f.lower().endswith(".webp")
    ]

    if existing_files:
        downloaded_files.append("already downloaded")
        logger.info(f"Performer posters already exist in {faces_dir}")
        return downloaded_files

    for index, url in enumerate(poster_urls, start=1):
        if len(downloaded_files) >= posters_limit:
            break

        try:
            # logger.debug(f"Attempting to download poster from: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Open image from response
            image = Image.open(BytesIO(response.content)).convert("RGB")

            # Save as webp format
            filename = f"{performer_slug}_{index}.webp"
            filepath = os.path.join(faces_dir, filename)
            image.save(filepath, format="WEBP")
            downloaded_files.append(filepath)
            logger.success(f"Saved image to {filepath}")

        except Exception as e:
            logger.warning(f"Failed to download or save poster {index} for {performer_slug}: {e}")

    if downloaded_files:
        return downloaded_files
    else:
        logger.error(f"All poster downloads failed for performer: {performer_slug}")
        return False


async def process_detection(image_path, output_path, zoom_factor, target_size, blur_kernel_size, text, font_size, text_color, position_percentage, MTCNN):
    filename = os.path.basename(image_path)
    base_filename = os.path.splitext(filename)[0]
    # Detect faces in the image
    bounding_boxes, keypoints, image = await detect_faces(image_path, MTCNN)

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
        output_file = f"{base_filename}-face-{i + 1}.webp"
        full_output_file_path = os.path.join(output_path, output_file)
        await save_face_image_with_rounded_corners(face, mask, full_output_file_path, target_size)

        await overlay_text(
            input_file=full_output_file_path,
            output_file=full_output_file_path,
            text=text,
            font_size=font_size,
            text_color=text_color,  # White text
            glow_color=(0, 0, 0),  # Black glow
            glow_thickness=3,  # Thickness of the glow
            bold=True,  # Simulated bold
            bold_thickness=0,  # Adjust thickness as needed
            max_chars_per_line=13,
            position_percentage=position_percentage,
            line_spacing=15  # Increased spacing for better readability
        )

        logger.success(f"Finished processing {image_path} - {output_file}")


async def overlay_text(
        input_file,
        output_file,
        text,
        font_size,
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

    # Try loading a common system font with specified size
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
        logger.warning("Falling back to default font. Font size may not match expected size.")

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


async def detect_faces(image_path, MTCNN):  # Adjust the threshold here

    threshold = 0.95

    detector = MTCNN()
    # Load the image
    image = cv2.imread(image_path)

    # Convert image to RGB (MTCNN expects RGB images)
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # Detect faces in the image
    faces = detector.detect_faces(rgb_image)

    # List to store bounding boxes and keypoints
    bounding_boxes = []
    keypoints = []

    # Extract the face bounding boxes and keypoints, applying the confidence threshold
    for face in faces:
        logger.debug(f"Confidence: {face['confidence']:.2f}")
        if face['confidence'] >= threshold:  # Only use faces with high confidence
            bounding_boxes.append(face['box'])  # Get the bounding box (x, y, width, height)
            keypoints.append(face['keypoints'])  # Get the keypoints (left eye, right eye, etc.)
        else:
            # logger.debug(f"Confidence level not high enough to pass threshold({threshold}): {face['confidence']:.2f}")
            pass

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


async def re_encode_video(new_filename, directory, keep_original_file):
    file_path = os.path.join(directory, new_filename)
    logger.info(f"Processing file: {file_path}")

    temp_output = await reencode_to_hevc(file_path)

    if temp_output is None:
        logger.debug(f"Already HEVC, skipping re-encode: {file_path}")
        return True

    if temp_output is False:
        logger.error(f"Skipping deletion, re-encoding failed for {file_path}")
        return False

    try:
        if keep_original_file:
            old_file_path = os.path.join(directory, f"{os.path.splitext(new_filename)[0]}_old{os.path.splitext(new_filename)[1]}")
            os.rename(file_path, old_file_path)
            logger.info(f"Original file renamed to: {old_file_path}")
        else:
            os.remove(file_path)

        final_output = os.path.join(directory, new_filename)
        shutil.move(temp_output, final_output)
        logger.info(f"Replaced original file with HEVC version: {final_output}")
    except Exception as e:
        logger.error(f"Failed to replace {file_path}: {e}")


async def get_video_duration(filepath):
    """Returns duration of the video in seconds using OpenCV."""
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        logger.error(f"Failed to open video file: {filepath}")
        return 0
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if fps == 0:
        logger.error(f"Invalid FPS value for file: {filepath}")
        return 0
    return int(frame_count // fps)


async def get_video_fps(video_path: str) -> float:
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise IOError(f"Failed to open video file: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return round(fps)


async def get_video_resolution_and_orientation(video_path: str) -> tuple[str, bool]:
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise IOError(f"Failed to open video file: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    # Match only specific standard resolutions
    if height >= 2160:
        resolution = "2160p"
    elif height >= 1080:
        resolution = "1080p"
    elif height >= 720:
        resolution = "720p"
    else:
        resolution = f"{height}p"

    is_vertical = height > width
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
    return f"{bitrate_kbps:.1f} kbps"


async def reencode_to_hevc(file_path):
    """Re-encode the given file to HEVC and show progress.

    Returns:
        str: Path to the converted file if successful.
        None: If already encoded in HEVC.
        False: If encoding failed.
    """
    if await is_hevc_encoded(file_path):
        return None

    directory, filename = os.path.split(file_path)
    temp_output = await generate_temp_filename(directory, filename)
    duration = await get_video_duration(file_path)

    ffmpeg_cmd = [
        "ffmpeg",
        "-hwaccel", "cuda",
        "-i", file_path,
        "-map", "0:v",
        "-map", "0:a",
        "-c:v", "libx265",
        "-vtag", "hvc1",
        "-x265-params", "crf=22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-map_metadata", "0",
        temp_output
    ]

    start_time = time.time()
    last_update_time = 0

    process = subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
    size_pattern = re.compile(r"size=\s*(\d+)KiB")
    speed_pattern = re.compile(r"speed=([\d\.x]+)")

    for line in process.stderr:
        now = time.time()
        if now - last_update_time >= 3:
            time_match = time_pattern.search(line)
            size_match = size_pattern.search(line)
            speed_match = speed_pattern.search(line)

            if time_match and size_match:
                encoded_time = parse_ffmpeg_time(time_match.group(1))
                current_size_kib = int(size_match.group(1))
                elapsed = now - start_time
                elapsed_human = format_eta(elapsed)
                speed = float(speed_match.group(1).replace('x', '')) if speed_match else 1.0

                if duration > 0 and speed > 0:
                    est_total_time = duration / speed
                    remaining = max(0, int(est_total_time) - int(elapsed))
                    eta_human = format_eta(remaining)

                    predicted_size_kib = (current_size_kib / encoded_time) * duration
                    predicted_size = format_size(predicted_size_kib)

                    estimated_bitrate = format_bitrate(current_size_kib, encoded_time)

                    logger.info(
                        f"Progress: Encoded {encoded_time:.2f}s / {duration}s "
                        f"({(encoded_time / duration) * 100:.1f}%), "
                        f"Speed: {speed:.2f}x, "
                        f"Elapsed: {elapsed_human}, "
                        f"ETA: {eta_human}, "
                        f"Predicted Final Size: {predicted_size}, "
                        f"Estimated Bitrate: {estimated_bitrate}"
                    )
                else:
                    logger.info(f"Progress: Encoded {encoded_time:.2f}s, Speed: {speed:.2f}x")

                last_update_time = now

    process.wait()
    if process.returncode != 0 or not os.path.exists(temp_output):
        logger.error(f"Re-encoding failed for {file_path}")
        return False

    return temp_output


async def generate_temp_filename(directory, original_name):
    """Generate a temporary filename for re-encoded file."""
    name, ext = os.path.splitext(original_name)
    return os.path.join(directory, f"{name}_temp{ext}")


async def is_hevc_encoded(file_path):
    """Check if a video file is encoded with HEVC using ffprobe."""
    command = f'ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "{file_path}"'

    try:
        stdout, stderr, code = await run_command(command)
        # logger.debug(stderr)

        # Compare stdout with "hevc" instead of stderr
        return stdout.strip().lower() == "hevc"

    except Exception as e:
        logger.error(f"Error checking codec for {file_path}: {e}")
        return False


async def get_video_codec(file_path):
    """Return the codec name of the first video stream in a video file using ffprobe."""
    command = f'ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "{file_path}"'

    try:
        stdout, stderr, code = await run_command(command)
        codec_name = stdout.strip().lower()
        return codec_name

    except Exception as e:
        logger.error(f"Error getting codec for {file_path}: {e}")
        return None


async def update_metadata(input_file, title, description, re_encode_hevc):
    """
    Updates the metadata of an MP4 video file with the specified title, description, and adds "HEVC" to the Tags field.

    Args:
        input_file (str): Path to the video file.
        title (str): Title to set for the video.
        description (str): Description to set for the video.

    Returns:
        bool: True if the metadata update was successful, False otherwise.
        :param description:
        :param title:
        :param input_file:
        :param re_encode_hevc:
    """
    try:
        # Load the MP4 file
        video = MP4(input_file)

        # Update metadata fields
        video["\xa9nam"] = title  # Title
        video["\xa9cmt"] = description  # Comment/Description
        if re_encode_hevc:
            # Add "HEVC" to the Tags field
            current_tags = video.get("\xa9gen", [])  # Get current tags or initialize an empty list
            if "HEVC" not in current_tags:
                current_tags.append("HEVC")  # Add "HEVC" if not already present
            video["\xa9gen"] = current_tags  # Update Tags field

        # Save changes
        video.save()

        # logger.info(f"Metadata updated successfully for: {input_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to update metadata for {input_file}: {e}")
        return False
