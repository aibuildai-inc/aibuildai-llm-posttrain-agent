"""The AIBuildAI product's Search Output, final result, runtime record, and run-owned file resources."""

from pathlib import PurePosixPath

from pydantic import ConfigDict, Field, field_serializer, field_validator

from engine.base import IGNORED_MODEL_ATTR_TYPES, WorkflowBaseModel
from engine.durable_execution import FileRef, FileRoot
from engine.execution_output import SuccessfulOutput
from engine.paths import (
    DELIVERABLE_DIRNAME,
    PRIVATE_DIRNAME,
    PUBLIC_DIRNAME,
    SUBMITTER_DIRNAME,
)


def task_data() -> FileRef:
    """The run's public data: what SETUP prepared, plus the real material behind every link it authored there.

    The one door to the task for every solving role. It is an index as much as
    a directory -- a link is only as readable as its target -- so this is where
    the product says so, and the launch boundary binds what it resolves to."""
    return FileRef(root=FileRoot.RUN, relative=PUBLIC_DIRNAME, links=True)


def public_files() -> FileRef:
    """The run's public data dir as a plain writable tree.

    SETUP fills it; every other role reads it through ``task_data()``, which
    also resolves the links SETUP authored there. The two views of one
    directory are deliberate: what a solver may read is the tree AND what its
    links name, while what SETUP may write is only the tree."""
    return FileRef(root=FileRoot.RUN, relative=PUBLIC_DIRNAME)


def task_folder_files() -> FileRef:
    """The user's whole task folder, answers and all.

    Only a role that prepares or audits the run may be granted it, and never a
    role that solves the task: an answer key nobody bound is an answer key
    nobody can read."""
    return FileRef(root=FileRoot.INPUT)


def grading_files() -> FileRef:
    """The manager-only grading material: the frozen score program, the answer key, the baseline attempt."""
    return FileRef(root=FileRoot.RUN, relative=PRIVATE_DIRNAME)


def deliverable_files() -> FileRef:
    """What the run hands back."""
    return FileRef(root=FileRoot.RUN, relative=DELIVERABLE_DIRNAME)


def submitter_files() -> FileRef:
    """The SUBMITTER's own append-only record of external results."""
    return FileRef(root=FileRoot.RUN, relative=SUBMITTER_DIRNAME)


class ScoredAttempt(SuccessfulOutput):
    """One measured attempt: the number, the directory it was measured on, and its parts.

    The ONE definition of the run's scored value. The score program's own
    Output and the Search Output that selects one both ARE this shape, so a
    scored value has a single spelling and the two journaled Outputs cannot
    drift into two different versions of the same fact."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        ignored_types=IGNORED_MODEL_ATTR_TYPES,
    )

    score: float
    # The attempt directory this number was measured on, so a consumer that
    # needs the scored files reads them off the result instead of looking
    # inside the composite for the Score unit that made it.
    output_dir: str
    components: tuple[tuple[str, float], ...] = ()

    @field_validator("components", mode="before")
    @classmethod
    def _validate_components(cls, value: object) -> tuple[tuple[str, float], ...]:
        if isinstance(value, dict):
            entries = tuple(value.items())
        else:
            entries = tuple(value)  # type: ignore[arg-type]
        names = tuple(name for name, _ in entries)
        if len(names) != len(set(names)):
            raise ValueError("duplicate component names are not allowed")
        return tuple(sorted(entries))

    @field_serializer("components")
    def _serialize_components(
        self, components: tuple[tuple[str, float], ...]
    ) -> dict[str, float]:
        return dict(components)


class SearchOutput(ScoredAttempt):
    """The scored result selected by a Search, before delivery."""


class DeliveryRecord(WorkflowBaseModel):
    """What one run handed the user: the delivered files and one sentence about them.

    The ONE definition of the delivery shape. The Finalizer produces it and the
    completed Search returns the same record, so the two boundaries cannot
    drift apart and reject each other's value. Frozen like the terminal
    Outputs that carry it: both may hold the same instance, so a mutable
    nested record would let one journaled Output change the other with no
    event behind it."""

    model_config = ConfigDict(frozen=True)

    relative_paths: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "Every file you wrote inside the deliverable directory, as paths "
            "relative to that directory. At least one is required: an empty "
            "deliverable means the run handed the user nothing."
        ),
    )

    @field_validator("relative_paths")
    @classmethod
    def _paths_stay_inside_the_deliverable(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        """A delivered name is relative and does not climb out of the directory.

        No directory is needed to know this, so it is asked here rather than by
        a verifier: an absolute name, or one that walks up out of its base, is
        not a path relative to the deliverable whatever the deliverable is."""
        escaping = [
            name
            for name in v
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
        ]
        if escaping:
            raise ValueError(
                f"names files outside the deliverable directory: {escaping}. The "
                "deliverable is what the user receives, so every delivered file "
                "must be a relative path that stays inside it"
            )
        return v

    summary: str = Field(
        min_length=1,
        description=(
            "Short plain description of what you delivered and how it matches "
            "the run's README: what each delivered file holds, and anything you "
            "had to reconstruct from the selected result's output. At least one "
            "character is required: this sentence is the run's recorded result, "
            "so an empty summary leaves the run with nothing to record."
        ),
    )


class SearchResult(SuccessfulOutput):
    """The final product result handed to the user.

    The Search's own Output combined with the independent delivery the
    Finalizer wrote from it."""

    model_config = ConfigDict(frozen=True)
    output: SearchOutput
    delivery: DeliveryRecord


class RunSuspendRequest(WorkflowBaseModel):
    """One saved request to stop a live run."""

    since_s: float
    reason: str


class AIBuildAISearchRuntimeRecord(WorkflowBaseModel):
    """The product Search's recorded phase, result selection, and suspend request."""

    exploration_started_at_s: float | None = None
    exploration_finished_at_s: float | None = None
    exploration_cost_usd: float | None = None
    selected_output: SearchOutput | None = None
    run_suspend_requested: RunSuspendRequest | None = None
