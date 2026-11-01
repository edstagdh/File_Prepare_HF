"""Cover image download, conversion and regeneration handling."""
import os
import httpx
import shutil
import asyncio
from io import BytesIO
from loguru import logger
from fileprep.runtime import prompt
from PIL import Image


async def cover_image_output_file_exists(input_video_file_name,
                                         original_video_file_name,
                                         output_path,
                                         image_output_format,
                                         cover_regeneration_mode,
                                         use_sub_folder=False,
                                         sub_folder_path=None):
    """
    Check if an output file already exists for the input or original video,
    and handle it according to the cover_regeneration_mode:
      - 'user input': prompt user
      - 'force regenerate': always regenerate
      - 'force keep': always keep existing file(s)
    """
    input_base_name, _ = os.path.splitext(input_video_file_name)
    original_base_name, _ = os.path.splitext(original_video_file_name)

    expected_input_output_file = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
    expected_original_output_file = os.path.join(output_path, f"{original_base_name}.{image_output_format}")

    # Also check subfolder if enabled
    sub_input_file = os.path.join(sub_folder_path, f"{input_base_name}.{image_output_format}") if use_sub_folder and sub_folder_path else None
    sub_original_file = os.path.join(sub_folder_path, f"{original_base_name}.{image_output_format}") if use_sub_folder and sub_folder_path else None

    def find_existing_file(*paths):
        for p in paths:
            if p and os.path.exists(p):
                return p
        return None

    existing_input = find_existing_file(expected_input_output_file, sub_input_file)
    existing_original = find_existing_file(expected_original_output_file, sub_original_file)

    def safe_move(src, dst_dir):
        """Safely move file to dst_dir, renaming if necessary."""
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

    # --- FORCE REGENERATE ---
    if cover_regeneration_mode == "force regenerate":
        # Delete any existing file(s)
        for existing_file in [existing_original, existing_input]:
            if existing_file and os.path.exists(existing_file):
                os.remove(existing_file)
                logger.info(f"[Force Regenerate] mode, Removed existing file:  {existing_file}")
        return False  # Always regenerate

    # --- FORCE KEEP ---
    if cover_regeneration_mode == "force keep":
        for existing_file in [existing_original, existing_input]:
            if existing_file and os.path.exists(existing_file):
                logger.info(f"[Force Keep] mode, Keeping existing file: '{existing_file}'")
                final_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
                if existing_file != final_path:
                    os.rename(existing_file, final_path)
                    logger.info(f"Renamed existing file to: {final_path}")
                if use_sub_folder and sub_folder_path:
                    safe_move(final_path, sub_folder_path)
                return True
        logger.info("[Force Keep] mode, No existing file found — Generating")
        return False

    # --- USER INPUT MODE (default behavior) ---
    if cover_regeneration_mode == "user input":
        if existing_original and existing_input and input_video_file_name.lower() != original_video_file_name.lower():
            logger.info(f"Both files '{existing_original}' and '{existing_input}' exist.")
            await asyncio.sleep(0.5)
            user_choice = (await prompt(f"Would you like to (K)eep one of existing files or (R)e-download? [K/R]: ")).lower()

            if user_choice == "k":
                await asyncio.sleep(0.5)
                keep_file = (await prompt(f"(O)riginal '{existing_original}' or (I)nput '{existing_input}': ")).lower()
                if keep_file == "o":
                    os.remove(existing_input)
                    kept_path = existing_original
                elif keep_file == "i":
                    os.remove(existing_original)
                    kept_path = existing_input
                else:
                    logger.error("Invalid choice! Skipping file processing.")
                    return False

                final_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
                os.rename(kept_path, final_path)
                logger.info(f"Kept and renamed file: {final_path}")

                if use_sub_folder and sub_folder_path:
                    safe_move(final_path, sub_folder_path)
                return True

            elif user_choice == "r":
                logger.info("User selected to re-download file.")
                return False
            else:
                logger.error("Invalid choice! Skipping file processing.")
                return False

        # Handle only one existing file
        for existing_file in [existing_original, existing_input]:
            if existing_file:
                logger.info(f"File '{existing_file}' exists.")
                await asyncio.sleep(0.5)
                user_choice = (await prompt(f"Would you like to (K)eep it or (R)e-download? [K/R]: ")).lower()

                if user_choice == "k":
                    final_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
                    os.rename(existing_file, final_path)
                    logger.info(f"Kept and renamed file: {final_path}")

                    if use_sub_folder and sub_folder_path:
                        safe_move(final_path, sub_folder_path)
                    return True
                elif user_choice == "r":
                    os.remove(existing_file)
                    logger.info("User selected to re-download cover image.")
                    return False
                else:
                    logger.error("Invalid choice! Skipping file processing.")
                    return False

    # No existing file found → regenerate
    return False


