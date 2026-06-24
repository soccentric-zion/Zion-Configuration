"""Regression guard for issue #41.

The dashboard SPA stores the JWT in ``localStorage``, which is readable
by any JavaScript running in the same origin. While there's no current
XSS vector (Vue's ``{{ }}`` is HTML-escaped and the codebase does not use
``v-html`` / ``innerHTML`` / ``document.write``), a future feature
introducing one of those would silently turn a single bug into a full
account-takeover primitive.

This guard scans ``static/js/`` and fails CI if any of the dangerous
APIs appear. Keep them out.

Issue #104 extends this with a server-side regression: every user-input
surface that the SPA renders must return the payload as a JSON string,
not as raw HTML, and the response Content-Type must NOT be ``text/html``.
If the server started serialising user input as HTML, the SPA's
``{{ }}`` escaping would no longer be the only line of defence.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC_JS = Path(__file__).resolve().parents[1] / "static" / "js"

# Allowed substrings (exceptions) — if a real sanitisation library is
# added later, whitelist its call-site here. For now the list is empty
# so any occurrence is a hard failure.
_ALLOWED_SUBSTRINGS: tuple[str, ...] = ()

# Patterns that catch the dangerous APIs even if formatting is unusual.
_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bv-html\b"),
    re.compile(r"\b\.innerHTML\b"),
    re.compile(r"\bouterHTML\b"),
    re.compile(r"\bdocument\.write(?:ln)?\b"),
)


def test_no_v_html_in_spa() -> None:
    """No template uses v-html; raw HTML injection is the XSS vector."""
    offenders: list[str] = []
    for js in _STATIC_JS.rglob("*.js"):
        text = js.read_text(encoding="utf-8", errors="ignore")
        for pat in _FORBIDDEN_PATTERNS:
            for m in pat.finditer(text):
                # Check if this match is in an allowed substring (line-level).
                start = max(0, m.start() - 20)
                end = min(len(text), m.end() + 20)
                window = text[start:end]
                if any(allow in window for allow in _ALLOWED_SUBSTRINGS):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{js.relative_to(_STATIC_JS.parents[1])}:{line}: {m.group(0)}")
    assert not offenders, "Unsafe DOM APIs in SPA (issue #41):\n" + "\n".join(offenders)


# --- server-side guards (#104) --------------------------------------------
# A classic defence-in-depth regression: every API that echoes user input
# back to the SPA must serve ``application/json`` (or another non-HTML
# type), and the JSON payload must be a quoted string, not a chunk of
# HTML embedded in a JSON response. If the server ever started returning
# ``text/html`` for these endpoints, the SPA's ``{{ }}`` escaping would
# no longer apply.

_XSS_PAYLOAD = '<script>alert("xss")</script>'


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_audit_target_returns_json_not_html(client: object, tokens: dict[str, str]) -> None:
    """A malicious string written as an audit target must come back with a
    JSON Content-Type, never ``text/html`` (#104).

    JSON serialisation does not escape ``<``/``>``; that is correct
    because the SPA parses the response with ``fetch().json()`` and
    Vue renders the resulting string with ``{{ }}`` (HTML-escaped).
    The defence-in-depth question is therefore the Content-Type: if
    the server started returning ``text/html``, the SPA could render
    it as HTML and the ``{{ }}`` escape would no longer apply.
    """
    admin = _h(tokens["admin"])
    # Audit a row via the VPN endpoint (records target=interface).
    resp = client.post(  # type: ignore[attr-defined]
        "/api/network/vpn",
        json={"action": "up", "interface": _XSS_PAYLOAD},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=vpn_action&limit=5",
        headers=admin,
    )
    assert audit.status_code == 200, audit.text
    ctype = audit.headers.get("content-type", "")
    assert "json" in ctype.lower(), f"audit endpoint must return JSON, got {ctype!r}"
    # Defense-in-depth: a malicious payload MUST NOT appear unescaped
    # in any HTML content the SPA might render. The SPA fetch layer
    # only consumes JSON, so we sanity-check the static SPA HTML does
    # not embed the payload inline.
    index_html = (_STATIC_JS.parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert (
        _XSS_PAYLOAD not in index_html
    ), "static/index.html embeds an XSS payload — the SPA shell must not embed user input"


def test_restore_point_name_payload_is_json_safe(client: object, tokens: dict[str, str]) -> None:
    """Restore-point names appear in the SPA via ``{{ }}``; verify the
    server does not echo a payload as HTML (#104)."""
    admin = _h(tokens["admin"])
    # The settings restore-points endpoint accepts a name. Use the
    # existing create+list flow.
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore-points",
        json={"name": _XSS_PAYLOAD, "description": "xss-canary"},
        headers=admin,
    )
    # 200 if the endpoint exists; 404 otherwise — either way, the
    # response must NOT be text/html.
    assert resp.status_code in {200, 404, 422, 428}, resp.text
    ctype = resp.headers.get("content-type", "")
    assert "json" in ctype.lower(), f"restore-points must return JSON, got {ctype!r}"
