"""One Agent's recorded cost facts, accounting rules, and published totals."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from engine.base import WorkflowBaseModel
from infra.cost_observation import PricingRates
from infra.util.formatting import canonical_model_name


class Usage(WorkflowBaseModel):
    """Canonical persisted token usage for one model call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    speed: str = ""

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            web_search_requests=(self.web_search_requests + other.web_search_requests),
            speed=self.speed or other.speed,
        )

    @classmethod
    def from_raw(cls, raw: dict | None) -> "Usage":
        """Parse a raw SDK or flattened transcript usage payload."""
        raw = raw or {}
        cache_creation = raw.get("cache_creation_input_tokens")
        if cache_creation is None:
            nested = raw.get("cache_creation") or {}
            cache_creation = (nested.get("ephemeral_5m_input_tokens", 0) or 0) + (
                nested.get("ephemeral_1h_input_tokens", 0) or 0
            )
        web_search = raw.get("web_search_requests")
        if web_search is None:
            server_tool_use = raw.get("server_tool_use") or {}
            web_search = server_tool_use.get("web_search_requests", 0)
        return cls(
            input_tokens=int(raw.get("input_tokens", 0) or 0),
            output_tokens=int(raw.get("output_tokens", 0) or 0),
            cache_read_input_tokens=int(raw.get("cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(cache_creation or 0),
            web_search_requests=int(web_search or 0),
            speed=str(raw.get("speed", "") or ""),
        )

class Turn(WorkflowBaseModel):
    """One billed model call, keyed by its stable message identity."""

    pricing_model: str
    attribution_model: str
    usage: Usage
    priced_cost_usd: float
    invocation: int


class InvocationResult(WorkflowBaseModel):
    """Terminal facts for one SDK invocation."""

    cost_usd: float | None
    usage_by_model: dict[str, dict[str, float]] | None
    duration_s: float | None
    duration_api_s: float | None
    num_turns: int
    local_rates_by_model: dict[str, PricingRates | None] | None = None


_ADDITIVE_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "web_search_requests",
)


def _classify_snapshot(new: Usage, old: Usage) -> str:
    pairs = [
        (getattr(new, field), getattr(old, field)) for field in _ADDITIVE_USAGE_FIELDS
    ]
    if any(new_value < old_value for new_value, old_value in pairs):
        return "shrink"
    if all(new_value == old_value for new_value, old_value in pairs):
        return "same"
    return "growth"


class Cost(WorkflowBaseModel):
    """One Agent's cost facts and the values derived from those facts.

    Usage snapshots are recorded by stable message identity and replace earlier snapshots. Published totals are always rebuilt from the complete recorded fact table, so duplicate delivery cannot increment them.
    """

    total_cost_usd: float = 0.0
    total_duration_s: float | None = 0.0
    total_duration_api_s: float | None = 0.0
    total_turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_input_tokens: int = 0
    total_cache_read_input_tokens: int = 0
    last_turn_input_tokens: int = 0
    last_turn_output_tokens: int = 0
    last_turn_cache_creation_input_tokens: int = 0
    last_turn_cache_read_input_tokens: int = 0
    cost_by_model: dict[str, float] = Field(default_factory=dict)
    turns_by_model: dict[str, int] = Field(default_factory=dict)

    turns: dict[str, Turn] = Field(default_factory=dict)
    invocation_usage: dict[int, Usage] = Field(default_factory=dict)
    results: dict[int, InvocationResult] = Field(default_factory=dict)
    # Which Action each provider send was made for, and what each send came
    # to. Both are invocation facts: the attribution is recorded when the send
    # opens, and the priced column is settled in the same pass as the lifetime
    # total, so the two views are the same arithmetic and cannot disagree. An
    # Action's spend is derived from them and is deliberately not stored: a
    # second total is a second authority.
    invocation_action: dict[int, int] = Field(default_factory=dict)
    cost_by_invocation: dict[int, float] = Field(default_factory=dict)
    current_invocation: int = 0
    current_invocation_open: bool = False
    unpriced_models_warned: list[str] = Field(default_factory=list)
    synth_seq: int = 0

    def open_invocation(self, action: int) -> None:
        """Start one provider send for one exact Action. A still-open invocation is a send with no recorded completion; the successor abandons it, and its real usage stays in the totals."""
        self.current_invocation += 1
        self.current_invocation_open = True
        self.invocation_action[self.current_invocation] = action

    def action_cost_usd(self, action: int) -> float:
        """What one Action's own provider sends came to.

        Derived, every time, from the sends attributed to that Action. A later
        Action of the same Agent spends none of this, and its own cap is
        compared with its own number."""
        # The attribution table is the authority, so it is what is read: an
        # invocation that has opened but not settled yet is in it with no
        # priced amount, and that is an ordinary state rather than a missing
        # key to defend against.
        return sum(
            self.cost_by_invocation[invocation]
            for invocation, owner in self.invocation_action.items()
            if owner == action and invocation in self.cost_by_invocation
        )

    def abandon_invocation(self) -> None:
        """Close an unfinished failed call without erasing its real usage."""
        self.current_invocation_open = False

    def invocation_is_open(self) -> bool:
        """Return whether the last backend turn has no terminal result."""
        return self.current_invocation_open

    def has_usage_snapshot(
        self, usage: Usage, *, cumulative: bool, message_id: str | None
    ) -> bool:
        """Return whether this exact SDK usage fact is already saved."""
        if cumulative:
            return self.invocation_usage.get(self.current_invocation) == usage
        turn = None if message_id is None else self.turns.get(message_id)
        return turn is not None and turn.usage == usage

    def record_usage(
        self,
        usage: Usage,
        *,
        cumulative: bool,
        message_id: str | None,
        event_model: str,
        parent_tool_use_id: str | None,
        resolved_model: str | None,
        local_rates_by_model: dict[str, PricingRates | None] | None,
        cache_creation_input_tokens: int | None,
        is_anthropic: bool,
        owner_role: str,
    ) -> None:
        """Apply one UsageDelta to Cost-owned accounting state."""
        resolved_model_name = (
            canonical_model_name(resolved_model) if resolved_model is not None else None
        )
        if cumulative:
            self._record_cumulative(usage, owner_role=owner_role)
            self._publish(
                is_anthropic=is_anthropic,
                resolved_model=resolved_model,
                resolved_model_name=resolved_model_name,
            )
            return

        self._record_turn(
            usage,
            message_id=message_id,
            event_model=event_model,
            parent_tool_use_id=parent_tool_use_id,
            resolved_model_name=resolved_model_name,
            local_rates_by_model=local_rates_by_model,
            is_anthropic=is_anthropic,
            owner_role=owner_role,
        )
        self.last_turn_input_tokens = usage.input_tokens
        self.last_turn_output_tokens = usage.output_tokens
        self.last_turn_cache_read_input_tokens = usage.cache_read_input_tokens
        if cache_creation_input_tokens is not None:
            self.last_turn_cache_creation_input_tokens = cache_creation_input_tokens
        self._publish(
            is_anthropic=is_anthropic,
            resolved_model=resolved_model,
            resolved_model_name=resolved_model_name,
        )

    def _record_cumulative(self, usage: Usage, *, owner_role: str) -> None:
        stored = self.invocation_usage.get(self.current_invocation)
        if stored is not None and _classify_snapshot(usage, stored) == "shrink":
            raise AssertionError(
                f"[{owner_role}] cumulative UsageDelta for invocation "
                f"{self.current_invocation} SHRANK vs the prior snapshot "
                f"({usage!r} < {stored!r}); a cumulative total can only grow "
                "(upstream artifact anthropics/claude-code#6805)."
            )
        self.invocation_usage[self.current_invocation] = usage

    def _record_turn(
        self,
        usage: Usage,
        *,
        message_id: str | None,
        event_model: str,
        parent_tool_use_id: str | None,
        resolved_model_name: str | None,
        local_rates_by_model: dict[str, PricingRates | None] | None,
        is_anthropic: bool,
        owner_role: str,
    ) -> None:
        pricing_model = (
            self._forced_gateway_model(
                resolved_model_name=resolved_model_name,
                is_anthropic=is_anthropic,
            )
            or event_model
        )
        attribution_model = self._attribution_model(
            event_model,
            parent_tool_use_id=parent_tool_use_id,
            resolved_model_name=resolved_model_name,
            is_anthropic=is_anthropic,
        )
        if message_id is None:
            self.synth_seq += 1
            key = f"__none__{self.current_invocation}_{self.synth_seq}"
            self.turns[key] = Turn(
                pricing_model=pricing_model,
                attribution_model=attribution_model,
                usage=usage,
                priced_cost_usd=self._price(
                    pricing_model,
                    usage,
                    local_rates_by_model=local_rates_by_model,
                ),
                invocation=self.current_invocation,
            )
            return

        existing = self.turns.get(message_id)
        if existing is None:
            self.turns[message_id] = Turn(
                pricing_model=pricing_model,
                attribution_model=attribution_model,
                usage=usage,
                priced_cost_usd=self._price(
                    pricing_model,
                    usage,
                    local_rates_by_model=local_rates_by_model,
                ),
                invocation=self.current_invocation,
            )
            return

        if existing.invocation != self.current_invocation:
            raise AssertionError(
                f"[{owner_role}] message_id {message_id!r} first seen in "
                f"invocation {existing.invocation} reappeared under invocation "
                f"{self.current_invocation}; one billed turn belongs to one "
                "invocation (upstream artifact anthropics/claude-code#6805)."
            )
        kind = _classify_snapshot(usage, existing.usage)
        if kind == "shrink":
            raise AssertionError(
                f"[{owner_role}] message_id {message_id!r} re-reported a SHRUNK "
                f"usage snapshot ({usage!r} < {existing.usage!r}); a turn's "
                "cumulative usage can only be re-reported byte-identical or grown "
                "(upstream artifact anthropics/claude-code#6805)."
            )
        if kind == "same":
            return
        priced_cost = self._price(
            pricing_model,
            usage,
            local_rates_by_model=local_rates_by_model,
        )
        existing.usage = usage
        existing.pricing_model = pricing_model
        existing.attribution_model = attribution_model
        existing.priced_cost_usd = priced_cost

    def settle_turn(
        self,
        result: InvocationResult,
        *,
        is_anthropic: bool,
        resolved_model: str | None,
        owner_role: str,
    ) -> None:
        if result.cost_usd is not None and result.cost_usd < 0:
            raise AssertionError(
                f"[{owner_role}] TurnComplete carried negative "
                f"cost_usd={result.cost_usd!r}; refusing to persist a negative "
                "cost into the run record."
            )
        existing = self.results.get(self.current_invocation)
        if existing is not None and existing != result:
            raise AssertionError(
                f"[{owner_role}] invocation {self.current_invocation} already has "
                f"result {existing!r}; a differing re-record {result!r} is a "
                "stream contradiction (one invocation owns one authoritative "
                "ResultMessage)."
            )
        self.results[self.current_invocation] = result
        self.current_invocation_open = False
        resolved_model_name = (
            canonical_model_name(resolved_model) if resolved_model is not None else None
        )
        self._publish(
            is_anthropic=is_anthropic,
            resolved_model=resolved_model,
            resolved_model_name=resolved_model_name,
        )

    def _forced_gateway_model(
        self,
        *,
        resolved_model_name: str | None,
        is_anthropic: bool,
    ) -> str | None:
        if is_anthropic or resolved_model_name is None:
            return None
        return resolved_model_name if resolved_model_name.startswith("gpt-") else None

    def _attribution_model(
        self,
        event_model: str,
        *,
        parent_tool_use_id: str | None,
        resolved_model_name: str | None,
        is_anthropic: bool,
    ) -> str:
        forced_model = self._forced_gateway_model(
            resolved_model_name=resolved_model_name,
            is_anthropic=is_anthropic,
        )
        if forced_model is not None:
            return forced_model
        if is_anthropic or parent_tool_use_id is not None:
            return event_model
        return resolved_model_name or event_model

    def _price(
        self,
        model: str,
        usage: Usage,
        *,
        local_rates_by_model: dict[str, PricingRates | None] | None,
    ) -> float:
        if not model:
            return 0.0
        if local_rates_by_model is None:
            raise AssertionError(
                "UsageDelta reached local pricing without recorded "
                "model-catalog observations"
            )
        if model not in local_rates_by_model:
            raise AssertionError(
                "UsageDelta recorded no local-rate observation for selected "
                f"pricing model {model!r}"
            )
        rates = local_rates_by_model[model]
        if rates is None:
            self._record_unpriced(model)
            return 0.0
        return self._cost_from_rates(usage, rates)

    def _record_unpriced(self, model: str) -> None:
        if model not in self.unpriced_models_warned:
            self.unpriced_models_warned.append(model)

    def _publish(
        self,
        *,
        is_anthropic: bool,
        resolved_model: str | None,
        resolved_model_name: str | None,
    ) -> None:
        total_cost = 0.0
        total_usage = Usage()
        total_turns = 0
        total_duration_s: float | None = 0.0
        total_duration_api_s: float | None = 0.0
        cost_by_model: dict[str, float] = {}
        turns_by_model: dict[str, int] = {}
        cost_by_invocation: dict[int, float] = {}

        invocations = (
            {turn.invocation for turn in self.turns.values()}
            | set(self.invocation_usage)
            | set(self.results)
        )
        for invocation in invocations:
            turns = [
                turn for turn in self.turns.values() if turn.invocation == invocation
            ]
            result = self.results.get(invocation)
            snapshot = self.invocation_usage.get(invocation)
            if snapshot is None:
                snapshot = sum((turn.usage for turn in turns), Usage())
            total_usage += snapshot
            total_turns += result.num_turns if result is not None else len(turns)
            for turn in turns:
                if turn.attribution_model:
                    turns_by_model[turn.attribution_model] = (
                        turns_by_model.get(turn.attribution_model, 0) + 1
                    )
            if result is None:
                by_model = self._priced_by_model(turns)
                headline = sum(by_model.values())
            else:
                headline, by_model = self._derive_result_cost(
                    turns,
                    result,
                    is_anthropic=is_anthropic,
                    resolved_model=resolved_model,
                    resolved_model_name=resolved_model_name,
                )
                if result.duration_s is None:
                    total_duration_s = None
                elif total_duration_s is not None:
                    total_duration_s += result.duration_s
                if result.duration_api_s is None:
                    total_duration_api_s = None
                elif total_duration_api_s is not None:
                    total_duration_api_s += result.duration_api_s
            total_cost += headline
            cost_by_invocation[invocation] = headline
            for model, amount in by_model.items():
                cost_by_model[model] = cost_by_model.get(model, 0.0) + amount

        self.cost_by_invocation = cost_by_invocation
        self.total_cost_usd = total_cost
        self.total_input_tokens = total_usage.input_tokens
        self.total_output_tokens = total_usage.output_tokens
        self.total_cache_creation_input_tokens = total_usage.cache_creation_input_tokens
        self.total_cache_read_input_tokens = total_usage.cache_read_input_tokens
        self.total_turns = total_turns
        self.total_duration_s = total_duration_s
        self.total_duration_api_s = total_duration_api_s
        self.cost_by_model = cost_by_model
        self.turns_by_model = turns_by_model

    def _priced_by_model(self, turns: list[Turn]) -> dict[str, float]:
        by_model: dict[str, float] = {}
        for turn in turns:
            if turn.attribution_model:
                by_model[turn.attribution_model] = (
                    by_model.get(turn.attribution_model, 0.0) + turn.priced_cost_usd
                )
        return by_model

    def _derive_result_cost(
        self,
        turns: list[Turn],
        result: InvocationResult,
        *,
        is_anthropic: bool,
        resolved_model: str | None,
        resolved_model_name: str | None,
    ) -> tuple[float, dict[str, float]]:
        if result.cost_usd is None:
            by_model = self._priced_by_model(turns)
            return sum(by_model.values()), by_model

        by_model: dict[str, float] = {}
        if result.usage_by_model:
            if is_anthropic:
                for model, usage in result.usage_by_model.items():
                    by_model[model] = by_model.get(model, 0.0) + float(usage["cost_usd"])
                return result.cost_usd, by_model

            forced_model = self._forced_gateway_model(
                resolved_model_name=resolved_model_name,
                is_anthropic=is_anthropic,
            )
            if result.local_rates_by_model is None:
                raise AssertionError(
                    "TurnComplete reached local settlement without recorded "
                    "model-catalog observations"
                )
            rates_by_model = result.local_rates_by_model
            if forced_model is not None:
                if forced_model not in rates_by_model:
                    raise AssertionError(
                        "TurnComplete recorded no local-rate observation for "
                        f"selected pricing model {forced_model!r}"
                    )
                rates = rates_by_model[forced_model]
                if rates is None:
                    self._record_unpriced(forced_model)
                    return 0.0, {}
                usage = sum(
                    (Usage.from_raw(row) for row in result.usage_by_model.values()),
                    Usage(),
                )
                amount = self._cost_from_rates(usage, rates)
                return amount, {forced_model: amount}

            settled = 0.0
            for model, row in result.usage_by_model.items():
                if model not in rates_by_model:
                    raise AssertionError(
                        "TurnComplete recorded no local-rate observation for "
                        f"usage_by_model key {model!r}"
                    )
                rates = rates_by_model[model]
                if rates is None:
                    self._record_unpriced(model)
                    continue
                amount = self._cost_from_rates(Usage.from_raw(row), rates)
                by_model[model] = by_model.get(model, 0.0) + amount
                settled += amount
            return settled, by_model

        priced_by_model = self._priced_by_model(turns)
        priced_sum = sum(priced_by_model.values())
        if priced_sum > 0.0:
            scale = result.cost_usd / priced_sum
            for model, amount in priced_by_model.items():
                by_model[model] = by_model.get(model, 0.0) + amount * scale
        elif result.cost_usd > 0.0 and resolved_model is not None:
            by_model[resolved_model] = (
                by_model.get(resolved_model, 0.0) + result.cost_usd
            )
        return result.cost_usd, by_model

    def _cost_from_rates(
        self,
        usage: Usage,
        rates: PricingRates,
    ) -> float:
        cache_write = rates.cache_write or rates.input
        return sum(
            (
                usage.input_tokens / 1_000_000 * rates.input,
                usage.output_tokens / 1_000_000 * rates.output,
                usage.cache_read_input_tokens / 1_000_000 * rates.cache_read,
                usage.cache_creation_input_tokens / 1_000_000 * cache_write,
                usage.web_search_requests * rates.web_search,
            )
        )
