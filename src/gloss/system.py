from __future__ import annotations

import ctypes
from dataclasses import dataclass
import json
import re
import subprocess
import sys


class SystemMetricsError(RuntimeError):
    pass


@dataclass(frozen=True)
class CpuTimes:
    """Cumulative 100ns ticks from GetSystemTimes (kernel includes idle)."""

    idle: int
    kernel: int
    user: int


@dataclass(frozen=True)
class SystemSample:
    cpu_percent: float | None
    ram_used_mb: float
    ram_total_mb: float
    ram_percent: float
    npu_percent: float | None = None


def npu_percent_from_counter_json(payload: str, luid: int) -> float | None:
    """Sum compute-engine samples for one LUID, excluding Arc GPU engines."""
    try:
        rows = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return None
    total = 0.0
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance = str(row.get("InstanceName") or row.get("inst") or "").lower()
        match = re.search(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", instance)
        if not match or "engtype_compute" not in instance:
            continue
        instance_luid = (int(match.group(1), 16) << 32) | int(match.group(2), 16)
        if instance_luid != luid:
            continue
        value = row.get("CookedValue", row.get("val"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += max(0.0, float(value))
            found = True
    return min(100.0, total) if found else None


class WindowsNpuSampler:
    """Read Windows GPU Engine PDH counters for the Intel AI Boost LUID."""

    def __init__(self, luid: int):
        self.luid = luid

    def sample(self) -> float | None:
        command = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$s=(Get-Counter '\\GPU Engine(*)\\Utilization Percentage' -MaxSamples 1).CounterSamples; "
            "@($s | Select-Object InstanceName,CookedValue) | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True, text=True, encoding="utf-8", timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return npu_percent_from_counter_json(result.stdout, self.luid)


def cpu_percent_between(first: CpuTimes, second: CpuTimes) -> float | None:
    idle = second.idle - first.idle
    kernel = second.kernel - first.kernel
    user = second.user - first.user
    total = kernel + user
    if total <= 0:
        return None
    busy = total - idle
    return max(0.0, min(100.0, busy * 100.0 / total))


class WindowsSystemSampler:
    """CPU%/RAM via Win32 calls (ctypes) — no psutil wheel needed (ADR-017).

    CPU% is computed between consecutive sample() calls; the first call
    returns cpu_percent=None.
    """

    def __init__(self, *, npu_luid: int | None = None) -> None:
        if sys.platform != "win32":
            raise SystemMetricsError("WindowsSystemSampler requires Windows.")
        self._kernel32 = ctypes.windll.kernel32
        self._last_times: CpuTimes | None = None
        self._npu = WindowsNpuSampler(npu_luid) if npu_luid is not None else None

    def sample(self) -> SystemSample:
        times = self._cpu_times()
        cpu = (
            cpu_percent_between(self._last_times, times)
            if self._last_times is not None
            else None
        )
        self._last_times = times
        used_mb, total_mb, percent = self._memory_status()
        return SystemSample(
            cpu_percent=cpu,
            ram_used_mb=used_mb,
            ram_total_mb=total_mb,
            ram_percent=percent,
            npu_percent=self._npu.sample() if self._npu is not None else None,
        )

    def _cpu_times(self) -> CpuTimes:
        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32),
            ]

        idle = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        ok = self._kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            raise SystemMetricsError("GetSystemTimes failed.")

        def ticks(value: "FILETIME") -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        return CpuTimes(idle=ticks(idle), kernel=ticks(kernel), user=ticks(user))

    def _memory_status(self) -> tuple[float, float, float]:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not self._kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise SystemMetricsError("GlobalMemoryStatusEx failed.")
        total_mb = status.ullTotalPhys / (1024 * 1024)
        used_mb = (status.ullTotalPhys - status.ullAvailPhys) / (1024 * 1024)
        percent = float(status.dwMemoryLoad)
        return used_mb, total_mb, percent
