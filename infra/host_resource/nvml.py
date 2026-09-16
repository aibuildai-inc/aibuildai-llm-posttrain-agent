"""Typed, bounded GPU facts from a leaf NVML process."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, NamedTuple, cast

import pynvml

_MIB = 1 << 20
_OPTIONAL = {pynvml.NVML_ERROR_NOT_SUPPORTED, pynvml.NVML_ERROR_FUNCTION_NOT_FOUND}
_NO_GPU_ERRORS = {
    pynvml.NVML_ERROR_LIBRARY_NOT_FOUND, pynvml.NVML_ERROR_DRIVER_NOT_LOADED,
    pynvml.NVML_ERROR_GPU_NOT_FOUND,
}
_LINK_END = {*_OPTIONAL, pynvml.NVML_ERROR_INVALID_ARGUMENT}
_ANCESTOR_RANK = {
    pynvml.NVML_TOPOLOGY_INTERNAL: 4, pynvml.NVML_TOPOLOGY_SINGLE: 4,
    pynvml.NVML_TOPOLOGY_MULTIPLE: 3, pynvml.NVML_TOPOLOGY_HOSTBRIDGE: 2,
    pynvml.NVML_TOPOLOGY_NODE: 1, pynvml.NVML_TOPOLOGY_SYSTEM: 0,
}


class NvmlQueryError(RuntimeError):
    """A bounded NVML process could not answer."""


class GpuStats(NamedTuple):
    index: int
    free_mib: int
    util_pct: int | None


class GpuTelemetry(NamedTuple):
    """The canonical product snapshot of one physical GPU."""
    index: int
    name: str
    temperature_c: int | None
    power_w: float | None
    power_cap_w: float | None
    memory_used_mb: int
    memory_total_mb: int
    utilization_pct: int | None
    memory_bw_pct: int | None


def _value(call: Any, *args: object, allowed: set[int] = _OPTIONAL) -> Any | None:
    try:
        return call(*args)
    except pynvml.NVMLError as error:
        if getattr(error, "value", None) in allowed:
            return None
        raise


def _link_value(call: Any, handle: Any, link: int, *args: object) -> Any | None:
    return _value(call, handle, link, *args, allowed=_LINK_END)


def _handles() -> list[tuple[int, Any]]:
    count = pynvml.nvmlDeviceGetCount()
    found = []
    for index in range(count):
        try:
            found.append((index, pynvml.nvmlDeviceGetHandleByIndex(index)))
        except pynvml.NVMLError as error:
            if getattr(error, "value", None) != pynvml.NVML_ERROR_NO_PERMISSION:
                raise
    if count and not found:
        raise NvmlQueryError(f"NVML reported {count} GPU(s), but none are accessible")
    return found


def _device_rows(query: str) -> list[GpuStats] | list[GpuTelemetry]:
    rows = []
    for index, handle in _handles():
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        usage = _value(pynvml.nvmlDeviceGetUtilizationRates, handle)
        if query == "stats":
            util = None if usage is None else int(usage.gpu)
            rows.append(GpuStats(index, int(memory.free) // _MIB, util))
            continue
        temperature = _value(
            pynvml.nvmlDeviceGetTemperatureV, handle, pynvml.NVML_TEMPERATURE_GPU,
            allowed={*_OPTIONAL, pynvml.NVML_ERROR_DEPRECATED},
        )
        if temperature is None:
            temperature = _value(
                pynvml.nvmlDeviceGetTemperature, handle, pynvml.NVML_TEMPERATURE_GPU,
                allowed={*_OPTIONAL, pynvml.NVML_ERROR_DEPRECATED},
            )
        power = _value(pynvml.nvmlDeviceGetPowerUsage, handle)
        power_cap = _value(pynvml.nvmlDeviceGetPowerManagementLimit, handle)
        rows.append(
            GpuTelemetry(
                index,
                pynvml.nvmlDeviceGetName(handle),
                None if temperature is None else int(temperature),
                None if power is None else float(power) / 1000,
                None if power_cap is None else float(power_cap) / 1000,
                int(memory.used) // _MIB,
                int(memory.total) // _MIB,
                None if usage is None else int(usage.gpu),
                None if usage is None else int(usage.memory),
            )
        )
    return rows


def _topology() -> list[list[int]]:
    handles = _handles()
    gpu_by_pci = {
        pynvml.nvmlDeviceGetPciInfo(handle).busId.lower(): index
        for index, handle in handles
    }
    ranks, direct = {}, set()
    switches = {index: set() for index, _ in handles}
    for position, (left, handle) in enumerate(handles):
        for right, other in handles[position + 1 :]:
            ancestor = _value(
                pynvml.nvmlDeviceGetTopologyCommonAncestor,
                handle,
                other,
            )
            if ancestor is not None:
                ranks[frozenset((left, right))] = _ANCESTOR_RANK.get(ancestor, 0)
        for link in range(pynvml.NVML_NVLINK_MAX_LINKS):
            active = _link_value(pynvml.nvmlDeviceGetNvLinkState, handle, link)
            if active is None:
                break
            if active != pynvml.NVML_FEATURE_ENABLED:
                continue
            remote_info = _link_value(
                pynvml.nvmlDeviceGetNvLinkRemotePciInfo, handle, link
            )
            if remote_info is None:
                continue
            remote = remote_info.busId.lower()
            peer = gpu_by_pci.get(remote)
            if peer is not None and peer != left:
                p2p = _link_value(
                    pynvml.nvmlDeviceGetNvLinkCapability, handle, link,
                    pynvml.NVML_NVLINK_CAP_P2P_SUPPORTED,
                )
                if p2p:
                    direct.add((left, peer))
                continue
            remote_type = _link_value(
                pynvml.nvmlDeviceGetNvLinkRemoteDeviceType, handle, link
            )
            if remote_type == pynvml.NVML_NVLINK_DEVICE_TYPE_SWITCH:
                switches[left].add(remote)
    for position, (left, _) in enumerate(handles):
        for right, _ in handles[position + 1 :]:
            if (
                (left, right) in direct and (right, left) in direct
                or switches[left] & switches[right]
            ):
                ranks[frozenset((left, right))] = 5
    return [[*pair, rank] for pair, rank in ranks.items()]


def _run_query(query: str) -> object:
    if query == "driver":
        version = int(pynvml.nvmlSystemGetCudaDriverVersion())
        return f"{version // 1000}.{(version % 1000) // 10}"
    if query == "minors":
        return {
            index: int(pynvml.nvmlDeviceGetMinorNumber(handle))
            for index, handle in _handles()
        }
    if query in {"stats", "telemetry"}:
        return _device_rows(query)
    return _topology()


def main(query: str) -> int:
    if query not in {"minors", "stats", "telemetry", "topology", "driver"}:
        raise SystemExit(f"unknown NVML query: {query}")
    try:
        pynvml.nvmlInit()
        try:
            result = _run_query(query)
        finally:
            pynvml.nvmlShutdown()
    except (pynvml.NVMLError, NvmlQueryError) as error:
        value = getattr(error, "value", None)
        if value not in _NO_GPU_ERRORS:
            print(f"{type(error).__name__}({value}): {error}", file=sys.stderr)
            return 2
        result = {} if query == "minors" else None if query == "driver" else []
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _decode(query: str, returncode: int, stdout: str, stderr: str) -> object:
    if returncode:
        raise NvmlQueryError(
            f"NVML {query} query failed (exit {returncode}): {stderr.strip()}"
        )
    from pydantic import TypeAdapter, ValidationError

    result_types = {
        "minors": dict[int, int],
        "stats": list[GpuStats],
        "telemetry": list[GpuTelemetry],
        "topology": list[tuple[int, int, int]],
        "driver": str | None,
    }
    try:
        result = TypeAdapter(result_types[query]).validate_json(stdout, strict=True)
    except ValidationError as error:
        raise NvmlQueryError(f"NVML {query} returned invalid JSON: {error}") from error
    if query != "topology":
        return result
    topology = {frozenset((left, right)): rank for left, right, rank in result}
    if any(len(pair) != 2 or rank not in range(6) for pair, rank in topology.items()):
        raise NvmlQueryError("NVML topology returned an invalid pair or rank")
    if len(topology) != len(result):
        raise NvmlQueryError("NVML topology returned a duplicate pair")
    return topology or None


async def _query_for_run(query: str, timeout_s: float) -> object:
    from infra.process.command import run_captured

    argv = [sys.executable, __file__, query]
    result = await run_captured(argv, timeout_s)
    return _decode(query, result.returncode, result.stdout, result.stderr)


async def query_gpu_minors(timeout_s: float) -> dict[int, int]:
    return cast(dict[int, int], await _query_for_run("minors", timeout_s))


async def query_gpu_stats(timeout_s: float) -> list[GpuStats]:
    return cast(list[GpuStats], await _query_for_run("stats", timeout_s))


async def query_topology(timeout_s: float) -> dict[frozenset[int], int] | None:
    return cast(dict[frozenset[int], int] | None, await _query_for_run("topology", timeout_s))


async def query_cuda_driver_version(timeout_s: float) -> str | None:
    return cast(str | None, await _query_for_run("driver", timeout_s))


def query_gpu_telemetry(timeout_s: float) -> list[GpuTelemetry]:
    return cast(list[GpuTelemetry], asyncio.run(_query_for_run("telemetry", timeout_s)))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: nvml.py QUERY")
    raise SystemExit(main(sys.argv[1]))
