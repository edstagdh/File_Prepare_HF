import math

import cv2
import json
import numpy as np
import os
import re
import requests
import shutil
import subprocess
import textwrap
import time
from io import BytesIO
from loguru import logger
from mutagen.mp4 import MP4
from Utilities import run_command, load_json_file
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


async def cover_image_output_file_exists(input_video_file_name, original_video_file_name, output_path, image_output_format):
    """
    Check if an output file already exists for the input or original video, and handle user input if both exist.
    """
    input_base_name, _ = os.path.splitext(input_video_file_name)
    original_base_name, _ = os.path.splitext(original_video_file_name)

    # Construct file paths for both original and input video files
    expected_input_output_file = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
    expected_original_output_file = os.path.join(output_path, f"{original_base_name}.{image_output_format}")

    # Check if the original file exists
    original_exists = os.path.exists(expected_original_output_file)
    input_exists = os.path.exists(expected_input_output_file)

    if original_exists and input_exists and input_video_file_name.lower() != original_video_file_name.lower():
        # Ask user for input on how to handle both existing files
        logger.info(f"Both files '{expected_original_output_file}' and '{expected_input_output_file}' exist.")
        time.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep one of existing files or (R)e-download? [K/R]: ").lower()

        if user_choice == "k":
            logger.info("User selected to keep one of the files, Which file would you like to keep? ")
            time.sleep(0.5)
            keep_file = input(f"(O)riginal '{expected_original_output_file}' or (I)nput '{expected_input_output_file}': ").lower()
            if keep_file == "o":
                os.remove(expected_input_output_file)
                os.rename(expected_original_output_file, os.path.join(output_path, f"{input_base_name}.{image_output_format}"))
                return True  # Renamed and kept the original
            elif keep_file == "i":
                os.remove(expected_original_output_file)
                os.rename(expected_input_output_file, os.path.join(output_path, f"{input_base_name}.{image_output_format}"))
                return True  # Renamed and kept the input
            else:
                logger.error("Invalid choice! Skipping file processing.")
                return False
        elif user_choice == "r":
            logger.info("User selected to re-download file.")
            # Regenerate output file
            return False  # Re-download the cover image
        else:
            logger.error("Invalid choice! Skipping file processing.")
            return False

    elif original_exists:
        # If only the original file exists, ask user whether to keep or regenerate
        logger.info(f"File '{expected_original_output_file}' exists.")
        time.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep it or (R)e-download? [K/R]: ").lower()
        if user_choice == "k":
            os.rename(expected_original_output_file, os.path.join(output_path, f"{input_base_name}.{image_output_format}"))
            return True  # Renamed and kept the original
        elif user_choice == "r":
            os.remove(expected_original_output_file)
            logger.info("User selected to re-download the cover image.")
            return False  # Re-download the cover image
        else:
            logger.error("Invalid choice! Skipping file processing.")
            return False

    elif input_exists:
        # If only the input file exists, ask user whether to keep or regenerate
        logger.info(f"File '{expected_input_output_file}' exists.")
        time.sleep(0.5)
        user_choice = input(f"Would you like to (K)eep it or (R)e-download? [K/R]: ").lower()
        if user_choice == "k":
            os.rename(expected_input_output_file, os.path.join(output_path, f"{input_base_name}.{image_output_format}"))
            return True  # Renamed and kept the input
        elif user_choice == "r":
            os.remove(expected_input_output_file)
            logger.info("User selected to re-download the cover image")
            return False  # Re-download the cover image
        else:
            logger.error("Invalid choice! Skipping file processing.")
            return False

    return False  # Neither file exists, so no issues to resolve


