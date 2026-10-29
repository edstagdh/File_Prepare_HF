"""
Low-level HTTP access to the scene/performer/site API, including retry
handling. No business logic lives here - just "make the request, retry on
transient failure".
"""

import asyncio
import json
from typing import Optional

import httpx
from loguru import logger

from .prompt_utils import prompt

REQUEST_TIMEOUT = httpx.Timeout(60.0)  # 60s for connect, read, write, and pool


async def send_request(
        api_url: str,
        api_auth: str,
        query_string: str,
        max_retries: int,
        delay: int,
        mode: str = "parse",
) -> Optional[dict]:
    """
    GET a resource from the API, retrying automatically on 5xx responses
    and offering the user a manual retry cycle once ``max_retries`` is
    exhausted.

    Args:
        api_url: Base API URL. If "performers" appears in it,
            ``query_string`` is appended directly; otherwise the URL is
            built according to ``mode``.
        api_auth: Bearer token for the "Authorization" header.
        query_string: The id / oshash / free-text parse string to query for.
        max_retries: Number of automatic retry attempts per retry cycle.
        delay: Seconds to wait between automatic retries.
        mode: "parse" (default, free-text search), "id" (direct lookup by
            id), or "oshash" (lookup by file hash).

    Returns:
        The parsed JSON response (with "data" normalized to a list for
        oshash lookups), or None if the request has no "data" key, fails
        with a non-retryable error, or the user declines to retry further
        after exhausting ``max_retries``.
    """
    if "performers" in api_url:
        url = f"{api_url}{query_string}"
    elif mode == "id":
        url = f"{api_url}/{query_string}"
    elif mode == "oshash":
        url = f"{api_url}/hash/{query_string}?type=OSHASH"
    else:
        url = f"{api_url}?orderBy=recently_released&parse={query_string}&per_page=40&page=1"

    headers = {"accept": "application/json", "Authorization": f"Bearer {api_auth}"}

    while True:  # allows user-triggered retry cycles once max_retries is exhausted
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.get(url, headers=headers)

                # Retry only on 5xx; anything else falls through to raise_for_status.
                if 500 <= response.status_code < 600:
                    raise httpx.HTTPStatusError(
                        f"Server error {response.status_code}", request=response.request, response=response
                    )

                response.raise_for_status()
                response_data = response.json()

                if attempt > 1:
                    logger.info("Retry successful!")

                if "data" not in response_data:
                    return None

                # oshash returns a single object; normalize to a list like the other modes.
                if mode == "oshash" and isinstance(response_data["data"], dict):
                    response_data["data"] = [response_data["data"]]

                return response_data
            except httpx.TimeoutException as e:
                logger.error(
                    f"Attempt {attempt}/{max_retries} timed out: "
                    f"{str(e).replace(api_url, '**REDACTED**')}"
                )
                if attempt < max_retries:
                    logger.warning(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error("Maximum retries reached.")

            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response else "N/A"
                logger.error(
                    f"Attempt {attempt}/{max_retries} failed (HTTP {status}): "
                    f"{str(e).replace(api_url, '**REDACTED**')}"
                )
                if attempt < max_retries:
                    logger.warning(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error("Maximum retries reached.")

            except httpx.RequestError as e:
                # Non-5xx errors are not retried automatically.
                logger.error(f"Non-retryable error: {str(e).replace(api_url, '**REDACTED**')}")
                return None

        # All automatic attempts in this cycle failed with 5xx - ask the user.
        await asyncio.sleep(0.5)
        user_input = (await prompt("Retry again? (y/n): ")).strip().lower()
        if user_input == "y":
            logger.info("User chose to retry again.")
            continue
        logger.warning("User chose to continue without retrying.")
        return None


async def fetch_api_site_data(
        api_url: str,
        api_auth: str,
        site_parent: str,
        max_retries: int,
        delay: int,
        debug: bool = False,
) -> Optional[str]:
    """
    Walk a site's parent hierarchy up to the top-level parent and return
    its name.

    Args:
        api_url: Base URL for the sites API.
        api_auth: Bearer token for the "Authorization" header.
        site_parent: Initial parent UUID to start the traversal from.
        max_retries: Maximum number of retry attempts.
        delay: Delay between retries, in seconds.
        debug: If True, log each hop's raw JSON payload.

    Returns:
        The name of the top-level parent site, or None on failure.

    Note:
        Preserved from the original implementation: on a request error with
        retries remaining, this logs "Retrying..." but then returns None
        immediately rather than actually looping again - it never performs
        more than one attempt in practice. Flagging this in case it should
        be a real retry loop instead.
    """
    url = f"{api_url}{site_parent}"
    headers = {"accept": "application/json", "Authorization": f"Bearer {api_auth}"}

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            response_data = response.json()

            if "data" not in response_data:
                logger.error("Data key not found in the response.")
                return None

            if attempt > 0:
                logger.info("Retry successful!")

            # Walk up the parent chain until there's no parent left.
            while site_parent:
                if debug:
                    logger.info(f"Fetching data for site: {site_parent}")
                    logger.debug(json.dumps(response_data, indent=4))

                top_parent = response_data["data"].get("name", "Unknown")
                next_parent = response_data["data"].get("parent")

                if next_parent is None:
                    return top_parent

                site_parent = next_parent["uuid"]
                url = f"{api_url}{site_parent}"
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, headers=headers)
                response.raise_for_status()
                response_data = response.json()

            return None

        except httpx.RequestError as e:
            logger.error(f"Attempt {attempt + 1} failed: {str(e).replace(api_url, '**REDACTED**')}")
            if attempt < max_retries - 1:
                logger.warning(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error("Maximum retries reached. Request failed.")
            return None

    return None
