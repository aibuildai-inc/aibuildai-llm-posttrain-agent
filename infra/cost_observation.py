"""Catalog price facts, and the rates the engine attaches to a cost-bearing observation before recording it."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ConfigDict

from infra import model_catalog
from infra.util.formatting import canonical_model_name


@dataclass(frozen=True)
class PricingRates:
    """Catalog price facts in USD per one million tokens."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    input: float
    output: float
    cache_read: float
    cache_write: float
    web_search: float


def _rates(model: str) -> PricingRates | None:
    if "<synthetic>" in model:
        return PricingRates(
            input=0.0, output=0.0, cache_read=0.0, cache_write=0.0,
            web_search=0.0,
        )
    info = model_catalog.catalog_rates_for(model)
    if info is None:
        return None
    return PricingRates(
        input=info.input,
        output=info.output,
        cache_read=info.cache_read,
        cache_write=info.cache_write,
        web_search=0.01,
    )


def local_rates_by_model(
    models: Iterable[str], resolved_model: str | None,
) -> dict[str, PricingRates | None]:
    """Every catalog rate Cost may select for one usage or turn observation: the models the observation names and the Agent's resolved model. The Agent records them on the Event before Cost reads it, so the journal holds the external price lookup the settlement used."""
    resolved = (
        canonical_model_name(resolved_model)
        if resolved_model is not None
        else None
    )
    named = {model for model in (*models, resolved) if model}
    return {model: _rates(model) for model in sorted(named)}
