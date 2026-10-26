"""Labels and limits for research-only price bases."""

from typing import Literal, cast

PriceBasis = Literal["action_free", "adjusted_research"]

ADJUSTED_RESEARCH_WARNING = (
    "Adjusted research basis: simulated quantities, fees, equity, and returns use vendor-adjusted "
    "prices. This does not model dividend cash or share transformations in actual accounts, "
    "does not support point-in-time claims, and may change when vendors apply future adjustments."
)


def validate_price_basis(value: str) -> PriceBasis:
    """Return a recognized price-basis label or reject it."""
    if value not in ("action_free", "adjusted_research"):
        raise ValueError(f"unknown price basis: {value!r}")
    return cast(PriceBasis, value)


def research_warnings(price_basis: str) -> list[str]:
    """Return disclosures required for the selected research basis."""
    basis = validate_price_basis(price_basis)
    return [ADJUSTED_RESEARCH_WARNING] if basis == "adjusted_research" else []
