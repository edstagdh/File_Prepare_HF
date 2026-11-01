"""
Cleaning up site names for scenes cross-posted through FansDB, and
resolving a site's ultimate parent/owner network.
"""

from typing import Optional, Tuple

from .api_client import fetch_api_site_data


def normalize_site_name(scene_data: dict) -> Tuple[str, Optional[dict]]:
    """
    Rewrite FansDB-prefixed cross-posted site names (OnlyFans/ManyVids/
    Fansly) into a cleaner "<Network>-<name>" form, and prefix ManyVids
    sub-sites with "Manyvids: " when their parent site is ManyVids.

    Args:
        scene_data: Scene API data with a "site" dict containing "name" and
            optionally "parent".

    Returns:
        A ``(site_name, site_parent)`` tuple - the cleaned site name, and
        the raw parent dict (or None) for further lookups.
    """
    site = scene_data.get("site", {}).get("name")

    if "onlyfans" in site.lower() and "fansdb" in site.lower():
        site = site.replace("FansDB: ", "").replace(" (onlyfans)", "")
        site = f"OnlyFans-{site}"
    if "manyvids" in site.lower() and "fansdb" in site.lower():
        site = site.replace("FansDB: ", "").replace(" (manyvids)", "")
        site = f"Manyvids-{site}"
    if "fansly" in site.lower() and "fansdb" in site.lower():
        site = site.replace("FansDB: ", "").replace(" (fansly)", "")
        site = f"Fansly-{site}"

    site_parent = scene_data.get("site", {}).get("parent")
    if site_parent and site_parent.get("name") == "ManyVids" and "Manyvids:" not in site:
        site = f"Manyvids: {site}"

    return site, site_parent


async def resolve_site_owner(
        site: str,
        site_parent: Optional[dict],
        api_sites_url: str,
        api_auth: str,
        max_retries: int,
        delay: int,
) -> Optional[str]:
    """
    Determine the top-level owner network for a site, suppressing it when
    it's redundant with the (already-prefixed) site name itself.

    Args:
        site: The (already-normalized) site name.
        site_parent: The site's parent dict, or None if it has no parent.
        api_sites_url: Base URL for the sites API.
        api_auth: Bearer token for the sites API.
        max_retries: Retry attempts forwarded to ``fetch_api_site_data``.
        delay: Retry delay forwarded to ``fetch_api_site_data``.

    Returns:
        The owner network name, or None if there's no parent, the lookup
        fails, or the owner name is already embedded in ``site`` for an
        OnlyFans/ManyVids/Fansly site.
    """
    if site_parent:
        site_parent_uuid = site_parent.get("uuid")
        site_owner = await fetch_api_site_data(api_sites_url, api_auth, site_parent_uuid, max_retries, delay)
    else:
        site_owner = site

    if (
            any(x in site.lower() for x in ("onlyfans", "manyvids", "fansly"))
            and site_owner
            and site_owner.lower() in site.lower()
    ):
        return None

    return site_owner
