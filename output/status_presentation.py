"""Single source of truth for run-status presentation across all output surfaces.

Owns the per-outcome-state presentation -- an INK hex, a text LABEL, a width-safe SYMBOL, and a Web theme TOKEN -- plus the shared health palette (success / warning / error). The Web view reads its status colors from here, so a status is defined exactly once.

Scope: this module owns ONLY execution-status color and the health palette. It does NOT own chrome (panel borders, secondary body text) or agent/model branding -- those stay where they live and are deliberately decoupled from status so they never shift when this palette changes.

It imports the engine status enums and is placed at the output/ package root as the common upstream of every output renderer. It creates no backend import: backends stays a self-contained library and never imports output.

Palette: macaron-derived inks -- each hue auto-darkened until it clears WCAG 4.5:1 on white, so an ink dot/text on white is legible.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.status import StatusState, TerminalStatus


@dataclass(frozen=True)
class Presentation:
    """How one StatusState renders across channels.

    - ink:    the status hue hex, used by the Web status dot color.
    - label:  short user-visible text label.
    - symbol: the uniform recolored status dot (◉) for every state -- colour carries the state, there is no per-state shape (project decision; WCAG 1.4.1 colour-redundancy dropped, 1.4.3 contrast kept).
    - token:  the Web theme token name.
    """

    ink: str
    label: str
    symbol: str
    token: str


# ── Health palette (shared: status states AND the resource-health axis) ───────
# good / warn / bad inks. Reused by budget-burn, rate-limit, MCP-health and
# validation-emphasis -- one health palette, no second set.
SUCCESS_INK = "#287748"  # green  -- done / good
WARNING_INK = "#84661F"  # amber  -- timeout + interrupted / warn
ERROR_INK = "#A63040"  # red    -- failed / bad

# States off the green/amber/red health axis get their own ink.
RUNNING_INK = "#2E629E"  # blue
PENDING_INK = "#5A626D"  # neutral grey

# Symbol channel. ONE recolored dot for every state: colour (ink/token) carries
# the state, there is NO per-state shape. WCAG 1.4.1 (colour-not-sole-signal) is
# deliberately dropped here (project decision, see designing-ui experience #3);
# WCAG 1.4.3 contrast is KEPT -- the ink stays >= 4.5:1. Glyph: U+25C9 fish-eye.
_DOT = "◉"  # the recolored status dot -- the only status symbol


_PRESENTATION: dict[StatusState, Presentation] = {
    StatusState.PENDING: Presentation(
        ink=PENDING_INK, label="PENDING", symbol=_DOT, token="pending"
    ),
    StatusState.RUNNING: Presentation(
        ink=RUNNING_INK, label="RUNNING", symbol=_DOT, token="running"
    ),
    StatusState.DONE: Presentation(
        ink=SUCCESS_INK, label="DONE", symbol=_DOT, token="bold success"
    ),
    StatusState.FAILED: Presentation(
        ink=ERROR_INK, label="FAILED", symbol=_DOT, token="bold error"
    ),
}


def presentation_for(state: StatusState) -> Presentation:
    """Return the Presentation for a StatusState. Fails loud on an unknown state."""
    if state not in _PRESENTATION:
        raise AssertionError(f"presentation_for: no Presentation for {state!r}")
    return _PRESENTATION[state]


_TERMINAL_STATE: dict[TerminalStatus, StatusState] = {
    TerminalStatus.COMPLETED: StatusState.DONE,
    TerminalStatus.FAILED: StatusState.FAILED,
    # FAILED_UNEXPECTED (an internal bug) shares the red/error hue
    # with FAILED — Axis B changes only the verdict WORD (TerminalStatus
    # .display_word -> "UNEXPECTED") + the guidance, never the health colour.
    TerminalStatus.FAILED_UNEXPECTED: StatusState.FAILED,
    TerminalStatus.INTERRUPTED: StatusState.RUNNING,
    TerminalStatus.EMPTY: StatusState.RUNNING,
}


def classify_terminal(terminal: TerminalStatus) -> StatusState:
    """Map a pipeline TerminalStatus to its COLOUR StatusState.

    COLOUR ONLY: the verdict WORD (COMPLETED / EMPTY / ...) is pipeline-level and stays separate -- callers keep the word (e.g. terminal.value.upper()) and must NOT overwrite it with presentation_for(state).label. EMPTY and INTERRUPTED use the RUNNING colour. Fails loud on an unhandled terminal.
    """
    if terminal not in _TERMINAL_STATE:
        raise AssertionError(
            f"classify_terminal: unhandled TerminalStatus {terminal!r}"
        )
    return _TERMINAL_STATE[terminal]


def classify_run(terminal: TerminalStatus | None) -> tuple[StatusState, str]:
    """Return the run's current colour state and user-visible word."""
    if terminal is not None:
        return classify_terminal(terminal), terminal.display_word
    return StatusState.RUNNING, "RUNNING"


def classify_mcp(status: str) -> tuple[StatusState, str]:
    """Map an MCP server's status label to its (StatusState, readiness WORD).

    The MCP-health axis (the comment beside SUCCESS/WARNING/ERROR_INK names it a reuser of the one health palette) is not an execution status, so its word is its own -- like a WorkUnit's public status returns state and label, this returns the colour StatusState plus the word the Web Workspace shows. A reachable server (RunMcpClient status "ok") is DONE (green) labelled "ready"; the one failure status the startup check emits -- unreachable, possibly carrying a "(reason)" suffix the Web view already built into the label -- is FAILED (red) and keeps that full label so the operator sees what broke. Colour + the uniform ◉ symbol come from presentation_for(state); this only owns the mapping and the word.
    """
    if status == "ok":
        return StatusState.DONE, "ready"
    return StatusState.FAILED, status
