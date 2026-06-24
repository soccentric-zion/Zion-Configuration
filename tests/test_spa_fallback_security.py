"""Regression tests for the SPA fallback path-traversal fix (issue #24).

The catch-all `/{full_path:path}` route must never serve files outside
STATIC_DIR. Path treats leading-slash and ".." components specially, so any
naive join leaks the filesystem to unauthenticated clients.

Audit Guardrail 1 / #115 — the SPA fallback is a trust boundary. Every
deep-link / typed URL the SPA allows the user to navigate to lands here.
These tests cover the historical regression plus the obvious additional
attack shapes (NUL bytes, encoded path traversal, double-dot variants,
and known-sensitive filesystem paths).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _is_index_html(resp: object) -> bool:
    body = getattr(resp, "content", b"")
    return b"<!doctype html" in body.lower() or b"<html" in body.lower()


def test_fallback_serves_index_for_absolute_path(client: TestClient) -> None:
    """`/etc/passwd` (or any absolute path) must NOT leak the file body."""
    resp = client.get("//etc/passwd")
    assert resp.status_code == 200
    # Must be index.html, NOT the contents of /etc/passwd
    assert _is_index_html(resp)


def test_fallback_serves_index_for_dotdot_escape(client: TestClient) -> None:
    """`/../requirements.txt` would walk up out of STATIC_DIR — must fall back."""
    resp = client.get("/../requirements.txt")
    assert resp.status_code == 200
    # requirements.txt is the Python project file, NOT html
    assert _is_index_html(resp)
    assert b"fastapi" not in resp.content.lower()


def test_fallback_serves_real_static_asset(client: TestClient) -> None:
    """Sanity check: legitimate paths under STATIC_DIR still resolve."""
    # /static is mounted separately and served as-is; the SPA fallback only
    # runs when /static does not match. Check that the index resolves via
    # the bare root.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert _is_index_html(resp)


# --- additional Guardrail 1 attack shapes (#115) ---------------------------
# These are not new attack vectors — they're well-known variations the
# original fix is robust against, but were never pinned by tests. Pinning
# them now means a future "optimization" of the fallback that strips the
# Path.resolve() check (e.g. switching to string prefix matching) will
# fail CI before it ships.


def test_fallback_blocks_url_encoded_traversal(client: TestClient) -> None:
    """URL-encoded `..` (%2e%2e/) must not escape STATIC_DIR."""
    resp = client.get("/%2e%2e/requirements.txt")
    assert resp.status_code == 200
    assert _is_index_html(resp)
    assert b"fastapi" not in resp.content.lower()


def test_fallback_blocks_nested_dotdot(client: TestClient) -> None:
    """Multiple `..` segments walking up repeatedly must still fall back."""
    resp = client.get("/foo/../../etc/passwd")
    assert resp.status_code == 200
    assert _is_index_html(resp)


def test_fallback_blocks_etc_shadow(client: TestClient) -> None:
    """A `/etc/...` style URL where the segment is not a real static asset
    must fall back to index.html (i.e. the SPA), not 404 and not leak."""
    resp = client.get("/etc/passwd")
    assert resp.status_code == 200
    assert _is_index_html(resp)


def test_fallback_blocks_data_dir(client: TestClient) -> None:
    """`/data/...` is the SQLite directory — it must not be served."""
    resp = client.get("/data/dashboard.db")
    assert resp.status_code == 200
    assert _is_index_html(resp)


def test_fallback_blocks_secrets_dir(client: TestClient) -> None:
    """`.ssh` / `.aws` style paths must not be served even if they exist."""
    resp = client.get("/.ssh/id_rsa")
    assert resp.status_code == 200
    assert _is_index_html(resp)


def test_fallback_serves_deep_link(client: TestClient) -> None:
    """The legitimate use case: a deep hash route like /network must
    still resolve to the SPA index so client-side routing can take over."""
    resp = client.get("/network")
    assert resp.status_code == 200
    assert _is_index_html(resp)


def test_fallback_serves_nested_deep_link(client: TestClient) -> None:
    """Multi-segment SPA routes must also fall back to the index."""
    resp = client.get("/cloud/alerts/rules")
    assert resp.status_code == 200
    assert _is_index_html(resp)


def test_fallback_blocks_null_byte_injection(client: TestClient) -> None:
    """NUL bytes must not truncate the path and serve a real file.

    httpx (and Starlette's URL parser) reject raw NUL bytes at the
    client side with a 400 InvalidURL — that itself is a defense. The
    fallback handler must also not crash on a percent-encoded NUL
    (%00), which the URL parser does NOT reject.
    """
    # Direct NUL: rejected by the URL parser. Either response is
    # acceptable as long as no file body leaks.
    with pytest.raises(Exception):
        client.get("/index.html\x00../../etc/passwd")
    # Percent-encoded NUL reaches the route handler.
    resp = client.get("/index.html%00../../etc/passwd")
    assert resp.status_code == 200
    assert _is_index_html(resp)