async def cover_image_download_and_conversion(image_url: str,
                                              alt_image_url: str,
                                              input_video_file_name: str,
                                              original_video_file_name: str,
                                              output_path: str,
                                              image_output_format: str) -> bool:
    try:
        # Check if the output file already exists
        input_base_name, _ = os.path.splitext(input_video_file_name)
        if await cover_image_output_file_exists(input_video_file_name, original_video_file_name, output_path, image_output_format):
            # logger.warning("Output file already exists. Skipping processing.")
            return True

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

        # Save the downloaded image to a temporary file
        temp_image_path = os.path.join(output_path, f"temp_image.{image_output_format}")
        with open(temp_image_path, 'wb') as f:
            f.write(response.content)
        # logger.debug(f"Image saved to {temp_image_path}")

        # Check and downscale if the image exceeds 1080p resolution
        try:
            with Image.open(temp_image_path) as img:
                width, height = img.size
                if width > 1920 or height > 1080:
                    try:
                        resample = Image.Resampling.LANCZOS  # Pillow >= 10
                    except AttributeError:
                        resample = Image.LANCZOS  # Pillow < 10

                    img.thumbnail((1920, 1080), resample)
                    img.save(temp_image_path, format=image_output_format.upper())
                    logger.info(f"Image downscaled to fit within 1080p: {temp_image_path}")
        except Exception as e:
            logger.error(f"Error while checking/downscaling image resolution: {e}")

        final_image_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
        # Check if the image format matches the desired format
        if not temp_image_path.lower().endswith(f".{image_output_format}"):
            # Convert the image format if needed
            await convert_image_format(temp_image_path, final_image_path, image_output_format)
            os.remove(temp_image_path)  # Remove the temporary file after conversion
            logger.info(f"Converted image saved to {final_image_path}")
        else:
            shutil.move(temp_image_path, final_image_path)
            logger.info(f"Image moved to final destination: {final_image_path}")

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


