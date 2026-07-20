from scripts.sign_release_evidence import sign_evidence, verify_envelope


def test_signed_evidence_detects_tampering() -> None:
    key = b"k" * 32
    envelope = sign_evidence({"campaign": "verified"}, key)
    assert verify_envelope(envelope, key)
    envelope["evidence"]["campaign"] = "changed"
    assert not verify_envelope(envelope, key)
