from loguru import logger
import pyimgbox
import os
import json
import asyncio


async def upload_single_image(filepath, new_filename_base_name, mode):
    try:
        # logger.debug(f"Starting anonymous upload for: {filepath}")
        gallery = pyimgbox.Gallery()

        async with gallery:
            async for submission in gallery.add([filepath]):
                if not submission['success']:
                    logger.error(f"Upload failed: {submission['filename']} - {submission['error']}")
                    return False

                logger.success(f"Upload successful: {submission['filename']}")
                result = {
                    "image_url": submission.get("image_url"),
                    "thumbnail_url": submission.get("thumbnail_url"),
                    "web_url": submission.get("web_url"),
                    "gallery_url": submission.get("gallery_url"),
                    "edit_url": submission.get("edit_url")
                }

                # === Save result to text file under a specific key ===
                key = f"{new_filename_base_name} - {mode}"
                result_str = json.dumps(result, indent=2)

                txt_filename = f"{new_filename_base_name}_imgbox.txt"
                txt_filepath = os.path.join(os.path.dirname(filepath), txt_filename)

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
                        data[key].append(result)

                        # Write the updated contents back to the file
                        f.seek(0)
                        f.write(json.dumps(data, indent=2))
                        f.truncate()  # Ensure no leftover data
                        # logger.debug(f"Appended new upload result under key '{key}' in existing file: {txt_filepath}")

                else:
                    # If the file doesn't exist, create a new one and add the key with result
                    data = {key: [result]}
                    with open(txt_filepath, "w", encoding="utf-8") as f:
                        f.write(json.dumps(data, indent=2))
                        # logger.debug(f"Created new result file with key '{key}': {txt_filepath}")

                return result

        logger.warning("Upload returned no result.")
        return False

    except Exception as e:
        logger.error(f"Exception during upload: {e}")
        return False



# # Example usage
# if __name__ == "__main__":
#     asyncio.run(upload_single_image(r"path\file_name.extension", "file_base_name"))
