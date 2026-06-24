"""Path-traversal-safe filesystem access.

Hard security boundary for the file manager and log viewer. Every user-supplied
path is canonicalized (symlinks resolved) and verified to live under one of the
configured allowlisted roots. This module is unit-tested against ``../``,
encoded ``..%2f``, absolute escapes and symlink escapes.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from app.config import get_settings
from app.errors import ApiError


def allowed_roots() -> list[Path]:
    return [Path(p).resolve() for p in get_settings().files.allowed_paths]


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_resolve(raw: str) -> Path:
    """Resolve ``raw`` and ensure it is inside an allowlisted root.

    Raises :class:`ApiError` (403) on any attempt to escape the allowlist.
    """
    # Decode percent-encoding (handles ..%2f and friends) before resolving.
    decoded = urllib.parse.unquote(raw)
    # Reject NUL bytes outright.
    if "\x00" in decoded:
        raise ApiError("invalid_path", "Invalid path", 400)

    candidate = Path(decoded)
    # os.path.realpath resolves symlinks and ".." components against the FS.
    resolved = Path(os.path.realpath(candidate))

    roots = allowed_roots()
    if any(_is_within(resolved, root) or resolved == root for root in roots):
        return resolved
    raise ApiError(
        "path_forbidden",
        "Path is outside the allowed directories",
        403,
        detail={"path": decoded},
    )
