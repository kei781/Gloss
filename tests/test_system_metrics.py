import sys
import json
import time
import unittest

from gloss.system import CpuTimes, cpu_percent_between, npu_percent_from_counter_json


class NpuCounterTest(unittest.TestCase):
    def test_filters_intel_npu_luid_and_compute_engine(self) -> None:
        samples = [
            {"InstanceName": "pid_28192_luid_0x00000000_0x00011b60_phys_0_eng_0_engtype_compute", "CookedValue": 88.1},
            {"InstanceName": "pid_31960_luid_0x00000000_0x000116de_phys_0_eng_0_engtype_compute", "CookedValue": 1.5},
            {"InstanceName": "pid_28192_luid_0x00000000_0x00011b60_phys_0_eng_1_engtype_3d", "CookedValue": 2.0},
        ]
        self.assertEqual(npu_percent_from_counter_json(json.dumps(samples), 0x11B60), 88.1)
        self.assertIsNone(npu_percent_from_counter_json(json.dumps(samples), 0x99999))


class CpuPercentBetweenTest(unittest.TestCase):
    def test_half_busy(self) -> None:
        first = CpuTimes(idle=0, kernel=0, user=0)
        # kernel includes idle: total = kernel + user = 100, busy = 100 - 50
        second = CpuTimes(idle=50, kernel=60, user=40)

        self.assertEqual(cpu_percent_between(first, second), 50.0)

    def test_fully_idle(self) -> None:
        first = CpuTimes(idle=0, kernel=0, user=0)
        second = CpuTimes(idle=100, kernel=100, user=0)

        self.assertEqual(cpu_percent_between(first, second), 0.0)

    def test_zero_delta_returns_none(self) -> None:
        times = CpuTimes(idle=10, kernel=10, user=10)

        self.assertIsNone(cpu_percent_between(times, times))

    def test_clamped_to_valid_range(self) -> None:
        first = CpuTimes(idle=100, kernel=100, user=0)
        second = CpuTimes(idle=50, kernel=150, user=0)  # idle went "backwards"

        value = cpu_percent_between(first, second)
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)


@unittest.skipUnless(sys.platform == "win32", "Windows-only sampler")
class WindowsSystemSamplerTest(unittest.TestCase):
    def test_live_sample_ranges(self) -> None:
        from gloss.system import WindowsSystemSampler

        sampler = WindowsSystemSampler()
        first = sampler.sample()
        self.assertIsNone(first.cpu_percent)
        time.sleep(0.2)
        second = sampler.sample()

        self.assertIsNotNone(second.cpu_percent)
        self.assertGreaterEqual(second.cpu_percent, 0.0)
        self.assertLessEqual(second.cpu_percent, 100.0)
        self.assertGreater(second.ram_total_mb, 0.0)
        self.assertGreater(second.ram_used_mb, 0.0)
        self.assertLessEqual(second.ram_used_mb, second.ram_total_mb)


if __name__ == "__main__":
    unittest.main()
