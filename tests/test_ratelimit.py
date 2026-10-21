import unittest

from workshop import ratelimit


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        ratelimit.reset()
        self.addCleanup(ratelimit.reset)
        self.c = Clock()

    def acquire(self, provider, **kw):
        ratelimit.acquire(provider, clock=self.c, sleep=self.c.sleep, **kw)

    def test_zero_or_missing_rpm_is_unlimited(self):
        for _ in range(500):
            self.acquire({"base_url": "http://a", "rpm": 0})
            self.acquire({"base_url": "http://a"})
        self.assertEqual(self.c.slept, [])

    def test_calls_within_the_limit_do_not_wait(self):
        for _ in range(5):
            self.acquire({"base_url": "http://a", "rpm": 5})
        self.assertEqual(self.c.slept, [])

    def test_short_overflow_waits_until_the_window_frees(self):
        p = {"base_url": "http://a", "rpm": 2}
        self.acquire(p)
        self.c.now = 50
        self.acquire(p)
        self.acquire(p)                      # the first call leaves the window in ~10s
        self.assertEqual(len(self.c.slept), 1)
        self.assertAlmostEqual(self.c.slept[0], 10.01, places=2)

    def test_long_overflow_fails_fast_so_fallback_can_take_over(self):
        p = {"base_url": "http://a", "rpm": 1}
        self.acquire(p)
        with self.assertRaises(ratelimit.RateLimited) as ctx:
            self.acquire(p)
        self.assertIn("1 requests/minute", str(ctx.exception))
        self.assertEqual(self.c.slept, [])

    def test_services_are_limited_independently(self):
        self.acquire({"base_url": "http://a", "rpm": 1})
        self.acquire({"base_url": "http://b", "rpm": 1})
        self.assertEqual(self.c.slept, [])

    def test_negative_rpm_is_treated_as_unlimited(self):
        for _ in range(5):
            self.acquire({"base_url": "http://a", "rpm": -3})


if __name__ == "__main__":
    unittest.main()
