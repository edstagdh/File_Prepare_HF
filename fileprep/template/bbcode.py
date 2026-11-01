"""Upload template (BBCode) and tags file generation."""
import os
import re
from loguru import logger
from fileprep import paths, runtime
from fileprep.uploaders.service import find_uploaded_url, hosts_for
from fileprep.utils.files import load_json_file
from fileprep.utils.text import CLEAN_CHARS


async def generate_template_video(
        new_title: str,
        scene_title: str,
        studio_info: str,
        scene_pretty_date: str,
        scene_description: str,
        scene_performers: str,
        fps: float,
        resolution_template: str,
        is_vertical: bool,
        codec: str,
        extension: str,
        output_directory: str,
        new_file_full_path: str,
        new_filename_base_name: str,
        template_file_full_path: str,
        code_version: str,
        scene_tags: list,
        studio_tags: list,
        image_output_format: str,
        fill_img_urls: bool,
        suffix: str,
        tpdb_scene_url: str
) -> bool:
    media_info_file_path = os.path.join(output_directory, f"{new_filename_base_name}_mediainfo.txt")

    pattern = f"[{re.escape(CLEAN_CHARS)}]"

    if not os.path.isfile(template_file_full_path):
        raise FileNotFoundError(f"Template file not found: {template_file_full_path}")

    if not os.path.isfile(media_info_file_path):
        raise FileNotFoundError(f"Media info file not found: {media_info_file_path}")

    # Load the template content
    with open(template_file_full_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    # Load media info
    with open(media_info_file_path, "r", encoding="utf-8") as f:
        media_info = f.read()

    if any(v is None for v in (fps, resolution_template, is_vertical, codec)):
        from fileprep.media.encode import get_video_codec
        from fileprep.media.probe import get_video_fps, get_video_resolution_and_orientation
        fps = await get_video_fps(new_file_full_path)
        resolution_template, is_vertical = await get_video_resolution_and_orientation(new_file_full_path)
        codec, self_encoded = await get_video_codec(new_file_full_path)

    resources_img_host_url = runtime.config().processing.outputs.template_icon_base_url.rstrip("/")

    # Load JSON config
    json_map, exit_code = await load_json_file(str(paths.BBCODE_IMAGES_FILE))
    if exit_code != 0 or json_map is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Additional information
    _, template_name = os.path.split(template_file_full_path)
    template_base_name, _ = os.path.splitext(template_name)
    fps_icon_url = f"{resources_img_host_url}{json_map[str(fps)]}"
    resolution_icon_url = f"{resources_img_host_url}{json_map[resolution_template]}"
    codec_icon_url = f"{resources_img_host_url}{json_map[codec]}"
    extension_icon_url = f"{resources_img_host_url}{json_map[extension.replace('.', '')]}"
    desc_button_icon_url = f"{resources_img_host_url}{json_map['desc_button']}"
    release_date_button_icon_url = f"{resources_img_host_url}{json_map['release_date_button']}"
    performers_button_icon_url = f"{resources_img_host_url}{json_map['performers_button']}"
    mediainfo_button_icon_url = f"{resources_img_host_url}{json_map['mediainfo_button']}"
    screens_button_icon_url = f"{resources_img_host_url}{json_map['screens_button']}"
    bg = f"{resources_img_host_url}{json_map['bg']}"
    tpdb_button = f"{resources_img_host_url}{json_map['tpdb_button']}"

    # Default image paths
    cover_image = f"{new_filename_base_name}.{image_output_format}"
    if not os.path.isfile(os.path.join(output_directory, cover_image)):
        cover_image = ""  # no cover (download failed or disabled) - leave the template's cover empty
        logger.warning("No cover image - the template's cover image is left empty.")
    preview_sheet_image = f"{new_filename_base_name}_preview_sheet.webp"  # Hardcoded due to HF supported file hosting requirements(Either WEBP or GIF)
    thumbnails_image = f"{new_filename_base_name}_thumbnails.{image_output_format}"

    # Use uploaded image URLs when available (first host of each asset's ordered host list wins);
    # otherwise the local file names stay in the template.
    if fill_img_urls:
        cover_image = find_uploaded_url(output_directory, new_filename_base_name, "cover", hosts_for("cover")) or cover_image
        thumbnails_image = find_uploaded_url(output_directory, new_filename_base_name, "thumbnails",
                                             hosts_for("thumbnails")) or thumbnails_image
        preview_sheet_image = find_uploaded_url(output_directory, new_filename_base_name, "Preview Sheet WebP",
                                                hosts_for("previews")) or preview_sheet_image

    # Load JSON config
    performers_images, exit_code = await load_json_file(str(paths.PERFORMER_IMAGES_FILE))
    if exit_code != 0 or performers_images is None:
        raise RuntimeError(f"Failed to load JSON config (exit code: {exit_code})")

    # Make a lowercase mapping of the JSON to support case-insensitive lookup
    performers_images_lower = {k.lower(): v for k, v in performers_images.items()}

    all_in_images = False

    if scene_performers:
        translation_table = str.maketrans("", "", "!@#$%^&*()_+='")

        processed_blocks = []
        mapped_names_list = []
        aliases_list = []

        # Clean and normalize names
        cleaned_names = []

        for data in scene_performers:
            performer_name = data[0]
            # Extract alias if it exists inside parentheses
            alias_match = re.search(r"\(([^)]*)\)", performer_name)
            if alias_match:
                alias = alias_match.group(1).strip()
                aliases_list.append(alias)
            else:
                alias = None

            # Remove anything inside parentheses and unwanted characters
            p = re.sub(r"\s*\([^)]*\)", "", performer_name).strip()
            p = p.translate(translation_table)
            cleaned_names.append(p)

        # Case-insensitive check if *all* names exist in performers_images_lower
        all_in_images = all(p.lower() in performers_images_lower for p in cleaned_names)

        # Process each cleaned name
        for full_name in cleaned_names:
            names = full_name.split()
            if len(names) > 1:
                joined = ".".join(names)
                processed_blocks.append(joined)
            else:
                processed_blocks.append(full_name)

            # Add [img] tag if all performers exist in performers_images_lower
            if all_in_images:
                lower_name = full_name.lower()
                if lower_name in performers_images_lower:
                    mapped_names_list.append(f"[img]{performers_images_lower[lower_name]}[/img]")
                else:
                    mapped_names_list.append(full_name)
            else:
                mapped_names_list.append(full_name)

        for alias in aliases_list:
            names = alias.split()
            if len(names) > 1:
                # If the second word starts with "id" followed by digits (case-insensitive)
                if re.match(r"^id\d+$", names[1], re.IGNORECASE):
                    joined = names[0]  # keep only the first word
                else:
                    joined = ".".join(names)
                processed_blocks.append(joined)
            else:
                processed_blocks.append(alias)
    else:
        processed_blocks = []
        mapped_names_list = []

    # Join the processed names (always space-separated)
    processed_string = " ".join(processed_blocks)

    # Join mapped names:
    # - if using [img], separate by a single space
    # - if not using [img], separate by ", "
    if scene_performers:
        if all_in_images:
            # ✅ 3 per row: insert a newline after every 3 images
            mapped_names = ""
            for i, img_tag in enumerate(mapped_names_list, start=1):
                mapped_names += img_tag + " "
                if i % 3 == 0 and i != len(mapped_names_list):
                    mapped_names += "\n"  # new line after every 3
            mapped_names = mapped_names.strip()
        else:
            # Normal comma-separated text for names
            mapped_names = ", ".join(mapped_names_list)
    else:
        mapped_names = ""

    for tag in studio_tags:
        cleaned_tag = tag.replace("'", "")
        cleaned_tag = re.sub(pattern, ".", cleaned_tag)
        processed_string += " " + cleaned_tag
        if cleaned_tag != tag.replace(" ", "") and (cleaned_tag.replace(" ", "")) not in processed_string:
            processed_string += " " + cleaned_tag.replace(" ", "").lower()

    if scene_tags:
        processed_string += " " + " ".join(scene_tags)
    processed_string += f" {fps}fps"
    # logger.debug(resolution_template)
    if resolution_template == "1080p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution_template} FHD"
    elif resolution_template == "2160p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution_template} UHD 4K"
    elif resolution_template == "720p":  # Currently, supports on 2160p/1080p/720p
        processed_string += f" {resolution_template} HD"
    else:
        processed_string += f" {resolution_template}"
    if codec == "hevc":
        processed_string += f" {codec} h265 x265.reencoded"
    else:
        processed_string += f" {codec}"
    # remove consecutive dots
    while ".." in processed_string:
        processed_string = processed_string.replace("..", ".")
    processed_string += f" {extension.replace('.', '')}"
    if suffix != "":
        processed_string += f" {suffix}"
    # Build output filename and path
    tags_filename = f"{new_filename_base_name}_tags.txt"
    tags_path = os.path.join(output_directory, tags_filename)

    # Write the string to the file
    try:
        with open(tags_path, "w", encoding="utf-8") as file:
            file.write(processed_string)
        # logger.debug(f"Tags saved to: {tags_path}")
    except Exception as e:
        logger.error(f"Failed to save tags: {e}")

    def trim_description(desc: str, max_len: int = 600) -> str:
        if len(desc) <= max_len:
            return desc

        trimmed = desc[:max_len]
        last_space = trimmed.rfind(" ")
        if last_space != -1:
            trimmed = trimmed[:last_space]

        return trimmed.rstrip() + "..."

    # Create replacement dictionary
    replacements = {
        "{BG}": bg,
        "{DESC_BUTTON}": desc_button_icon_url,
        "{SCENE_STUDIO}": studio_info,
        "{RELEASE_DATE_BUTTON}": release_date_button_icon_url,
        "{PERFORMERS_BUTTON}": performers_button_icon_url,
        "{MEDIAINFO_BUTTON}": mediainfo_button_icon_url,
        "{SCREENS_BUTTON}": screens_button_icon_url,
        "{NEW_TITLE}": scene_title,
        "{TPDB_LINK}": f"[url={tpdb_scene_url}][img=100]{tpdb_button}[/img][/url]" if tpdb_scene_url != "" else "",
        "{SCENE_PRETTY_DATE}": scene_pretty_date if scene_pretty_date != "" else "N/A",
        "{SCENE_DESCRIPTION}": trim_description(scene_description),
        "{FORMATTED_NAMES}": mapped_names,
        "{FPS}": fps_icon_url,
        "{RESOLUTION}": resolution_icon_url,
        "{IS_VERTICAL}": "yes" if is_vertical else "no",
        "{CODEC}": codec_icon_url,
        "{EXTENSION}": extension_icon_url,
        "{MEDIA_INFO}": media_info,
        "{COVER_IMAGE}": cover_image,
        "{PREVIEW_SHEET}": preview_sheet_image,
        "{STATIC_THUMBNAILS_SHEET}": thumbnails_image,
        "{CODE_VERSION}": code_version
    }
    try:
        # Replace placeholders
        for placeholder, value in replacements.items():
            if placeholder in template_content:
                template_content = template_content.replace(placeholder, value)

        # Make sure the target directory exists
        os.makedirs(output_directory, exist_ok=True)

        # build template filename
        output_filename = f"{new_filename_base_name}_template.txt"

        # Save the modified file
        output_path = os.path.join(output_directory, output_filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(template_content)
            # logger.debug(f"Template saved to: {tags_path}")
    except Exception as e:
        logger.error(f"An error has occured during creating of template for file: {new_filename_base_name}.{extension}")
        return False

    return True
