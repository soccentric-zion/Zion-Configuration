"""Regression guard for #119 — SPA has a global error boundary.

Without a top-level error handler, an uncaught error in a component's
setup()/render() leaves the SPA in a half-mounted state with no UI for
the user. Vue 3's equivalent of an error boundary is
``app.config.errorHandler``; this guard fails CI if it is removed.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC_JS = Path(__file__).resolve().parents[1] / "static" / "js"
_APP_JS = _STATIC_JS / "app.js"


def test_app_js_sets_error_handler() -> None:
    """``createApp(...)`` must be followed by ``app.config.errorHandler = ...``."""
    text = _APP_JS.read_text()
    # Match `app.config.errorHandler = <expr>;` and assert the RHS is
    # not a no-op like `() => {}` or `null`.
    m = re.search(r"app\.config\.errorHandler\s*=\s*([^;]+);", text)
    assert m is not None, (
        "app.config.errorHandler is not set in static/js/app.js — "
        "uncaught component errors will leave the SPA in a broken state (#119)"
    )
    rhs = m.group(1).strip()
    # Reject obvious no-op assignments.
    assert rhs not in {
        "() => {}",
        "null",
        "undefined",
        "false",
        "() => null",
    }, f"app.config.errorHandler is a no-op: {rhs!r} (#119)"
    # The handler must render *something* for the user — a banner,
    # toast, or alert. (We don't pin a specific element; the regex
    # catches the common Vue/vanilla fallbacks.)
    assert re.search(
        r"(role\s*=\s*['\"]alert['\"]|toast|createElement|prepend|append\(|textContent)",
        text,
    ), "errorHandler does not render any user-visible feedback (#119)"


def test_error_handler_logs_to_console() -> None:
    """Console logging is the developer-facing half of the boundary."""
    text = _APP_JS.read_text()
    m = re.search(r"app\.config\.errorHandler\s*=\s*([^;]+);", text)
    assert m is not None
    body = m.group(1)
    assert "console" in body, (
        "errorHandler does not log the error to console — debugging " "from a user's report becomes much harder (#119)"
    )
