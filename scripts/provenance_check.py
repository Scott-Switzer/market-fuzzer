from pathlib import Path

required = [
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "docs/HACKATHON_WORK.md",
    "docs/DECISIONS.md",
    "docs/CODEX_COLLABORATION.md",
    "docs/METHODOLOGY.md",
    "docs/LIMITATIONS.md",
]
missing = [path for path in required if not Path(path).is_file()]
assert not missing, f"missing provenance files: {missing}"
for path in Path("app").rglob("*.py"):
    text = path.read_text(errors="ignore")
    assert "LOBSTER" not in text, f"commercial-data dependency referenced in {path}"
print("provenance files and public-data boundary ok")
