"""Runtime guard for mutually exclusive PWF distributions."""

from __future__ import annotations

from importlib import metadata


COMMUNITY_DISTRIBUTION = "planning-with-files-governed"
INTERNAL_DISTRIBUTION = "planning-with-files-governed-internal"


class EditionConflictError(RuntimeError):
    """Raised before business execution in an invalid co-installation."""

    code = "PWF_EDITION_CONFLICT"


def installed_editions() -> dict[str, str]:
    result: dict[str, str] = {}
    for distribution_name in (COMMUNITY_DISTRIBUTION, INTERNAL_DISTRIBUTION):
        try:
            result[distribution_name] = metadata.version(distribution_name)
        except metadata.PackageNotFoundError:
            continue
    return result


def assert_runtime_edition_compatible() -> None:
    installed = installed_editions()
    if COMMUNITY_DISTRIBUTION in installed and INTERNAL_DISTRIBUTION in installed:
        raise EditionConflictError(
            "PWF_EDITION_CONFLICT: Community and Internal distributions are both installed; "
            "rebuild a single-edition virtual environment"
        )


__all__ = [
    "COMMUNITY_DISTRIBUTION",
    "INTERNAL_DISTRIBUTION",
    "EditionConflictError",
    "assert_runtime_edition_compatible",
    "installed_editions",
]
