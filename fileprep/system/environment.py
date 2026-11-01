"""Python runtime checks."""

import sys


def python_version_supported(min_version: list[int], max_version: list[int]) -> bool:
    current = sys.version_info[:2]
    return tuple(min_version[:2]) <= current <= tuple(max_version[:2])


def python_version() -> str:
    return sys.version.split()[0]
