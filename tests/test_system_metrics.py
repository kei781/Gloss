import sys
import time
import unittest

from gloss.system import CpuTimes, cpu_percent_between


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
