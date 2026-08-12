#!/usr/bin/env python3
"""perf_stats.py 单元测试。运行: python3 -m unittest test_perf_stats -v"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

import perf_stats


def make_record(**overrides):
    r = {
        "timestamp": "2026-08-12T01:00:00.000Z",
        "provider_id": "p",
        "model_id": "m",
        "tokens_input": 100,
        "tokens_output": 10,
        "tokens_reasoning": 20,
        "cache_read": 30,
        "cache_write": 40,
        "cost": 0.01,
        "ttft_ms": 500,
        "total_ms": 2000,
        "generation_ms": 1500,
        "tpot_ms": 150.0,
        "finish": "stop",
        "error": None,
        "user_prompt": "hi",
        "output_text": "hello",
    }
    r.update(overrides)
    return r


class TestLoadMetrics(unittest.TestCase):
    def test_load_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "metrics-2026-08-12.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(make_record()) + "\n")
                f.write("not-json\n")
                f.write("\n")
                f.write(json.dumps(make_record(finish="cancelled")) + "\n")
            records = perf_stats.load_metrics(d, "2026-08-12")
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["model_id"], "m")

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(perf_stats.load_metrics(d, "2026-01-01"))


class TestCancelled(unittest.TestCase):
    def test_finish_cancelled(self):
        self.assertTrue(perf_stats.is_cancelled(make_record(finish="cancelled")))

    def test_finish_error(self):
        self.assertTrue(perf_stats.is_cancelled(make_record(finish="error")))

    def test_error_field(self):
        self.assertTrue(perf_stats.is_cancelled(make_record(finish="stop", error="boom")))

    def test_old_data_fallback(self):
        r = make_record(finish=None, tokens_input=0, tokens_output=0, output_text="")
        self.assertTrue(perf_stats.is_cancelled(r))

    def test_normal_stop_not_cancelled(self):
        self.assertFalse(perf_stats.is_cancelled(make_record()))


class TestAggregate(unittest.TestCase):
    def test_sums_and_excludes_cancelled(self):
        records = [
            make_record(provider_id="p", model_id="m", tokens_input=100, tokens_output=10, cost=0.01, ttft_ms=500, total_ms=2000, tpot_ms=100),
            make_record(provider_id="p", model_id="m", tokens_input=200, tokens_output=20, cost=0.02, ttft_ms=1500, total_ms=4000, tpot_ms=200),
            make_record(provider_id="p", model_id="m", finish="cancelled", tokens_input=999, tokens_output=0, cost=0.99),
        ]
        by = perf_stats.aggregate(records)
        self.assertEqual(len(by), 1)
        m = by["p/m"]
        self.assertEqual(m["count"], 2)
        self.assertEqual(m["tokens_in"], 300)
        self.assertEqual(m["tokens_out"], 30)
        self.assertAlmostEqual(m["cost"], 0.03)
        self.assertEqual(m["ttfts"], [500, 1500])
        self.assertEqual(m["totals"], [2000, 4000])
        self.assertEqual(m["tpots"], [100, 200])

    def test_model_filter(self):
        records = [
            make_record(model_id="deepseek-v4"),
            make_record(model_id="qwen-max"),
        ]
        by = perf_stats.aggregate(records, model_filter="deepseek")
        self.assertEqual(len(by), 1)
        self.assertEqual(next(iter(by)), "p/deepseek-v4")


class TestHelpers(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(perf_stats.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(perf_stats.percentile([1, 2, 3, 4], 0.99), 3.97)
        self.assertEqual(perf_stats.percentile([], 0.5), 0)
        self.assertEqual(perf_stats.percentile([5], 0.99), 5)
        self.assertEqual(perf_stats.percentile([1608, 4723], 0.5), 3165.5)

    def test_avg(self):
        self.assertEqual(perf_stats.avg([1, 2, 3]), 2)
        self.assertEqual(perf_stats.avg([]), 0)

    def test_fmt_ms(self):
        self.assertEqual(perf_stats.fmt_ms(500), "500ms")
        self.assertEqual(perf_stats.fmt_ms(1500), "1.500s")
        self.assertEqual(perf_stats.fmt_ms(0), "-")
        self.assertEqual(perf_stats.fmt_ms(None), "-")

    def test_fmt_tokens(self):
        self.assertEqual(perf_stats.fmt_tokens(1234), "1,234")
        self.assertEqual(perf_stats.fmt_tokens(0), "0")
        self.assertEqual(perf_stats.fmt_tokens(None), "0")

    def test_fmt_cost(self):
        self.assertEqual(perf_stats.fmt_cost(0), "$0")
        self.assertEqual(perf_stats.fmt_cost(0.000123), "$0.000123")
        self.assertEqual(perf_stats.fmt_cost(0.1234), "$0.1234")

    def test_trunc(self):
        self.assertEqual(perf_stats.trunc("abc", 5), "abc")
        self.assertEqual(perf_stats.trunc("a\nb", 5), "a b")
        self.assertEqual(perf_stats.trunc("abcdefg", 5), "ab...")

    def test_fmt_local_ts(self):
        self.assertEqual(perf_stats.fmt_local_ts(None), "N/A")
        self.assertEqual(perf_stats.fmt_local_ts(""), "N/A")
        self.assertEqual(perf_stats.fmt_local_ts(1234567890), "1234567890")
        self.assertEqual(perf_stats.fmt_local_ts("bad-timestamp"), "bad-timestamp")


class TestListDates(unittest.TestCase):
    def test_list_available_dates(self):
        with tempfile.TemporaryDirectory() as d:
            for f in ["metrics-2026-08-11.jsonl", "metrics-2026-08-12.jsonl", "metrics-nope.jsonl", "other.txt"]:
                with open(os.path.join(d, f), "w", encoding="utf-8") as fh:
                    fh.write("{}")
            dates = perf_stats.list_available_dates(d)
            self.assertEqual(dates, ["2026-08-11", "2026-08-12"])


if __name__ == "__main__":
    unittest.main()