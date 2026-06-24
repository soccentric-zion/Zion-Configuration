"""Regression test for #141 (test isolation).

CLAUDE.md Do: tests must be hermetic — they must not depend on
shared state. Previously the ``client`` and ``tokens`` fixtures were
``scope="session"``, so a DB row written by one test leaked into the
next (the canonical example: ``test_ota_history_tiebreak`` failed
when an earlier run left ``v-test-N`` rows in ``ota_history``).

The fixtures are now function-scoped: every test gets a fresh
tempdir, config, and DB. This test asserts the property directly —
mutate the DB in one test, then write a *separate* test that proves
the next test sees a fresh state.
"""

from __future__ import annotations


def test_db_mutations_do_not_leak_to_next_test(client: object, tokens: dict[str, str]) -> None:
    """Write a row into ``ota_history`` with a unique sentinel version.
    A sibling test (``test_db_sees_fresh_state``) reads back and
    asserts it sees *zero* rows with that sentinel — proof that the
    fixture resets between tests (#141).
    """
    from app.db.database import get_db

    sentinel = f"v-isol-{id(test_db_mutations_do_not_leak_to_next_test)}"

    async def insert() -> None:
        await get_db().execute(
            "INSERT INTO ota_history (ts, version, result, triggered_by) VALUES (?, ?, 'queued', 'iso')",
            ("2099-01-01 00:00:00", sentinel),
        )

    import asyncio

    asyncio.run(insert())

    # Sanity: this test sees the row.
    rows = client.get(  # type: ignore[attr-defined]
        "/api/ota/history?limit=1000",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    ).json()["history"]
    assert any(r["version"] == sentinel for r in rows)


def test_db_sees_fresh_state(client: object, tokens: dict[str, str]) -> None:
    """Sibling of ``test_db_mutations_do_not_leak_to_next_test``: this
    test must see *zero* rows with the sentinel version, proving the
    fixture reset the DB between tests (#141)."""
    sentinel = f"v-isol-{id(test_db_mutations_do_not_leak_to_next_test)}"
    rows = client.get(  # type: ignore[attr-defined]
        "/api/ota/history?limit=1000",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    ).json()["history"]
    leaked = [r for r in rows if r["version"] == sentinel]
    assert not leaked, (
        f"DB leaked between tests: found {len(leaked)} rows with sentinel {sentinel!r} "
        f"— conftest client fixture is not resetting state (#141)"
    )
