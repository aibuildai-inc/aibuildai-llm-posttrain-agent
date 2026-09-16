"""Archive the resolved AgentConfig in the Run home for later startup."""

from __future__ import annotations

from pathlib import Path

from config import AgentConfig

RUN_CONFIG_FILENAME = "run_config.json"


def _config_path(run_home: str) -> Path:
    return Path(run_home) / RUN_CONFIG_FILENAME


def save_run_config(config: AgentConfig, run_home: str) -> Path:
    path = _config_path(run_home)
    config.run.require_run_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        config.model_dump_json(), encoding="utf-8"
    )
    return path


def load_run_config(run_home: str) -> AgentConfig:
    path = _config_path(run_home)
    if not path.exists():
        raise FileNotFoundError(f"archived run config not found: {path}")
    config = AgentConfig.model_validate_json(path.read_text(encoding="utf-8"))
    config.run.require_run_id()
    return config
