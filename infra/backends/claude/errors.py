"""The errors a Conversation raises, and the classification of the SDK's structured error messages into them.

Exception hierarchy: BackendError
    + PermanentError (terminal error of the conversation; escapes send())
    + AgentError (fatal SDK subprocess error; escapes send())

A retryable provider condition never crosses this boundary as an exception: the Conversation recovers it itself and never resends the interrupted request.
"""
from __future__ import annotations

from enum import Enum

from claude_agent_sdk import AssistantMessage, ResultError, ResultMessage, TextBlock


class BackendError(Exception):
    """Base for all backend exceptions."""


class PermanentError(BackendError):
    """Caller must stop after a backend-recognized terminal provider error.

    The Reason enum gives engine code a stable taxonomy independent of any backend's native error codes. The provider_code carries the raw native code, when the provider gave one, for diagnostic display (Web failure_code field, transcript writer); a reason the Conversation itself decided carries none.
    """

    class Reason(Enum):
        BILLING = "billing"
        SESSION_LOST = "session_lost"
        # The provider stayed unavailable for the whole configured outage
        # ceiling (``llm.retry_ceiling_s``): the Conversation's own
        # decision to stop waiting, not a provider code.
        RETRY_CEILING = "retry_ceiling"
        # The backend rejected the request itself as invalid
        # (HTTP 400) — e.g. a model name the active account cannot serve.
        # Permanent: retrying the identical request cannot succeed, the
        # operator must fix the config. Distinct from BILLING (403, account
        # permission).
        BAD_REQUEST = "bad_request"
        # The model's reply did not fit in the reply-size ceiling,
        # so nothing usable came back. Permanent: the identical request
        # overflows the same ceiling again, so retrying only burns the clock —
        # one role spent its whole hour on six such replies and returned
        # nothing.
        REPLY_TOO_LONG = "reply_too_long"

    def __init__(
        self,
        reason: "PermanentError.Reason",
        *,
        provider_code: str | None = None,
        message: str = "",
    ) -> None:
        super().__init__(message or reason.value)
        self.reason = reason
        self.provider_code = provider_code


class AgentError(BackendError):
    """Public wrapper for SDK-level errors, propagated unchanged.

    Raised when a backend SDK subprocess itself fails. Its message preserves the native exception type and diagnostic output, so a caller does not replace those details with a generic message.

    ``oom_killed`` answers the one question the message cannot: the SDK reports a dead subprocess as an exit code, and a negative one says only that some signal killed it, so nothing in the text separates "the memory cap" from "our own stop wait". The kernel counts OOM kills per cgroup, and the unit's Confinement owns that cgroup, so the backend asks it across the launch and carries the answer here. ``PermanentError`` already keeps this shape: a fact the engine can act on, beside the third party's own text.
    """

    def __init__(self, message: str, *, oom_killed: bool) -> None:
        super().__init__(message)
        self.oom_killed = oom_killed

    def __reduce__(self) -> tuple[object, ...]:
        # The durable step records a failed attempt by pickling its error,
        # and a resume reads it back. The default exception reduction
        # replays only ``args``, so a keyword-only field is dropped and the
        # rebuild raises TypeError inside the resume itself — the run then
        # dies with "an unexpected error occurred" instead of resuming.
        return (_rebuild_agent_error, (str(self), self.oom_killed))


def _rebuild_agent_error(message: str, oom_killed: bool) -> "AgentError":
    return AgentError(message, oom_killed=oom_killed)


class _RetryableError(BackendError):
    """PRIVATE -- the adapter's own. Every non-conclusive provider/transport error: rate limit, quota, an expired credential, 5xx, overload, socket drop, safety refusal, or any text the classifier does not recognize. The Conversation catches it, opens the outage pause, and ends the request without completing it; it never crosses the neutral boundary.

    Carries only the raw provider error text -- no reset-time estimate. The three-outcome contract polls at a flat interval until recovery (or the configured ceiling), so nothing predicts when the provider recovers."""


# The structured provider codes that terminate a run; everything else, free
# text included, retries. The membership test is "can a retry of the
# identical request ever succeed": an overflowed reply ceiling, a
# missing account permission, or an invalid request cannot, while an expired
# credential (`authentication_failed`) waits for the operator's login.
_CONCLUSIVE_CODES = {
    "billing_error": PermanentError.Reason.BILLING,
    "invalid_request": PermanentError.Reason.BAD_REQUEST,
    "max_output_tokens": PermanentError.Reason.REPLY_TOO_LONG,
}

# The CLI ends a turn with ``terminal_reason`` ``aborted_streaming`` /
# ``aborted_tools`` only after ``interrupt()``: that is the product's own
# cancellation, never a provider outage and never a failure, so the
# conversation ends the turn on it and it never reaches classification.
CANCELLED_REASONS = frozenset({"aborted_streaming", "aborted_tools"})
# The CLI's own loop ended this conversation (its turn cap, its cost cap, its
# structured-output retries); the identical request cannot continue in that
# session, so the conversation is over for this Agent.
_EXHAUSTED_SUBTYPES = frozenset(
    {
        "error_max_turns",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }
)


def classify_assistant_error(message: AssistantMessage) -> BackendError:
    """Classify an ``AssistantMessage.error`` code: a conclusive code is permanent, everything else retries."""
    text = " ".join(
        b.text for b in (message.content or []) if isinstance(b, TextBlock)
    )
    reason = _CONCLUSIVE_CODES.get(message.error or "")
    if reason is not None:
        return PermanentError(reason, provider_code=message.error, message=text)
    return _RetryableError(text)


def classify_result_error(result: ResultMessage | ResultError) -> BackendError:
    """Classify an error result the CLI ended a turn with, or the ``ResultError`` the SDK raises when the CLI exits on one.

    The structured fields decide; ``errors`` and ``result`` are diagnostic prose carried in the message and never choose the outcome.
    """
    said = (
        "; ".join(result.errors or ())
        or result.result
        or result.subtype
        or "error result"
    )
    if result.api_error_status == 400:
        return PermanentError(
            PermanentError.Reason.BAD_REQUEST,
            provider_code=str(result.api_error_status),
            message=said,
        )
    if result.subtype in _EXHAUSTED_SUBTYPES:
        return PermanentError(
            PermanentError.Reason.SESSION_LOST,
            provider_code=result.subtype,
            message=said,
        )
    return _RetryableError(said)
