"""The run's metric contract record — pure data.

Setup declares the metric name and optimization direction in its own Output; the run records them as the journaled metric contract (``MetricContractRecorded`` -> ``RunState.metric_contract``) and every later role reads that journaled fact. This module holds only the shape roles receive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MetricContractInput:
    metric_name: str
    metric_direction: Literal["max", "min"]
