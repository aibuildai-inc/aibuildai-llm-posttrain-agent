# Phase-separated preparation and model validation

Use separate evidence for separate phases. This package runs a preparation Agent
without a graphics processing unit (GPU), then a model-validation Agent with one
GPU. Environment readiness permits validation to start; only actual model patch
validation permits final success. Loading the example proves its package contract,
not that a model or grader has executed.

Review this counterexample as a regression case:

```python
prepared = await prepared_handle.result()
if not prepared.ready:
    return Failure(kind=FailureKind.UNEXPECTED, reason=prepared.reason)
# The GPU validation Action is spawned only below this gate.
```

If the preparation prompt defines `ready` as requiring a successful model-generated
patch, but forbids model loading and has no reachable delegated producer, the gate
is impossible. The reviewer must reject it and identify the earlier gate, missing
evidence, later producer and incompatible capability. Retrying preparation or
setting `ready=True` without evidence does not repair the dependency.

The accompanying package is the positive case: `environment_ready` depends only on
executable environment checks; `model_patch_validated` belongs to the later GPU
Action. A real build or import failure still blocks preparation, and a failed
model patch still blocks final success. No topology is prescribed: a delegated
child can also produce evidence if its actual grant and reachable order permit it.
