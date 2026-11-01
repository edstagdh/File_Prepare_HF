"""Performer face portrait generation (poster download, YuNet face detection, crop and text overlay)."""
import cv2
import numpy as np
import os
import re
import httpx
import textwrap
import asyncio
from io import BytesIO
from loguru import logger
from PIL import Image, ImageDraw, ImageFont
from fileprep.tpdb.api_client import TpdbConnectionError
from fileprep.tpdb.performers import get_performer_profile_picture
from fileprep import paths
from fileprep.utils.files import load_json_file


async def generate_performer_profile_picture(performers, directory, tpdb_performer_url, target_size, zoom_factor, blur_kernel_size, posters_limit, face_detector_model_path,
                                             performer_image_output_format, font_full_name, face_score_threshold):
    """
        Creates a folder named 'faces' in the specified directory and processes performer pictures.

        :param font_full_name:
        :param performer_image_output_format:
        :param face_detector_model_path:
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
    performers_images, exit_code = await load_json_file(str(paths.PERFORMER_IMAGES_FILE))
    if exit_code != 0 or performers_images is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Create lowercase lookup map for fast case-insensitive checks
    lower_map = {k.lower(): k for k in performers_images.keys()}

    for data in performers:
        try:
            if len(data) < 2:
                logger.warning(f"Skipping invalid tuple: {data}")
                continue
            # Clean performer name if alias is included:
            translation_table = str.maketrans("", "", "!@#$%^&*()_+='")
            # Remove anything inside parentheses and the parentheses themselves
            performer_name = data[0]
            p = re.sub(r"\s*\([^)]*\)", "", performer_name)
            p = p.translate(translation_table)

            performer_id = data[1]

            if p.lower() in lower_map:
                logger.debug(f"Performer {p} already has mapped image in json file (case-insensitive)")
                continue
            logger.debug(f"Processing performer {p}, ID: {performer_id}")
            performer_posters, performer_slug = await get_performer_profile_picture(p, performer_id, posters_limit)
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
                await process_detection(file, faces_dir, zoom_factor, target_size, blur_kernel_size, p, font_size, text_color, position_percentage, face_detector_model_path,
                                        performer_image_output_format, font_full_name, face_score_threshold)

        except TpdbConnectionError:
            raise  # API down: stop the job instead of failing only this file
        except Exception:
            logger.exception(f"Error processing performer {p}, ID: {performer_id}")
            return False
    return True


async def download_poster_images(poster_urls, faces_dir, performer_slug, posters_limit):
    os.makedirs(faces_dir, exist_ok=True)
    downloaded_files = []

    existing_files = [f for f in os.listdir(faces_dir)
                      if f.startswith(performer_slug) and f.lower().endswith(".webp")]
    if existing_files:
        logger.info(f"Performer posters already exist in {faces_dir}")
        return ["already downloaded"]

    async def fetch_one(index, url):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            filename = f"{performer_slug}_{index}.webp"
            filepath = os.path.join(faces_dir, filename)
            image.save(filepath, format="WEBP")
            logger.success(f"Saved image to {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"Failed to download or save poster {index} for {performer_slug}: {e}")
            return None

    results = await asyncio.gather(*(fetch_one(i, url) for i, url in enumerate(poster_urls[:posters_limit], start=1)))
    downloaded_files = [r for r in results if r]

    return downloaded_files if downloaded_files else False


async def process_detection(image_path, output_path, zoom_factor, target_size, blur_kernel_size, text, font_size, text_color, position_percentage, face_detector_model_path,
                            performer_image_output_format, font_full_name, face_score_threshold):
    filename = os.path.basename(image_path)
    base_filename = os.path.splitext(filename)[0]
    # Detect faces in the image
    bounding_boxes, keypoints, image = await detect_faces(image_path, face_detector_model_path, face_score_threshold)

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
        font_path = str(paths.resource(font_full_name))
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

    # Save in the format of the output file's extension (portrait image format setting)
    pil_format = {"jpg": "JPEG", "jpeg": "JPEG"}.get(os.path.splitext(output_file)[1].lstrip(".").lower(),
                                                     os.path.splitext(output_file)[1].lstrip(".").upper() or "WEBP")
    if pil_format in ("JPEG", "BMP"):
        combined = combined.convert("RGB")
    combined.save(output_file, pil_format)
    logger.success(f"Saved output image as {pil_format}: {output_file}")


async def detect_faces(image_path, face_detector_model_path, face_score_threshold):
    image = cv2.imread(image_path)
    img_h, img_w = image.shape[:2]

    detector = cv2.FaceDetectorYN.create(
        model=face_detector_model_path,
        config="",
        input_size=(img_w, img_h),
        score_threshold=face_score_threshold,
    )

    _, faces = detector.detect(image)

    bounding_boxes = []
    keypoints = []

    for face in (faces if faces is not None else []):
        logger.debug(f"Confidence: {float(face[-1]):.2f}")
        x, y, w, h = face[0:4].astype(int)
        bounding_boxes.append([x, y, w, h])
        keypoints.append(face[4:14].reshape(5, 2).tolist())  # right eye, left eye, nose, right mouth, left mouth

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
