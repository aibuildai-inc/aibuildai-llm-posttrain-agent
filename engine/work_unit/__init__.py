"""WorkUnit state, saved resources, and execution.

Two concrete WorkUnit families live in subpackages:
  - agent/    durable LLM Agents and their shared rules.
  - program/  synchronous typed Programs and their fixed process driver.

This package exports nothing: the two family facades (``agent``, ``program``)
are the authoring surface, and internal code imports ``engine.work_unit.base``
directly. Bare ``WorkUnit`` is not an authoring name -- authored units subclass
Agent or Program.
"""
