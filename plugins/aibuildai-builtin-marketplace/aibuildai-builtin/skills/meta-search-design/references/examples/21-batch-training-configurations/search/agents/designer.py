"""The role that writes the fixed training source and freezes the batch."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import DesignerInput, DesignerOutput
from .policy import DESIGNER_POLICY


class DesignerAgent(Agent[DesignerInput, DesignerOutput]):
    """Write train.py once, then propose N meaningfully different configurations."""

    name = "designer"
    policy = DESIGNER_POLICY
    prompt_template = "agent/designer.j2"
