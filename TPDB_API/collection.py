"""
Checking whether a scene is already in the user's collection, and adding it
if not.
"""

import httpx
from loguru import logger

from Utilities import load_credentials


async def _request(method: str, url: str, token: str) -> dict | None:
    """
    Minimal authorized JSON request helper local to the collection check.

    Args:
        method: HTTP method, e.g. "GET" or "POST".
        url: Full request URL.
        token: Bearer token for the "Authorization" header.

    Returns:
        The parsed JSON body on a 200/201 response, or None on failure.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(method=method, url=url, headers=headers, timeout=60)

        if response.status_code not in (200, 201):
            logger.error(f"{method} request failed [{response.status_code}]: {response.text}")
            return None

        return response.json()

    except Exception:
        logger.exception(f"{method} request failed")
        return None


async def ensure_scene_collected(scene_id: str, jav_api_mode: bool, movies_api_mode: bool) -> bool:
    """
    Check whether a scene is already in the collection, adding it if not.

    Args:
        scene_id: The scene/movie/JAV-scene ID to check and add.
        jav_api_mode: If True, use the JAV collection endpoint.
        movies_api_mode: If True (and not ``jav_api_mode``), use the Movie
            collection endpoint. Otherwise the generic Scene endpoint is
            used.

    Returns:
        True if the scene was already collected or was successfully added,
        False on any failure.
    """
    if not scene_id:
        logger.error("Scene ID is required.")
        return False

    try:
        api_auth, api_url, _ = await load_credentials(mode=8)
        if not api_auth or not api_url:
            logger.error("API auth token or URL missing (mode 8).")
            return False

        if jav_api_mode:
            url = f"{api_url}?scene_id={scene_id}&type=JAV"
        elif movies_api_mode:
            url = f"{api_url}?scene_id={scene_id}&type=Movie"
        else:
            url = f"{api_url}?scene_id={scene_id}&type=Scene"

        # 1. Check whether it's already collected.
        check_response = await _request("GET", url, api_auth)
        if not isinstance(check_response, dict) or "value" not in check_response:
            logger.error(f"Invalid check response for scene {scene_id}: {check_response}")
            return False

        if check_response["value"] is True:
            logger.warning(f"Scene {scene_id} already collected.")
            return True

        # 2. Add it.
        add_response = await _request("POST", url, api_auth)
        if add_response is not None:
            if movies_api_mode:
                logger.info("Movie added to collection.")
            elif jav_api_mode:
                logger.info("JAV Scene added to collection.")
            else:
                logger.info("Scene added to collection.")
            return True

        logger.error(f"Failed to add scene {scene_id} to collection.")
        return False

    except Exception:
        logger.exception("Unexpected error while ensuring scene is collected.")
        return False
