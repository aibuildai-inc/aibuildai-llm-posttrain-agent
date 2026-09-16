"""Answer prompts on a Conversation the caller holds open, or on one opened for a single prompt.

Also the one source of the gate rejection every caller sends back into a conversation, so no caller carries its own near-copy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from infra.backends.claude.conversation import Conversation
from infra.backends.claude.event_adapter import ConversationObserver
from infra.backends.spec import AgentSpec


class StructuredOutputInvalid(AssertionError):
    """The conversation returned no structured result where one was required."""


def rejected_resubmit(*, gate: str, instruction: str, label: str, reason: str) -> str:
    """The one sentence every gate that runs AFTER submission sends back.

    A gate names itself, the caller says what the agent must do next (``instruction``), and the gate labels its reason.
    """
    return (
        f"Your submitted output has been REJECTED by the {gate} that runs "
        "AFTER submission. The earlier 'Structured output provided "
        "successfully' tool result only confirmed the schema; that submission "
        f"is now void. {instruction} "
        f"{label}: {reason}"
    )


async def conversation_turn(
    conversation: Conversation,
    *,
    spec: AgentSpec,
    prompt: str,
    observer: ConversationObserver | None = None,
    verify: Callable[[dict], Awaitable[str | None]] | None = None,
) -> dict | str:
    """Answer one prompt on a Conversation the caller opened, and return its structured result or top-level text.

    The caller owns the Conversation, so this neither opens nor closes it: a conversation that answers several prompts in a row keeps everything the earlier prompts read and fetched. Provider interruptions are the Conversation's own business and never surface here. ``observer`` hears every fact of this prompt's interactions, the repair rounds included; a caller with nothing to record passes none.

    Without ``verify`` this is one completed interaction. With it, the conversation runs as many as the check needs: the check returns None to accept the terminal structured result, or a sentence saying what is wrong. That sentence goes back to the SAME conversation, so the agent still holds everything it read and fetched and repairs its own answer instead of starting over from an empty context. There is no attempt bound; a conversation that is not converging is for the person watching the run to stop.

    ``verify`` takes the result as a dict, so this layer needs no type from the caller's layer and the caller keeps deciding what counts as wrong. It needs an ``output_schema`` to have something to check: without one the conversation returns text after a single completed interaction, and a check that can never run is worse than no check, so that combination is refused before the first send.
    """
    if verify is not None and spec.output_schema is None:
        raise AssertionError(
            f"{spec.name}: verify needs an output_schema to check"
        )
    message = prompt
    while True:
        response = await conversation.send(message, observer=observer)
        if spec.output_schema is None:
            return response.text
        if response.output is None:
            raise StructuredOutputInvalid(
                f"{spec.name}: conversation produced no structured result"
            )
        if verify is None:
            return response.output
        reason = await verify(response.output)
        if reason is None:
            return response.output
        message = rejected_resubmit(
            gate="authoritative verification",
            instruction="Fix the problem and call StructuredOutput again.",
            label="Reason",
            reason=reason,
        )


async def agent_turn(
    *,
    spec: AgentSpec,
    prompt: str,
    observer: ConversationObserver | None = None,
    verify: Callable[[dict], Awaitable[str | None]] | None = None,
    retry_ceiling_s: float | None = None,
) -> dict | str:
    """Run one agent conversation from a complete agent spec, checked or unchecked.

    This is ``conversation_turn`` plus the Conversation it runs on: open one for this prompt, answer it under the same policy, and close it when the answer is in. A caller that keeps no conversation therefore never names one.
    """
    async with Conversation(spec, retry_ceiling_s=retry_ceiling_s) as conversation:
        return await conversation_turn(
            conversation, spec=spec, prompt=prompt, observer=observer, verify=verify
        )
