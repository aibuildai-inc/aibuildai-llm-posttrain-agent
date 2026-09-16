"""Recorded metrics for this WorkUnit attempt. The API works like wandb.

The framework made this module importable to the active WorkUnit. Use it to record per-step metrics; the Web Workspace draws one live curve per metric key.

    import aibuildai

    aibuildai.init()
    for i, batch in enumerate(loader):
        loss = train_step(batch)
        aibuildai.log({"train_loss": float(loss), "learning_rate": lr})
    aibuildai.finish()

That is the whole API: ``init()``, ``log(metrics, step=None)``, ``finish()``. ``init`` accepts and records arbitrary keyword arguments (``project``, ``config=...``, and so on) so wandb-style calls work unchanged. ``finish`` is also registered via ``atexit``, so a script that forgets it still closes its series. Data goes to this WorkUnit attempt's own ``metrics.jsonl``; nothing leaves the machine.

Gradient boosting hides its loop inside ``.fit()`` — read the per-round loss the library already keeps, then replay it:

    # xgboost
    result: dict = {}
    model = xgb.train(params, dtrain, evals=[(dval, "val")], evals_result=result)
    for i, v in enumerate(result["val"]["rmse"]):
        aibuildai.log({"val_rmse": float(v)}, step=i)

    # lightgbm: callbacks=[lgb.record_evaluation(result)]     then replay as above
    # catboost: model.get_evals_result()                      then replay as above
    # sklearn GBM: model.staged_score(X_val, y_val)           then replay as above

A model that does not iterate at all may skip logging entirely.
"""

from __future__ import annotations

import atexit
import json
import os
import time

_SUPPORTED = ("init", "log", "finish")

_file = None
_t0 = 0.0
_auto_step = 0
_finished = False


def _output_dir() -> str:
    """Return the metric directory that the WorkUnit launcher exported.

    The framework launches every Agent and Program with ``OUTPUT_DIR`` set to
    that attempt's own output directory. A copy of this file in another
    workspace still records metrics for the active attempt.
    """
    output_dir = os.environ.get("OUTPUT_DIR")
    if not output_dir:
        raise RuntimeError(
            "aibuildai.init: OUTPUT_DIR is not set; run this WorkUnit through "
            "the framework, which exports it."
        )
    return output_dir


def init(**kwargs: object) -> None:
    """Open one metric series. Call once before ``log``."""
    global _file, _t0, _auto_step, _finished
    out_dir = _output_dir()
    os.makedirs(out_dir, exist_ok=True)
    _file = open(
        os.path.join(out_dir, "metrics.jsonl"), "a", encoding="utf-8"
    )
    _t0 = time.time()
    _auto_step = 0
    _finished = False
    event: dict = {"event_type": "start", "metrics": {}}
    if kwargs:
        event["config"] = {key: repr(value) for key, value in kwargs.items()}
    _write(event)
    atexit.register(finish)


def log(metrics: dict, step: int | None = None) -> None:
    """Append one step of metrics. Each key becomes one live curve."""
    global _auto_step
    if _file is None:
        raise RuntimeError("aibuildai.log: call aibuildai.init() first.")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("aibuildai.log: metrics must be a non-empty dict.")
    if step is None:
        step = _auto_step
    _auto_step = step + 1
    values = {key: float(value) for key, value in metrics.items()}
    _write({"step": step, "event_type": "step", "metrics": values})


def finish() -> None:
    """Close the series with an empty completion event."""
    global _file, _finished
    if _file is None or _finished:
        return
    _finished = True
    _write({"event_type": "finish", "metrics": {}})
    _file.close()
    _file = None


def _write(event: dict) -> None:
    if _file is None:
        raise RuntimeError("aibuildai: no open trace; call aibuildai.init() first.")
    event["elapsed_seconds"] = time.time() - _t0
    _file.write(json.dumps(event) + "\n")
    _file.flush()


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"aibuildai.{name} is not part of this module. The supported API is "
        f"{_SUPPORTED}: wandb-style usage with init(), log(metrics, step=None), "
        f"and finish()."
    )
