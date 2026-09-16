"""The debate participant: one identity across its opening, critique, and rebuttal."""

from engine.work_unit.agent import Agent

from .io import DebateTurnOutput, DebaterInput
from .policy import DEBATER_POLICY


class DebaterAgent(Agent[DebaterInput, DebateTurnOutput]):
    """Keep one participant's reasoning for all debate turns."""

    name = "debater"
    policy = DEBATER_POLICY
    prompt_template = "agent/debater.j2"