async def cover_image_download_and_conversion(image_url: str,
                                              tpdb_image_url: str,
                                              input_video_file_name: str,
                                              original_video_file_name: str,
                                              output_path: str,
                                              image_output_format: str,
                                              use_sub_folder,
                                              sub_folder_path,
                                              cover_regeneration_mode) -> bool:
    try:
        input_base_name, _ = os.path.splitext(input_video_file_name)

        exists = await cover_image_output_file_exists(
            input_video_file_name,
            original_video_file_name,
            output_path,
            image_output_format,
            cover_regeneration_mode,
            use_sub_folder,
            sub_folder_path
        )

        # If file exists, and we’re not regenerating, skip download
        if exists and cover_regeneration_mode != "force regenerate":
            return True

        # Download helper: retries timeouts, network errors, 5xx and 429 (e.g. "502 Bad Gateway")
        async def download_image(url, attempts=3):
            for attempt in range(1, attempts + 1):
                try:
                    async with httpx.AsyncClient(follow_redirects=True) as client:
                        response = await client.get(url, timeout=20)
                    if response.status_code >= 500 or response.status_code == 429:
                        raise httpx.HTTPStatusError(f"HTTP {response.status_code}", request=response.request, response=response)
                    response.raise_for_status()
                    break
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    retryable = isinstance(exc, httpx.TransportError) or exc.response.status_code >= 500 or exc.response.status_code == 429
                    if not retryable or attempt == attempts:
                        raise
                    logger.warning(f"Cover download attempt {attempt}/{attempts} failed ({exc}) - retrying")
                    await asyncio.sleep(2 * attempt)
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                try:
                    img = Image.open(BytesIO(response.content))
                    img.verify()
                except Exception as e:
                    raise ValueError(f"URL does not contain a valid image: {url}") from e
            return response

        # Try the scene image, then the TPDB copy
        response, errors = None, []
        for url in [u for u in (image_url, tpdb_image_url) if u]:
            try:
                response = await download_image(url)
                break
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        if response is None:
            logger.warning("No cover image could be downloaded - the file continues without a cover. "
                           + (" | ".join(errors) if errors else "TPDB has no image for this scene."))
            return True

        temp_image_path = os.path.join(output_path, f"temp_image.{image_output_format}")
        with open(temp_image_path, "wb") as f:
            f.write(response.content)

        # Downscale if too large
        try:
            with Image.open(temp_image_path) as img:
                width, height = img.size
                if width > 1920 or height > 1080:
                    resample = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
                    img.thumbnail((1920, 1080), resample)
                    img.save(temp_image_path, format=image_output_format.upper())
                    logger.info(f"Image downscaled to fit within 1080p: {temp_image_path}")
        except Exception as e:
            logger.error(f"Error while checking/downscaling image: {e}")

        final_image_path = os.path.join(output_path, f"{input_base_name}.{image_output_format}")
        shutil.move(temp_image_path, final_image_path)
        logger.success(f"Image saved to {final_image_path}")

        if use_sub_folder and sub_folder_path:
            os.makedirs(sub_folder_path, exist_ok=True)
            sub_final_path = os.path.join(sub_folder_path, os.path.basename(final_image_path))
            shutil.move(final_image_path, sub_final_path)
            logger.info(f"Image moved to subfolder: {sub_final_path}")

        return True

    except Exception as e:
        logger.error(f"Unhandled error in cover image processing: {e}")
        return False
    finally:
        temp = os.path.join(output_path, f"temp_image.{image_output_format}")
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass


async def convert_image_format(input_file_path: str, output_file_path: str, output_format: str):
    """
    Converts an image to the specified format and saves it in the same directory.

    Args:
        input_file_path (str): Full path to the input image file.
        output_file_path (str): Path to save the image
        output_format (str): Target image format (e.g., "jpeg", "png", "webp", "avif", "jpg").

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
            "avif": "AVIF",
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
