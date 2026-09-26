"""
Public entry points for the TPDB_API package. Re-exported here so existing
callers doing ``from TPDB_API import query_api``,
``from TPDB_API import ensure_scene_collected``, or
``from TPDB_API import get_performer_profile_picture`` keep working
unchanged after the split from the old single-file module.
"""

from .collection import ensure_scene_collected
from .performers import get_performer_profile_picture
from .query_api import query_api

__all__ = ["query_api", "ensure_scene_collected", "get_performer_profile_picture"]
