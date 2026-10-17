"""Product copy may describe only tiers the repository actually ships."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = (ROOT / "README.md", ROOT / "docs" / "GTM_STRATEGY.md")
ASPIRATIONAL = (
    "$99",
    "$399",
    "Research license",
    "Team license",
    "/seat/mo",
    "Enterprise/private pack",
    "custom pricing",
)


def test_pricing_surfaces_omit_unshipped_paid_tiers() -> None:
    for path in SURFACES:
        text = path.read_text()
        for claim in ASPIRATIONAL:
            assert claim not in text, f"{path.name} still claims {claim!r}"
    readme = (ROOT / "README.md").read_text()
    pricing = readme.split("## Pricing", 1)[1].split("\n## ", 1)[0]
    assert "$" not in pricing
    assert "local self-hosted" in pricing
    assert "no paid subscription" in pricing
