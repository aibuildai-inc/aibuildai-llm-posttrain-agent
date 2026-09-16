"""Input of the model-export-and-compatibility-validation Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExportCompatibilitySearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim deployment objective every role serves.")
    source_checkpoint_path: str = Field(description="Absolute path of the immutable trained checkpoint.")
    toolchain_dir: str = Field(description="Directory holding the fixed export.py and validate.py scripts.")
    offered_targets: tuple[str, ...] = Field(min_length=1, description="Export targets the strategist may pick.")
    offered_precision_profiles: tuple[str, ...] = Field(min_length=1)
    offered_shape_profiles: tuple[str, ...] = Field(min_length=1)
    offered_converter_options: tuple[str, ...] = Field(description="Closed set of converter flags the strategist may enable.")
    offered_compatibility_profiles: tuple[str, ...] = Field(min_length=1, description="Environment profiles the strategist may schedule.")
    offered_tolerance_profiles: tuple[str, ...] = Field(min_length=1)
    mandatory_environment_profiles: tuple[str, ...] = Field(min_length=1, description="Profiles that must pass before any package is accepted.")
    max_rounds: int = Field(default=3, ge=1, le=6, description="Hard budget of plan-export-validate rounds.")
    strategy_wall_clock_seconds: int = Field(default=600, ge=60)
    export_wall_clock_seconds: int = Field(default=1800, ge=60, description="Wall clock of the export role.")
    validation_wall_clock_seconds: int = Field(default=900, ge=60, description="Wall clock of each validation role.")
    review_wall_clock_seconds: int = Field(default=600, ge=60)
