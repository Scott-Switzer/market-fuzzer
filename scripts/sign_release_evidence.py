"""Create or verify an operator-authenticated release evidence envelope."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sign_evidence(evidence: dict[str, Any], key: bytes) -> dict[str, Any]:
    if len(key) < 32:
        raise ValueError("evidence signing key must contain at least 32 bytes")
    return {
        "schema_version": "operator_signed_evidence_v1",
        "algorithm": "hmac-sha256-v1",
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "evidence": evidence,
        "signature": hmac.new(key, canonical(evidence), hashlib.sha256).hexdigest(),
        "claim_boundary": "operator-authenticated single-appliance evidence; not a public-key attestation",
    }


def verify_envelope(envelope: dict[str, Any], key: bytes) -> bool:
    expected = hmac.new(key, canonical(envelope["evidence"]), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(envelope.get("signature", "")), expected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("ARENA_EVIDENCE_SIGNING_KEY", "").encode()
    value = json.loads(args.evidence.read_text())
    if args.verify:
        if not verify_envelope(value, key):
            raise SystemExit("evidence signature verification failed")
        print("evidence signature verified")
        return
    envelope = sign_evidence(value, key)
    output = args.output or args.evidence.with_suffix(".signed.json")
    output.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
