import requests
import json
from loguru import logger
import os
import base64
from Utilities import load_credentials


async def upload_to_hamster(hamster_api_key, hamster_album_id, filepath, img_title):
    """
    Upload an image to Hamster.is using Base64 encoding.
    """
    try:
        url = "https://hamster.is/api/1/upload"

        if not os.path.isfile(filepath):
            logger.error(f"File not found: {filepath}")
            return None

        # Read and encode the image to Base64
        with open(filepath, "rb") as img_file:
            encoded_image = base64.b64encode(img_file.read()).decode("utf-8")

        # Prepare headers and payload
        headers = {
            "X-API-Key": hamster_api_key,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        filename = os.path.basename(filepath)
        data = {
            "source": encoded_image,
            "filename": filename,  # 🧠 This preserves .webp extension
            "title": img_title,
            "format": "json",
            "nsfw": 1
        }

        if hamster_album_id:
            data["album_id"] = hamster_album_id

        # Send POST request
        # logger.debug(f"Uploading {filepath} as Base64...")
        response = requests.post(url, headers=headers, data=data)

        # Try parsing JSON
        try:
            resp_json = response.json()
        except Exception:
            logger.error(f"Invalid JSON response: {response.text}")
            return None

        # logger.debug(resp_json)
        # Check for success
        if resp_json.get("status_code") == 200:
            image_url = resp_json["image"]["url"]
            # logger.success(f"✅ Uploaded successfully: {image_url}")
            return image_url
        else:
            logger.error(f"Upload failed: {resp_json}")
            return None

    except Exception as e:
        logger.exception(f"An error occurred during upload: {e}")
        return None


async def hamster_upload_single_image(filepath, new_filename_base_name, mode):
    key = f"{new_filename_base_name} - {mode}"
    txt_filename = f"{new_filename_base_name}_hamster.txt"
    txt_filepath = os.path.join(os.path.dirname(filepath), txt_filename)
    img_title = f"{new_filename_base_name}_{mode}"

    hamster_album_id, hamster_api_key, _ = await load_credentials(5)

    if not hamster_api_key or not hamster_album_id:
        logger.error("Missing 'hamster_api_key' or 'hamster_album_id' in creds.secret.")
        exit(-99)

    result = await upload_to_hamster(hamster_api_key, hamster_album_id, filepath, img_title)
    result_json = {
        "image_url": result
    }

    if result:
        # logger.debug(f"Upload completed for image: {filepath} → {result}")
        if os.path.exists(txt_filepath):
            with open(txt_filepath, "r+", encoding="utf-8") as f:
                contents = f.read()
                try:
                    data = json.loads(contents)  # Try to load existing JSON data
                except json.JSONDecodeError:
                    data = {}  # If the file is not valid JSON, create a new dictionary

                # Add the result under the specific key
                if key not in data:
                    data[key] = []
                data[key].append(result_json)

                # Write the updated contents back to the file
                f.seek(0)
                f.write(json.dumps(data, indent=2))
                f.truncate()  # Ensure no leftover data
                # logger.debug(f"Appended new upload result under key '{key}' in existing file: {txt_filepath}")

        else:
            # If the file doesn't exist, create a new one and add the key with result
            data = {key: [result_json]}
            with open(txt_filepath, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))
                # logger.debug(f"Created new result file with key '{key}': {txt_filepath}")
        return True
    else:
        # logger.error(f"Upload failed for image: {filepath}")
        return False
