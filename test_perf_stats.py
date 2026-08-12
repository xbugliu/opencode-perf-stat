#!/usr/bin/env python3
"""perf_stats.py 单元测试。运行: python3 -m unittest test_perf_stats -v"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

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
        self.assertEqual(perf_stats.trunc(None, 5), "")
        self.assertEqual(perf_stats.trunc({"a": 1}, 5), "{'...")

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


class TestDefaultDbPath(unittest.TestCase):
    def test_default_db_path_falls_back_to_none(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = d
            try:
                self.assertIsNone(perf_stats.default_db_path())
            finally:
                if old is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old


class TestDbSource(unittest.TestCase):
    def _make_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE message (id text PRIMARY KEY, session_id text, time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL)")
        conn.execute("CREATE TABLE part (id text PRIMARY KEY, message_id text NOT NULL, session_id text NOT NULL, time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL)")
        return conn

    def _insert_user(self, conn, mid, sid, created, text):
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                     (mid, sid, created, created, json.dumps({"role": "user", "time": {"created": created}})))
        conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                     (mid + "-p", mid, sid, created, created, json.dumps({"type": "text", "text": text})))

    def _insert_assistant(self, conn, mid, sid, created, completed, model, tokens, finish, parent, parts):
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                     (mid, sid, created, completed,
                      json.dumps({"role": "assistant", "modelID": model, "providerID": "p", "parentID": parent,
                                  "time": {"created": created, "completed": completed},
                                  "tokens": tokens, "finish": finish, "cost": 0.01})))
        for i, (ptype, ptc, text) in enumerate(parts):
            data = {"type": ptype, "time": {"start": ptc, "end": ptc + 100}}
            if text is not None:
                data["text"] = text
            conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                         (f"{mid}-p{i}", mid, sid, ptc, ptc + 100, json.dumps(data)))

    def test_load_db_records(self):
        conn = self._make_db()
        # user message at 10:00, assistant replies at 10:00:01
        today = datetime.now().strftime("%Y-%m-%d")
        base = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=perf_stats.LOCAL_TZ)
        t0 = int(base.timestamp() * 1000)
        self._insert_user(conn, "u1", "s1", t0, "hello")
        tokens1 = {"input": 100, "output": 50, "reasoning": 10, "cache": {"read": 40, "write": 0}}
        self._insert_assistant(conn, "a1", "s1", t0, t0 + 2000, "m1", tokens1, "stop", "u1",
                               [("reasoning", t0 + 300, "think"), ("text", t0 + 500, "hi there"), ("tool", t0 + 600, None)])
        recs = perf_stats.load_db_records(conn, today)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["model_id"], "m1")
        self.assertEqual(r["ttft_ms"], 300)
        self.assertEqual(r["total_ms"], 2000)
        self.assertEqual(r["generation_ms"], 1700)
        self.assertEqual(r["tpot_ms"], 34.0)
        self.assertEqual(r["tokens_input"], 100)
        self.assertEqual(r["cache_read"], 40)
        self.assertEqual(r["tool_calls"], 1)
        self.assertEqual(r["user_prompt"], "hello")
        self.assertEqual(r["output_text"], "hi there")
        self.assertEqual(r["reasoning_text"], "think")
        self.assertEqual(r["finish"], "stop")

    def test_load_db_records_cancelled_and_other_day(self):
        conn = self._make_db()
        today = datetime.now().strftime("%Y-%m-%d")
        base = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=perf_stats.LOCAL_TZ)
        t0 = int(base.timestamp() * 1000)
        # cancelled (no finish, no tokens, no output)
        self._insert_user(conn, "u2", "s1", t0, "hi")
        self._insert_assistant(conn, "a2", "s1", t0, t0 + 1000, "m1",
                               {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                               None, "u2", [("text", t0 + 100, "")])
        # a message on a different day -> excluded
        other = (base - timedelta(days=1)).timestamp() * 1000
        self._insert_user(conn, "u3", "s1", int(other), "old")
        self._insert_assistant(conn, "a3", "s1", int(other), int(other) + 500, "m1",
                               {"input": 5, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                               "stop", "u3", [("text", int(other) + 50, "x")])
        recs = perf_stats.load_db_records(conn, today)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["finish"], "cancelled")
        self.assertEqual(recs[0]["error"], "cancelled")

    def test_error_dict_is_normalized(self):
        conn = self._make_db()
        today = datetime.now().strftime("%Y-%m-%d")
        base = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=perf_stats.LOCAL_TZ)
        t0 = int(base.timestamp() * 1000)
        self._insert_user(conn, "u1", "s1", t0, "hi")
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                     ("a1", "s1", t0, t0 + 500,
                      json.dumps({"role": "assistant", "modelID": "m1", "providerID": "p", "parentID": "u1",
                                  "time": {"created": t0, "completed": t0 + 500},
                                  "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                                  "finish": "error", "error": {"name": "MessageError", "message": "boom"}})))
        conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                     ("a1-p0", "a1", "s1", t0 + 50, t0 + 60, json.dumps({"type": "text", "text": "x"})))
        recs = perf_stats.load_db_records(conn, today)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["error"], "boom")
        self.assertTrue(perf_stats.is_cancelled(recs[0]))
        self.assertEqual(perf_stats.trunc(recs[0]["error"], 20), "boom")

    def test_list_db_dates(self):
        conn = self._make_db()
        today = datetime.now().strftime("%Y-%m-%d")
        base = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=perf_stats.LOCAL_TZ)
        self._insert_user(conn, "u1", "s1", int(base.timestamp() * 1000), "hi")
        dates = perf_stats.list_db_dates(conn)
        self.assertEqual(dates, [today])


if __name__ == "__main__":
    unittest.main()