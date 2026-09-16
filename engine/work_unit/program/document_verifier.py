"""The common document gate, as one ordinary Program verifier."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict

from engine.durable_execution import action
from engine.failure import Failure
from engine.work_unit.program.base import Program
from engine.execution_output import VerifierOutput


@dataclass(frozen=True)
class DocumentVerifierInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    # The absolute path the producing role promised to compile, and the role's
    # own artifacts dir, which is what the sandbox must open for the read.
    document_path: str
    artifacts_dir: str


class DocumentVerifierProgram(Program[DocumentVerifierInput]):
    """Accept the declared document only when it is a real PDF file.

    A role that declares ``required_pdf`` writes and compiles the document
    itself, so the whole product question here is whether the file the role
    promised exists as a plain readable PDF; whether it says anything true is
    the role's LLM verifier's judgement, not this one's."""

    name: ClassVar[str] = "document_verifier"

    @action
    def run(self) -> VerifierOutput | Failure:
        path = Path(self.input.document_path)
        # open().read(4), not read_bytes()[:4]: the latter loads the whole
        # document into memory to look at its first four bytes.
        if path.is_file():
            with path.open("rb") as handle:
                if handle.read(4) == b"%PDF":
                    return VerifierOutput(passed=True, reason="")
        return VerifierOutput(
            passed=False,
            reason=(
                f"{path} is not a PDF file. Write your document and compile it "
                f"yourself until {path} exists, then call StructuredOutput again."
            ),
        )
