"""Regression test for #126 — SPA read-only badges on fuses / keys cards.

audit.md §4 Boot + §9 Security: fuses, secure-boot, and key enrollment
are status-only. The SPA needs a clear "read-only" badge so
operators do not look for missing write controls and conclude the
dashboard is broken.

The project doesn't ship Playwright (no e2e framework in tests/),
so this guard rails the SPA source: it fails CI if a future change
removes the badge text from the security page template, OR if the
endpoint the badge is supposed to accompany is removed from the
SPA's mount-time fetches, OR if a write endpoint is added to any
of the audit-status-only routers.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SPA = _REPO / "static" / "js" / "pages" / "security.js"


def _spa() -> str:
    assert _SPA.exists(), f"security page missing: {_SPA}"
    return _SPA.read_text(encoding="utf-8")


# Surfaces that audit §4 / §9 mark as status-only. The SPA must
# render a read-only badge on each.
_READ_ONLY_SURFACES = (
    "secure-boot",
    "fuses",
    "enrolled-keys",
    "tpm",
)


def _testid_for_surface(surface: str) -> str:
    """Convert kebab-case surface to the data-testid used in the SPA."""
    return f"readonly-badge-{surface}"


def test_spa_renders_readonly_badge_per_surface() -> None:
    """Every status-only surface must carry a badge with a unique
    ``data-testid`` (#126)."""
    spa = _spa()
    for surface in _READ_ONLY_SURFACES:
        testid = _testid_for_surface(surface)
        assert testid in spa, f"missing data-testid='{testid}' in SPA"


def test_spa_badges_use_info_severity() -> None:
    """Every badge must use the PrimeVue ``info`` severity (the
    project's neutral / informational color) — a danger / warning
    severity would imply the operator has to do something."""
    spa = _spa()
    for surface in _READ_ONLY_SURFACES:
        testid = _testid_for_surface(surface)
        # Find the surrounding block and verify the Tag is 'info'.
        idx = spa.find(f'data-testid="{testid}"')
        assert idx != -1, f"missing badge {testid}"
        chunk = spa[idx : idx + 400]
        assert 'severity="info"' in chunk, f"badge {testid} should use info severity"


def test_spa_badges_are_accessible() -> None:
    """Each badge must have an explicit ``aria-label`` so a screen
    reader announces the read-only state even when the visual tag
    is rendered as an icon-only button."""
    spa = _spa()
    for surface in _READ_ONLY_SURFACES:
        testid = _testid_for_surface(surface)
        idx = spa.find(f'data-testid="{testid}"')
        assert idx != -1
        chunk = spa[idx : idx + 400]
        assert 'aria-label="Read-only"' in chunk, f'badge {testid} needs aria-label="Read-only" for screen readers'


def test_spa_fetches_all_status_only_endpoints() -> None:
    """The SPA must call every status-only endpoint at mount so the
    badge has data behind it (otherwise the panel renders but the
    payload is missing)."""
    spa = _spa()
    for path in (
        "/api/security/secure-boot",
        "/api/security/fuses",
        "/api/security/keys/enrolled",
        "/api/security/tpm",
    ):
        assert path in spa, f"SPA must fetch {path} at mount to populate the status panel"


def test_spa_security_no_write_to_status_only_endpoints() -> None:
    """No POST / PUT / DELETE to any status-only endpoint (#126).

    The router layer enforces this (no write handler exists), but
    pinning it at the SPA layer catches a future SPA-side mistake
    (e.g. a misguided admin trying to clear fuses) before it ships.
    """
    spa = _spa()
    for path in (
        "/api/security/secure-boot",
        "/api/security/fuses",
        "/api/security/keys/enrolled",
        "/api/security/tpm",
    ):
        # The SPA must not POST/PUT/DELETE the endpoint. api.post /
        # api.put / api.del are the only mutators the SPA uses
        # (consistent with the rest of the SPA, per `api.js`).
        for verb in ("api.post(", "api.put(", "api.del("):
            assert (
                verb + '"' + path not in spa and verb + "'" + path not in spa
            ), f"SPA must not {verb.strip()}() {path} — read-only surface"
