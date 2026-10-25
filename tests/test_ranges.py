"""Range validation never expands or silently changes a requested interval."""

import unittest

from bobowatch.protocol import Invalid
from bobowatch.ranges import WINDOWS, overview_range


class RangeTests(unittest.TestCase):
    def test_rolling_choices_and_default(self):
        self.assertEqual(overview_range({}), {"window": 300})
        for seconds in WINDOWS:
            self.assertEqual(overview_range({"window": [str(seconds)]}), {"window": seconds})
        with self.assertRaises(Invalid):
            overview_range({"window": ["120"]})

    def test_absolute_bounds_are_exact_and_mutually_exclusive(self):
        self.assertEqual(
            overview_range({"start_ms": ["1000"], "end_ms": ["2000"]}, now_ms=2000),
            {"start_ms": 1000, "end_ms": 2000},
        )
        for query in (
            {"start_ms": ["1000"]},
            {"end_ms": ["2000"]},
            {"start_ms": ["1000"], "end_ms": ["2000"], "window": ["300"]},
            {"start_ms": ["2000"], "end_ms": ["1000"]},
            {"start_ms": ["2000"], "end_ms": ["2000"]},
            {"start_ms": ["-1"], "end_ms": ["2000"]},
            {"start_ms": ["1.5"], "end_ms": ["2000"]},
            {"start_ms": ["true"], "end_ms": ["2000"]},
            {"start_ms": ["1e3"], "end_ms": ["2000"]},
            {"start_ms": ["0"], "end_ms": ["32001"]},
        ):
            with self.subTest(query=query), self.assertRaises(Invalid):
                overview_range(query, now_ms=2000)
