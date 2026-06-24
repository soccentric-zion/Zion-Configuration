"""Regression tests for #120 — OTA bundle signature verification (audit §8).

audit.md §8 OTA: "signed update chain." The previous implementation
literally emitted "checking signature" (app/tasks/ota.py) without any
actual swupdate / GPG verification — a malicious or corrupted bundle
would be accepted and "installed" in simulation.

The fix:
- ``OtaSignature`` pydantic model in ``app/util/models.py`` (with
  ``extra="forbid"``).
- ``signing_keys`` table (migration v11) holds operator-uploaded
  public keys.
- ``POST /api/security/signing-keys`` (admin) uploads a public key.
- ``POST /api/ota/app`` now requires a ``signature`` form field;
  the router verifies the signature against the bundle's SHA256
  before the install runs. Mismatches return 422 + an audit row.
- ``verify_bundle_signature`` is the single verification entry point
  in ``app/tasks/ota.py`` — exercised end-to-end here with a
  real ed25519 key generated on the fly.
"""

from __future__ import annotations

import base64
import hashlib
import json


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _gen_keypair() -> tuple[bytes, bytes]:
    """Return ``(public_bytes, private_bytes)`` for a fresh ed25519 key.

    Uses the stdlib `cryptography` package — already a first-party
    project dep.
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    sk = ed25519.Ed25519PrivateKey.generate()
    pk_bytes = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sk_pem = sk.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return pk_bytes, sk_pem


def _upload_key(client: object, admin: dict[str, str], key_id: str, pk_b64: str) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/signing-keys",
        json={"key_id": key_id, "alg": "ed25519", "public_key": pk_b64, "label": "test"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text


def _sign(private_pem: bytes, payload_json: str) -> str:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    sk = load_pem_private_key(private_pem, password=None)
    sig = sk.sign(payload_json.encode("utf-8"))
    return base64.b64encode(sig).decode("ascii")


def test_valid_signature_passes(client: object, tokens: dict[str, str]) -> None:
    """A bundle with a valid ed25519 signature is accepted (#120)."""
    admin = _h(tokens["admin"])
    pk_bytes, sk_pem = _gen_keypair()
    _upload_key(client, admin, "test-key-good", base64.b64encode(pk_bytes).decode("ascii"))

    bundle = b"fake swu bundle v1.2.3"
    sha = hashlib.sha256(bundle).hexdigest()
    payload = json.dumps({"version": "1.2.3", "sha256": sha})
    sig_b64 = _sign(sk_pem, payload)

    sig_body = json.dumps(
        {
            "alg": "ed25519",
            "signature": sig_b64,
            "public_key_id": "test-key-good",
            "signed_payload": payload,
        }
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("update.swu", bundle, "application/octet-stream")},
        data={"signature": sig_body},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "update.swu"
    assert body["size"] == len(bundle)
    assert body["job_id"]


def test_invalid_signature_rejected(client: object, tokens: dict[str, str]) -> None:
    """A bundle with a tampered signature is 422 + audit (#120)."""
    admin = _h(tokens["admin"])
    pk_bytes, _sk_pem = _gen_keypair()
    _upload_key(client, admin, "test-key-bad", base64.b64encode(pk_bytes).decode("ascii"))

    bundle = b"another bundle"
    sha = hashlib.sha256(bundle).hexdigest()
    payload = json.dumps({"version": "0.0.1", "sha256": sha})

    # Sign with a *different* private key — the verify path will
    # reject the signature.
    _, other_sk_pem = _gen_keypair()
    bad_sig = _sign(other_sk_pem, payload)

    sig_body = json.dumps(
        {
            "alg": "ed25519",
            "signature": bad_sig,
            "public_key_id": "test-key-bad",
            "signed_payload": payload,
        }
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("bad.swu", bundle, "application/octet-stream")},
        data={"signature": sig_body},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "signature_invalid"

    # Audit row recorded.
    audit_resp = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_signature_rejected&limit=200", headers=admin
    )
    assert audit_resp.status_code == 200
    rows = audit_resp.json()["entries"]
    matches = [r for r in rows if r.get("target") == "bad.swu"]
    assert len(matches) == 1, f"expected one ota_signature_rejected row, got {matches}"