async def generate_performer_profile_picture(performers, directory, tpdb_performer_url, target_size, zoom_factor, blur_kernel_size, posters_limit, MTCNN,
                                             performer_image_output_format, font_full_name):
    """
        Creates a folder named 'faces' in the specified directory and processes performer pictures.

        :param font_full_name:
        :param performer_image_output_format:
        :param MTCNN:
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

    for data in performers:
        try:
            if len(data) < 2:
                logger.warning(f"Skipping invalid tuple: {data}")
                continue
            performer_name = data[0]
            performer_id = data[1]

            if performer_name in performers_images:
                logger.debug(f"Performer {performer_name} already has mapped image in json file")
                continue
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
                await process_detection(file, faces_dir, zoom_factor, target_size, blur_kernel_size, performer_name, font_size, text_color, position_percentage, MTCNN,
                                        performer_image_output_format, font_full_name)

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


async def process_detection(image_path, output_path, zoom_factor, target_size, blur_kernel_size, text, font_size, text_color, position_percentage, MTCNN,
                            performer_image_output_format, font_full_name):
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


async def detect_faces(image_path, MTCNN):  # Adjust the threshold here

    threshold = 0.93

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


async def re_encode_video(new_filename, directory, keep_original_file, is_vertical, re_encode_downscale, limit_cpu_usage, remove_chapters):
    file_path = os.path.join(directory, new_filename)
    # logger.debug(f"Processing file: {file_path}")

    temp_output = await re_encode_to_hevc(file_path, is_vertical, re_encode_downscale, limit_cpu_usage, remove_chapters)

    if temp_output is None:
        # logger.debug(f"Already HEVC/AV1, skipping re-encode: {file_path}")
        return True

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


async def re_encode_to_hevc(file_path, is_vertical, re_encode_downscale, limit_cpu_usage, remove_chapters):
    """Re-encode the given file to HEVC and show progress.

    Returns:
        str: Path to the converted file if successful.
        None: If already encoded in HEVC.
        False: If encoding failed.
    """
    encode_results = await is_video_hevc_or_av1(file_path)
    if encode_results:  # Already encoded with HEVC/AV1
        return None
    if encode_results is None:  # Could not determine encoding (fail)
        return False

    width, height, bit_rate = await get_video_resolution(file_path)

    directory, filename = os.path.split(file_path)
    temp_output = await generate_temp_filename(directory, filename)
    duration, fps = await get_video_duration(file_path)

    keyint = int(fps * 2)  # every 2 seconds

    x265_params = (
        "crf=24:"
        "preset=medium:"
        "ref=3:"
        "limit-refs=2:"
        f"keyint={keyint}:"
    )

    if limit_cpu_usage:
        # Calculate half of total CPU cores (round up to avoid 0)
        half_threads = max(1, math.ceil(os.cpu_count() / 2))
        # Add x265 pools option to limit encoding threads
        x265_params += f"pools={half_threads}:"

    ffmpeg_cmd = [
        "ffmpeg",
        "-i", file_path,
        "-map", "0:v:0",
        "-map", "0:a",
        "-c:v", "libx265",
        "-vtag", "hvc1",
        "-x265-params", x265_params,
        "-c:a", "aac",
        "-b:a", "128k",
        "-map_metadata", "-1",
    ]

    if remove_chapters:
        ffmpeg_cmd += ["-map_chapters", "-1"]
    else:
        ffmpeg_cmd += ["-map_chapters", "0"]

    ffmpeg_cmd += [
        "-dn",
        "-sn"
    ]

    # Add scale filter if resolution is higher than 1080p and downscaling is enabled
    if re_encode_downscale and width and height:
        if not is_vertical and (width > 1920 or height > 1080):
            ffmpeg_cmd += ["-vf", "scale='min(1920,iw)':'min(1080,ih)'"]
        elif is_vertical and height > 1080:
            ffmpeg_cmd += ["-vf", "scale=-2:1080"]

    ffmpeg_cmd.append(temp_output)

    start_time = time.time()
    last_update_time = 0

    process = subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
    size_pattern = re.compile(r"size=\s*(\d+)KiB")
    speed_pattern = re.compile(r"speed=([\d\.x]+)")

    for line in process.stderr:
        now = time.time()
        if now - last_update_time >= 10:
            time_match = time_pattern.search(line)
            size_match = size_pattern.search(line)
            speed_match = speed_pattern.search(line)

            if time_match and size_match:
                encoded_time = parse_ffmpeg_time(time_match.group(1))
                current_size_kib = int(size_match.group(1))
                elapsed = now - start_time
                elapsed_human = format_eta(elapsed)
                speed = float(speed_match.group(1).replace('x', '')) if speed_match else 1.0

                if encoded_time > 0:  # Only proceed if encoded_time is greater than zero
                    if duration > 0 and speed > 0:
                        est_total_time = duration / speed
                        remaining = max(0, int(est_total_time) - int(elapsed))
                        eta_human = format_eta(remaining)
                        predicted_size_kib = (current_size_kib / encoded_time) * duration
                        predicted_size = format_size(predicted_size_kib)
                        estimated_bitrate = format_bitrate(current_size_kib, encoded_time)
                        msg = ""
                        msg += f"Encoded {encoded_time:.2f}s / {duration}s "
                        msg += f"({(encoded_time / duration) * 100:.1f}%), "
                        msg += f"Speed: {speed:.2f}x, "
                        msg += f"Elapsed: {elapsed_human}, "
                        msg += f"ETA: {eta_human}, "
                        msg += f"Estimated Size: {predicted_size}, "
                        msg += f"Estimated Bit rate: {estimated_bitrate}"
                        logger.info(msg)
                    else:
                        logger.info(f"Progress: Encoded {encoded_time:.2f}s, Speed: {speed:.2f}x")
                else:
                    logger.warning("Encoded time is zero, skipping bitrate prediction.")

                last_update_time = now

    process.wait()
    if process.returncode != 0 or not os.path.exists(temp_output):
        logger.error(f"Re-encoding failed for {file_path}, return code: {process.returncode}")
        return False

    return temp_output


async def is_video_hevc_or_av1(file_path: str) -> bool:
    """
    Check if the video is encoded with HEVC (H.265) or AV1.
    Returns True if encoded with either HEVC or AV1 (but not both), False otherwise, None if failed.
    """
    if not os.path.isfile(file_path):
        return False

    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        file_path
    ]

    try:
        stdout, stderr, returncode = await run_command(command)

        if returncode != 0:
            logger.error(f"ffprobe failed for {file_path}:\n{stderr}")
            return False

        data = json.loads(stdout)
        codec = data.get("streams", [{}])[0].get("codec_name", "").lower()
        if codec in {"hevc", "av1"}:
            codec_results = True
            return codec_results
        else:
            return False

    except Exception as e:
        logger.error(f"Failed to check codec for {file_path}: {e}")
        return None


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
    return int(frame_count // fps), fps


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
    """Return the codec name of the first video stream in a video file using ffprobe."""
    command = f'ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "{file_path}"'

    try:
        stdout, stderr, code = await run_command(command)
        codec_name = stdout.strip().lower()
        return codec_name

    except Exception as e:
        logger.error(f"Error getting codec for {file_path}: {e}")
        return None


async def update_metadata(input_file, title, description):
    """
    Updates the metadata of an MP4 video file with the specified title, description.

    Args:
        input_file (str): Path to the video file.
        title (str): Title to set for the video.
        description (str): Description to set for the video.

    Returns:
        bool: True if the metadata update was successful, False otherwise.
        :param description:
        :param title:
        :param input_file:
    """
    try:
        # Load the MP4 file
        video = MP4(input_file)

        # Update metadata fields
        video["\xa9nam"] = title  # Title
        video["\xa9cmt"] = description  # Comment/Description

        # Save changes
        video.save()

        # logger.info(f"Metadata updated successfully for: {input_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to update metadata for {input_file}: {e}")
        return False
