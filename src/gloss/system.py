from __future__ import annotations

import ctypes
from dataclasses import dataclass
import re
import sys

from gloss.log import log


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


def npu_percent_from_rows(rows: list[dict], luid: int) -> float | None:
    """Sum compute-engine samples for one LUID across all processes."""
    total = 0.0
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance = str(row.get("InstanceName") or "").lower()
        match = re.search(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", instance)
        if not match or "engtype_compute" not in instance:
            continue
        instance_luid = (int(match.group(1), 16) << 32) | int(match.group(2), 16)
        if instance_luid != luid:
            continue
        value = row.get("CookedValue")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += max(0.0, float(value))
            found = True
    return min(100.0, total) if found else None


class _PdhValueUnion(ctypes.Union):
    _fields_ = [("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_int64)]


class _PdhValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_uint32), ("value", _PdhValueUnion)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhValue)]


class WindowsNpuSampler:
    """Read NPU GPU Engine counters through PDH without launching a process per tick."""

    def __init__(self, luid: int):
        self.luid = luid
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        self._pdh = None
        self._missing_luid_logged = False
        if sys.platform != "win32":
            return
        try:
            pdh = ctypes.WinDLL("pdh.dll")
            pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
            pdh.PdhOpenQueryW.restype = ctypes.c_uint32
            pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
            pdh.PdhAddEnglishCounterW.restype = ctypes.c_uint32
            pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
            pdh.PdhCollectQueryData.restype = ctypes.c_uint32
            pdh.PdhGetFormattedCounterArrayW.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
            ]
            pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_uint32
            pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
            pdh.PdhCloseQuery.restype = ctypes.c_uint32
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                return
            self._pdh = pdh
            if pdh.PdhAddEnglishCounterW(
                self._query, r"\GPU Engine(*)\Utilization Percentage", 0,
                ctypes.byref(self._counter),
            ) != 0:
                self.close()
                return
            pdh.PdhCollectQueryData(self._query)
        except (AttributeError, OSError):
            self.close()

    def sample(self) -> float | None:
        if self._pdh is None or not self._counter:
            return None
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        size = ctypes.c_uint32(0)
        count = ctypes.c_uint32(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            self._counter, 0x00000200, ctypes.byref(size), ctypes.byref(count), None,
        )
        if status != 0x800007D2 or size.value == 0:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            self._counter, 0x00000200, ctypes.byref(size), ctypes.byref(count), buffer,
        )
        if status != 0:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhItem))
        rows = [
            {"InstanceName": items[index].szName, "CookedValue": items[index].FmtValue.value.doubleValue}
            for index in range(count.value)
            if items[index].FmtValue.CStatus in (0, 1)
        ]
        value = npu_percent_from_rows(rows, self.luid)
        if value is None and rows and not self._missing_luid_logged:
            observed = sorted({
                (int(match.group(1), 16) << 32) | int(match.group(2), 16)
                for row in rows
                if "engtype_compute" in str(row["InstanceName"]).lower()
                if (match := re.search(
                    r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)",
                    str(row["InstanceName"]).lower(),
                ))
            })
            if observed:
                log(
                    "configured NPU LUID not found in compute counters",
                    level="WARN", configured=hex(self.luid),
                    observed=",".join(hex(item) for item in observed),
                )
                self._missing_luid_logged = True
        return value

    def close(self) -> None:
        if self._pdh is not None and self._query:
            self._pdh.PdhCloseQuery(self._query)
        self._pdh = None
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()

    def __del__(self) -> None:
        self.close()


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