def test_signature_hash_mismatch_rejected(client: object, tokens: dict[str, str]) -> None:
    """A signed manifest for bundle A cannot be replayed against bundle B (#120)."""
    admin = _h(tokens["admin"])
    pk_bytes, sk_pem = _gen_keypair()
    _upload_key(client, admin, "test-key-replay", base64.b64encode(pk_bytes).decode("ascii"))

    # Sign the manifest for a 100-byte bundle...
    bundle_a = b"A" * 100
    sha_a = hashlib.sha256(bundle_a).hexdigest()
    payload_a = json.dumps({"version": "9.9.9", "sha256": sha_a})
    sig_a = _sign(sk_pem, payload_a)

    # ...then upload a *different* bundle but the same manifest + sig.
    bundle_b = b"B" * 200
    sig_body = json.dumps(
        {
            "alg": "ed25519",
            "signature": sig_a,
            "public_key_id": "test-key-replay",
            "signed_payload": payload_a,
        }
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("replay.swu", bundle_b, "application/octet-stream")},
        data={"signature": sig_body},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "signature_invalid"

    # The audit row uses the hash-mismatch action specifically.
    audit_resp = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_signature_hash_mismatch&limit=200", headers=admin
    )
    assert audit_resp.status_code == 200
    rows = audit_resp.json()["entries"]
    matches = [r for r in rows if r.get("target") == "replay.swu"]
    assert len(matches) == 1, f"expected one hash-mismatch row, got {matches}"


def test_unknown_key_id_rejected(client: object, tokens: dict[str, str]) -> None:
    """An unknown ``public_key_id`` is 422 (not 404 — the caller is installing)."""
    admin = _h(tokens["admin"])
    bundle = b"x" * 50
    payload = json.dumps({"version": "1.0.0", "sha256": hashlib.sha256(bundle).hexdigest()})
    sig_body = json.dumps(
        {
            "alg": "ed25519",
            "signature": base64.b64encode(b"x" * 64).decode("ascii"),
            "public_key_id": "no-such-key",
            "signed_payload": payload,
        }
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("x.swu", bundle, "application/octet-stream")},
        data={"signature": sig_body},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text
    assert "no-such-key" in resp.json()["error"]["message"]


def test_missing_signature_field_rejected(client: object, tokens: dict[str, str]) -> None:
    """An upload without a ``signature`` form field is 422."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("x.swu", b"y", "application/octet-stream")},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_alg_mismatch_rejected(client: object, tokens: dict[str, str]) -> None:
    """A key uploaded as ed25519 cannot be used with alg=rsa-pss."""
    admin = _h(tokens["admin"])
    pk_bytes, _sk_pem = _gen_keypair()
    _upload_key(client, admin, "test-key-alg", base64.b64encode(pk_bytes).decode("ascii"))

    bundle = b"abc"
    payload = json.dumps({"version": "1", "sha256": hashlib.sha256(bundle).hexdigest()})
    sig_body = json.dumps(
        {
            "alg": "rsa-pss",
            "signature": base64.b64encode(b"x" * 64).decode("ascii"),
            "public_key_id": "test-key-alg",
            "signed_payload": payload,
        }
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("x.swu", bundle, "application/octet-stream")},
        data={"signature": sig_body},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_signing_key_admin_only(client: object, tokens: dict[str, str]) -> None:
    """Only admin can upload / delete signing keys."""
    viewer = _h(tokens["viewer"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/signing-keys",
        json={"key_id": "x", "alg": "ed25519", "public_key": "AAAA"},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text


def test_signing_key_list_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    """A viewer can list signing keys (no secrets)."""
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/ota/signing-keys", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert "keys" in resp.json()
