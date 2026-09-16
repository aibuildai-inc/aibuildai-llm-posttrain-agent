"""Input of the judge-calibrated preference Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JudgeCalibratedPreferenceSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every role serves.")
    data_dir: str = Field(description="Read-only directory holding the raw task corpus.")
    eval_script: str = Field(description="Absolute path to the fixed, official evaluation script.")
    judge_script: str = Field(description="Absolute path to the script that scores completions with the judge model.")
    sample_script: str = Field(description="Absolute path to the script that draws candidate completions from a checkpoint.")
    soup_script: str = Field(description="Absolute path to the script that averages checkpoint weights into one soup directory.")
    dpo_module: str = Field(description="Module run as `python -m <dpo_module>` to train on chosen-rejected pairs.")
    raft_module: str = Field(description="Module run as `python -m <raft_module>` to fine-tune on the top-ranked completion only.")
    judge_model: str = Field(description="Absolute path or identifier of the model that ranks completions.")
    candidates_per_prompt: int = Field(default=4, ge=2, le=16, description="Completions ranked per prompt.")
    min_judge_agreement: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of held-out graded pairs the judge must call the same way as the "
            "grader. Below it the preference branch is abandoned, not weakened."
        ),
    )
    dpo_min_margin: float = Field(
        default=0.15,
        gt=0.0,
        description=(
            "Mean judge score gap between the chosen and rejected sides above which the "
            "pairs are separated enough for a pairwise loss."
        ),
    )
    min_pairs: int = Field(default=200, ge=1, description="Preference pairs below which no training round starts.")
    min_branch_seconds: int = Field(
        default=3600,
        ge=60,
        description="The training branch starts only while the exploration remainder pays for one of this size.",
    )
    parent_wall_clock_seconds: int = Field(default=5400, ge=60)
    judge_wall_clock_seconds: int = Field(default=1800, ge=60, description="Wall clock of each judge role.")
    trainer_wall_clock_seconds: int = Field(default=5400, ge=60, description="Wall clock of the trainer role: training, two evaluations, and the soup.")
