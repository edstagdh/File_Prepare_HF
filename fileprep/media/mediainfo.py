"""MediaInfo text report generation."""
import os
from loguru import logger
from pymediainfo import MediaInfo


async def generate_mediainfo_file(input_file_full_path, output_path):
    try:
        # Split the full path into directory, file name, and extension
        _, file_name = os.path.split(input_file_full_path)
        file_base_name, _ = os.path.splitext(file_name)

        # Define the output text file name
        output_file = os.path.join(output_path, f"{file_base_name}_mediainfo.txt")

        # Check if the output file exists and delete it
        if os.path.exists(output_file):
            os.remove(output_file)
            # logger.debug(f"Existing mediainfo file {output_file} deleted.")

        # Use pymediainfo to parse the media file
        media_info_text = MediaInfo.parse(input_file_full_path, output="text", full=False)

        # Write the mediainfo output to the text file
        with open(output_file, 'w', encoding='utf-8', newline='') as file:
            file.write(media_info_text)

        # logger.debug(f"Mediainfo for {input_file_full_path} has been saved to {output_file}")

        return True

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return False
