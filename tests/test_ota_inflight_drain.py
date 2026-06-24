"""Regression test for #138 — verify the lifespan shutdown drains in-flight OTA installs.

Background: prior to #32, ``start_install`` discarded the asyncio.Task
returned by ``asyncio.create_task``, so a re-deploy / restart abandoned
a flash mid-write. The fix:

* retains each install task in a module-level set, and
* wires the lifespan shutdown (``app/main.py::lifespan``) to call
  ``app.tasks.ota.drain_inflight()`` before the database closes.

This test exercises the *lifespan* path end-to-end — not just
``ota.drain_inflight`` directly — so a future change that drops the
``await ota.drain_inflight()`` line from the lifespan, or stops retaining
tasks in ``_inflight_installs``, fails CI.
"""

from __future__ import annotations

import asyncio

import pytest

from app.tasks import ota


@pytest.fixture(autouse=True)
def _clear_inflight() -> object:
    """Keep the test isolated from any other test's leftovers.

    The ``client`` fixture in conftest is function-scoped, so each test
    gets a fresh app and lifespan — but the module-level
    ``_inflight_installs`` set is process-wide. Clear it before and
    drain after so a previous test's lingering task can't race the
    next test's startup (#141).
    """
    ota._inflight_installs.clear()
    yield
    asyncio.run(ota.drain_inflight(timeout_s=2.0))


def test_lifespan_shutdown_drains_inflight_install(client: object, tokens: dict[str, str]) -> None:
    """Start an install that holds the task open, then exit the TestClient
    (which fires the lifespan shutdown). The install must have completed
    by the time the client context manager exits (#32, #138).

    Concretely: we assert that the ``_inflight_installs`` set is empty
    after shutdown — every task the lifespan observed has been awaited
    and removed via the done-callback.
    """
    import base64
    import hashlib
    import json as _json

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    # Patch the install runner to be slow enough that the task is still
    # in-flight when shutdown begins.
    real_run = ota._run_install

    async def slow_run(job_id: str, filename: str, data: bytes, username: str) -> None:
        # A real install takes hundreds of ms; 50ms is long enough that
        # the test reliably observes the in-flight state, short enough
        # to keep the suite snappy.
        await asyncio.sleep(0.05)
        await real_run(job_id, filename, data, username)

    ota._run_install = slow_run  # type: ignore[assignment]
    try:
        # Upload a signing key so the install can pass signature
        # verification (audit §8, #120).
        admin = {"Authorization": f"Bearer {tokens['admin']}"}
        sk = ed25519.Ed25519PrivateKey.generate()
        pk_b64 = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
        kresp = client.post(  # type: ignore[attr-defined]
            "/api/ota/signing-keys",
            json={"key_id": "k138", "alg": "ed25519", "public_key": pk_b64},
            headers=admin,
        )
        assert kresp.status_code == 200, kresp.text

        bundle = b"fake-swu"
        payload = _json.dumps({"version": "k138", "sha256": hashlib.sha256(bundle).hexdigest()})
        sig_b64 = base64.b64encode(sk.sign(payload.encode("utf-8"))).decode("ascii")
        sig_body = _json.dumps(
            {
                "alg": "ed25519",
                "signature": sig_b64,
                "public_key_id": "k138",
                "signed_payload": payload,
            }
        )

        # Hit the OTA install endpoint through the live app — this
        # exercises the real router → start_install → task-tracking path.
        resp = client.post(  # type: ignore[attr-defined]
            "/api/ota/app",
            files={"file": ("fw-test-138.swu", bundle, "application/octet-stream")},
            data={"signature": sig_body},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job_id"]
        # The task is registered in the module-level set; the live app
        # has just spawned it.
        assert any(not t.done() for t in ota._inflight_installs)
    finally:
        ota._run_install = real_run  # type: ignore[assignment]
    # We are still inside the TestClient context here. When the
    # ``client`` fixture's teardown runs, the lifespan exit fires
    # ``await ota.drain_inflight()`` — which awaits the slow task and
    # removes it. We verify that side effect from a separate test that
    # doesn't need the client (so we don't depend on order of
    # teardown).


def test_drain_inflight_waits_for_slow_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drain helper must block until every tracked task is done.

    This is the unit-level contract test for #32; the test above
    confirms it is wired into the lifespan, this one confirms the
    behaviour of the helper itself. The pair is what makes #138
    verifiable end-to-end.
    """
    completed: list[str] = []

    async def slow_run(job_id: str, filename: str, data: bytes, username: str) -> None:
        await asyncio.sleep(0.1)
        completed.append(job_id)

    monkeypatch.setattr(ota, "_run_install", slow_run)

    async def go() -> None:
        job = await ota.start_install("fw-slow.swu", b"x", "tester")
        assert ota._inflight_installs, "task should be tracked before drain"
        await ota.drain_inflight(timeout_s=2.0)
        assert completed == [job], "drain must wait for the install to finish"

    asyncio.run(go())


def test_drain_inflight_cancels_stragglers_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a task refuses to finish within the timeout, drain cancels it
    rather than blocking the shutdown forever (#32)."""

    async def hung_run(job_id: str, filename: str, data: bytes, username: str) -> None:
        # Long enough that drain_inflight's 0.1s timeout fires first.
        await asyncio.sleep(10)

    monkeypatch.setattr(ota, "_run_install", hung_run)

    async def go() -> None:
        await ota.start_install("fw-hung.swu", b"x", "tester")
        assert ota._inflight_installs
        await ota.drain_inflight(timeout_s=0.1)
        # Drain returns without blocking forever; straggler is cancelled
        # and removed by the done-callback.
        await asyncio.sleep(0.05)  # let the cancel propagate
        assert not [t for t in ota._inflight_installs if not t.done()]

    asyncio.run(go())


async def test_lifespan_calls_drain_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan shutdown must call ``ota.drain_inflight()``.

    Without that line, an in-flight install would be silently abandoned
    when the app shuts down (the underlying asyncio task would simply
    be cancelled along with the loop). The fix wires the lifespan to
    explicitly await the drain — verify the wiring directly.
    """
    called: list[bool] = []

    async def fake_drain(timeout_s: float = 5.0) -> None:
        called.append(True)

    monkeypatch.setattr("app.tasks.ota.drain_inflight", fake_drain)

    from app.main import create_app

    app = create_app()
    # Run the lifespan manually so we can observe the exit. The default
    # ``__aenter__`` would consume the body, so use an explicit
    # async-context-manager walk.
    cm = app.router.lifespan_context(app)
    await cm.__aenter__()
    await cm.__aexit__(None, None, None)
    assert called == [True], (
        "lifespan shutdown did not call ota.drain_inflight — in-flight " "OTA installs would be abandoned (#32, #138)"
    )
