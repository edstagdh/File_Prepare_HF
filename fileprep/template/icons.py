"""Select the icon set of an upload template from Resources/BBCode_Images.json."""
import re
from pathlib import PurePath, PureWindowsPath

from loguru import logger


def template_theme(template_file: str) -> str:
    """The name after `_Template_` in the file name: HF_Template_Reel.txt -> Reel, HF_Template.txt -> ''."""
    stem = PureWindowsPath(PurePath(template_file)).stem
    match = re.search(r"_Template_(.+)$", stem, re.IGNORECASE)
    return match.group(1) if match else ""


def icons_for_template(icons: dict[str, str], template_file: str) -> dict[str, str]:
    """
    The icon map for a template: `<Theme>_<key>` entries (case-insensitive) replace `<key>`.
    Keys the theme does not define keep the base image; a base template uses the map as it is.
    """
    theme = template_theme(template_file)
    if not theme:
        return icons
    prefix = f"{theme}_".casefold()
    themed = {key[len(prefix):]: value for key, value in icons.items() if key.casefold().startswith(prefix)}
    if not themed:
        logger.warning(f"No '{theme}_' icons in BBCode_Images.json - {template_file} uses the base icons.")
    return {**icons, **themed}
