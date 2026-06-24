"""Shared pytest fixtures.

Each test gets a fresh, fully-simulated environment: a throwaway SQLite
database, a fixed JWT secret, forced HAL simulation, and a sandbox
directory as the only allowlisted file root. Per-test isolation
prevents the session-shared state that previously caused flakes
(e.g. the ota_history tiebreak test failing because earlier test runs
left v-test-N rows in the table — #141).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Each test allocates a fresh tempdir + sandbox + config.yaml. The
# admin (admin/admin) is seeded on first init by ``init_db`` /
# ``seed_default_admin`` so the role matrix tests still work out of
# the box.
_KEEP_TMP = os.environ.get("DASH_TEST_KEEP_TMP", "").lower() in {"1", "true", "yes"}


def _make_test_environment() -> tuple[Path, Path, Path]:
    """Allocate a fresh tempdir + sandbox + config.yaml for one test.

    Returns ``(tmp_root, sandbox, config_path)``. The DB is created
    lazily by the app's ``init_db`` when ``create_app()`` runs.
    """
    tmp = Path(tempfile.mkdtemp(prefix="dash-test-"))
    sandbox = tmp / "sandbox"
    sandbox.mkdir()
    (sandbox / "hello.txt").write_text("hello world\n")
    config = tmp / "config.yaml"
    config.write_text(
        f"""
server:
  host: 127.0.0.1
  port: 8123
auth:
  jwt_secret: "test-secret-fixed-for-deterministic-tokens-0123456789"
  max_failed_logins: 5
  lockout_minutes: 15
database:
  path: "{tmp / 'test.db'}"
files:
  allowed_paths:
    - "{sandbox}"
simulation:
  force: true
"""
    )
    return tmp, sandbox, config


def _teardown_test_environment(tmp: Path) -> None:
    """Remove the tempdir unless DASH_TEST_KEEP_TMP is set (for debug)."""
    if _KEEP_TMP:
        return
    shutil.rmtree(tmp, ignore_errors=True)


# Module-level state bag — the simplest way to share the per-test
# tempdir between the function-scoped ``sandbox`` and ``client``
# fixtures without re-architecting the suite. Each test starts with
# an empty bag, and we tear down on fixture finalization.
_test_env_state: dict[str, Path] = {}


@pytest.fixture
def client() -> Iterator[object]:
    """A TestClient with the app's lifespan run (DB init + admin seeded).

    Per-test scoped (#141): every test gets a fresh DB so rows from a
    prior test can't leak in. ``client`` always owns the tempdir; tests
    that also need the file-safety sandbox should take the ``sandbox``
    fixture, which reuses the same tempdir (a single env per test).
    """
    from fastapi.testclient import TestClient

    _tmp, sb, config = _make_test_environment()
    _test_env_state["tmp"] = _tmp
    _test_env_state["sandbox"] = sb
    _test_env_state["config"] = config
    os.environ["DASHBOARD_CONFIG"] = str(config)

    # Reset any module-level state that the app caches on import.
    # The HAL registry and settings are both ``lru_cache``d — clearing
    # the caches makes the next call rebuild against the fresh
    # DASHBOARD_CONFIG and the freshly-detected hardware (sim mode
    # here), so each test gets a clean, deterministic state (#141).
    from app.config import get_settings
    from app.hal.registry import registry as hal_registry

    get_settings.cache_clear()  # type: ignore[attr-defined]
    hal_registry.cache_clear()  # type: ignore[attr-defined]

    from app.main import create_app

    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        _teardown_test_environment(_tmp)
        _test_env_state.clear()


@pytest.fixture
def sandbox(client: object) -> Path:  # noqa: ARG001 — client fixture creates the env
    """The single allowlisted file root for path-safety tests.

    Depends on ``client`` so the sandbox lives in the same tempdir as
    the TestClient's DB — otherwise the app's ``allowed_paths`` and the
    test's ``sandbox`` parameter would point at different directories
    (#141).
    """
    sb = _test_env_state.get("sandbox")
    if sb is None:  # pragma: no cover - client fixture always sets it
        raise RuntimeError("client fixture did not create a sandbox")
    return sb


def _login(client: object, username: str, password: str) -> str:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


@pytest.fixture
def tokens(client: object) -> dict[str, str]:
    """Return a JWT for each role, creating operator/viewer users via the API.

    Per-test (#141) so each test starts with a clean users table —
    a previous test's lockout or disable doesn't affect the next.
    """
    admin = _login(client, "admin", "admin")
    headers = {"Authorization": f"Bearer {admin}"}
    for role in ("operator", "viewer"):
        client.post(  # type: ignore[attr-defined]
            "/api/users",
            json={"username": role, "password": f"{role}-pw", "role": role},
            headers=headers,
        )
    return {
        "admin": admin,
        "operator": _login(client, "operator", "operator-pw"),
        "viewer": _login(client, "viewer", "viewer-pw"),
    }


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
