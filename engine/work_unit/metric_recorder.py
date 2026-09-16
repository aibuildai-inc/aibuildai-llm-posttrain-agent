"""Install the WorkUnit metric recorder in an execution directory."""

from pathlib import Path


_RECORDER_SOURCE = (
    Path(__file__).resolve().parent / "resources" / "aibuildai.py"
).read_text(encoding="utf-8")


def install_metric_recorder(directory: str) -> None:
    """Put the shared metric recorder where WorkUnit code can import it."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    (target / "aibuildai.py").write_text(_RECORDER_SOURCE, encoding="utf-8")
