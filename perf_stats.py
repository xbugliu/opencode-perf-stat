#!/usr/bin/env python3
"""OpenCode 性能统计 metrics 分析脚本

用法:
  python3 perf_stats.py [date] [--dir PERF_DIR] [--detail] [--model MODEL] [--list]

参数:
  date            日期，格式 YYYY-MM-DD，默认今天
  --dir           metrics 文件所在目录，默认 ~/.opencode/perf
  --detail        额外打印单笔明细和用户输入摘要（默认只打印汇总）
  --model         只看指定模型（子串匹配）
  --list          列出所有可用日期

示例:
  python3 perf_stats.py
  python3 perf_stats.py 2026-08-12
  python3 perf_stats.py 2026-08-12 --detail
  python3 perf_stats.py 2026-08-12 --model deepseek
  python3 perf_stats.py --list
"""

import json
import os
import sys
import glob
import argparse
from datetime import datetime
from collections import defaultdict

LOCAL_TZ = datetime.now().astimezone().tzinfo


# ─── helpers ───────────────────────────────────────────────────────────────────

def default_perf_dir():
    return os.path.join(os.path.expanduser("~"), ".opencode", "perf")


def list_available_dates(perf_dir):
    pattern = os.path.join(perf_dir, "metrics-*.jsonl")
    files = glob.glob(pattern)
    dates = []
    prefix, suffix = "metrics-", ".jsonl"
    for f in files:
        basename = os.path.basename(f)
        if not basename.startswith(prefix) or not basename.endswith(suffix):
            continue
        date_part = basename[len(prefix):-len(suffix)]
        if len(date_part) == 10 and date_part[4] == "-":
            dates.append(date_part)
    dates.sort()
    return dates


def load_metrics(perf_dir, date_str):
    filepath = os.path.join(perf_dir, f"metrics-{date_str}.jsonl")
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def fmt_ms(ms):
    if ms is None or ms <= 0:
        return "-"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms/1000:.3f}s"


def fmt_tpot(ms):
    if ms is None or ms <= 0:
        return "-"
    return f"{ms:.1f}ms"


def fmt_tokens(n):
    if not n:
        return "0"
    return f"{n:,}"


def fmt_cost(c):
    if not c or c == 0:
        return "$0"
    if c < 0.01:
        return f"${c:.6f}"
    return f"${c:.4f}"


def percentile(sorted_arr, p):
    """Linear-interpolation percentile on a sorted array."""
    if not sorted_arr:
        return 0
    if len(sorted_arr) == 1:
        return sorted_arr[0]
    k = (len(sorted_arr) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_arr) - 1)
    return sorted_arr[lo] + (sorted_arr[hi] - sorted_arr[lo]) * (k - lo)


def avg(arr):
    return sum(arr) / len(arr) if arr else 0


def trunc(s, n):
    if not s:
        return ""
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[:n-3] + "..."


def display_width(s):
    """Display width accounting for CJK wide chars."""
    return sum(2 if ord(ch) > 0x7F else 1 for ch in s)


def ljust(s, n):
    """Left-justify, accounting for CJK wide chars."""
    s = str(s)
    width = display_width(s)
    return s if width >= n else s + " " * (n - width)


def rjust(s, n):
    """Right-justify, accounting for CJK wide chars."""
    s = str(s)
    width = display_width(s)
    return s if width >= n else " " * (n - width) + s


def fmt_local_ts(raw_ts):
    """Render an ISO timestamp in local time; fall back to raw text."""
    if not raw_ts:
        return "N/A"
    if not isinstance(raw_ts, str):
        return str(raw_ts)[:23]
    try:
        dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw_ts[:23]


def print_table(cols, rows):
    """cols: list of (header, width, align 'l'/'r'); rows: list of value lists."""
    fmt = lambda a, w: rjust if a == "r" else ljust
    header = "  ".join(fmt(a, w)(h, w) for h, w, a in cols)
    print(header)
    print("-" * len(header))
    for values in rows:
        print("  ".join(fmt(a, w)(v, w) for (_, w, a), v in zip(cols, values)))


# ─── detail table ──────────────────────────────────────────────────────────────

def is_cancelled(r):
    """Detect cancelled/timeout/error records, including old data without finish='cancelled'."""
    if r.get("finish") in ("cancelled", "error"):
        return True
    if r.get("error"):
        return True
    # Fallback for old data: finish is null and no tokens/output
    return (
        not r.get("finish")
        and r.get("tokens_input", 0) == 0
        and r.get("tokens_output", 0) == 0
        and not r.get("output_text")
    )


def print_detail(records, model_filter=None):
    if model_filter:
        records = [r for r in records if model_filter.lower() in r.get("model_id", "").lower()]

    cancelled = [r for r in records if is_cancelled(r)]
    active = [r for r in records if not is_cancelled(r)]

    print()
    print("=" * 130)
    print(f"  单笔请求明细 ({len(active)} 笔)")
    print("=" * 130)

    cols = [
        ("#",         4,  "r"),
        ("发起时间",   24, "l"),
        ("模型",       26, "l"),
        ("TTFT",       9,  "r"),
        ("TPOT",       9,  "r"),
        ("总耗时",     9,  "r"),
        ("Gen",        9,  "r"),
        ("TokIN",      7,  "r"),
        ("TokOUT",     7,  "r"),
        ("CacheR",     8,  "r"),
        ("CacheW",     7,  "r"),
        ("Cost",       9,  "r"),
        ("Tools",      5,  "r"),
        ("Finish",    11,  "l"),
    ]

    rows = []
    for i, r in enumerate(active):
        rows.append([
            str(i + 1),
            fmt_local_ts(r.get("timestamp")),
            trunc(f"{r.get('provider_id','?')}/{r.get('model_id','?')}", 26),
            fmt_ms(r.get("ttft_ms", 0)),
            fmt_tpot(r.get("tpot_ms", 0)),
            fmt_ms(r.get("total_ms", 0)),
            fmt_ms(r.get("generation_ms", 0)),
            fmt_tokens(r.get("tokens_input", 0)),
            fmt_tokens(r.get("tokens_output", 0)),
            fmt_tokens(r.get("cache_read", 0)),
            fmt_tokens(r.get("cache_write", 0)),
            fmt_cost(r.get("cost", 0)),
            str(r.get("tool_calls", 0)),
            r.get("finish") or "-",
        ])
    print_table(cols, rows)

    # user prompt snippets
    print()
    print("  " + "-" * 100)
    print("  用户输入摘要:")
    print("  " + "-" * 100)
    for i, r in enumerate(active):
        prompt = r.get("user_prompt") or r.get("output_text") or ""
        snippet = trunc(prompt, 80)
        print(f"  [{i+1:>3}] {snippet}")
    print()

    # cancelled/error records
    if cancelled:
        print()
        print("=" * 130)
        print(f"  已取消/超时/错误 ({len(cancelled)} 笔，不参与汇总)")
        print("=" * 130)

        ccols = [
            ("#",         4,  "r"),
            ("发起时间",   19, "l"),
            ("模型",       26, "l"),
            ("总耗时",     9,  "r"),
            ("Finish",    11, "l"),
            ("Error",     20, "l"),
            ("用户输入",   40, "l"),
        ]

        rows = []
        for i, r in enumerate(cancelled):
            rows.append([
                str(i + 1),
                fmt_local_ts(r.get("timestamp")),
                trunc(f"{r.get('provider_id','?')}/{r.get('model_id','?')}", 26),
                fmt_ms(r.get("total_ms", 0)),
                r.get("finish") or "-",
                trunc(r.get("error") or "-", 20),
                trunc(r.get("user_prompt") or "", 40),
            ])
        print_table(ccols, rows)
        print()


# ─── summary ───────────────────────────────────────────────────────────────────

def aggregate(records, model_filter=None):
    if model_filter:
        records = [r for r in records if model_filter.lower() in r.get("model_id", "").lower()]

    # Exclude cancelled/timeout/error records from summary
    records = [r for r in records if not is_cancelled(r)]

    by_model = defaultdict(lambda: {
        "count": 0,
        "tokens_in": 0, "tokens_out": 0, "tokens_reasoning": 0,
        "cache_read": 0, "cache_write": 0,
        "cost": 0.0,
        "ttfts": [], "totals": [], "tpots": [], "gens": [],
    })

    for r in records:
        key = f"{r.get('provider_id','?')}/{r.get('model_id','?')}"
        m = by_model[key]
        m["count"] += 1
        m["tokens_in"] += r.get("tokens_input", 0)
        m["tokens_out"] += r.get("tokens_output", 0)
        m["tokens_reasoning"] += r.get("tokens_reasoning", 0)
        m["cache_read"] += r.get("cache_read", 0)
        m["cache_write"] += r.get("cache_write", 0)
        m["cost"] += r.get("cost", 0)
        if r.get("ttft_ms", 0) > 0:
            m["ttfts"].append(r["ttft_ms"])
        if r.get("total_ms", 0) > 0:
            m["totals"].append(r["total_ms"])
        if r.get("tpot_ms", 0) > 0:
            m["tpots"].append(r["tpot_ms"])
        if r.get("generation_ms", 0) > 0:
            m["gens"].append(r["generation_ms"])

    for m in by_model.values():
        m["ttfts"].sort()
        m["totals"].sort()
        m["tpots"].sort()
        m["gens"].sort()

    return by_model


def print_summary(by_model, date_str):
    print()
    print("=" * 130)
    print(f"  汇总统计 — {date_str}")
    print("=" * 130)

    total_count = sum(m["count"] for m in by_model.values())
    total_tokens_in = sum(m["tokens_in"] for m in by_model.values())
    total_tokens_out = sum(m["tokens_out"] for m in by_model.values())
    total_tokens_reasoning = sum(m["tokens_reasoning"] for m in by_model.values())
    total_cost = sum(m["cost"] for m in by_model.values())
    total_cache_read = sum(m["cache_read"] for m in by_model.values())
    total_cache_write = sum(m["cache_write"] for m in by_model.values())

    print()
    print(f"  总请求数:      {total_count}")
    print(f"  总 Tokens:     input={fmt_tokens(total_tokens_in)}  output={fmt_tokens(total_tokens_out)}  reasoning={fmt_tokens(total_tokens_reasoning)}")
    print(f"  总 Cache:      read={fmt_tokens(total_cache_read)}  write={fmt_tokens(total_cache_write)}")
    print(f"  总 Cost:       ${total_cost:.6f}")

    cols = [
        ("模型",       30, "l"),
        ("请求数",     6,  "r"),
        ("TTFT avg",   9,  "r"),
        ("p50",        9,  "r"),
        ("p99",        9,  "r"),
        ("Total avg",  10, "r"),
        ("p50",        10, "r"),
        ("p99",        10, "r"),
        ("TPOT avg",   9,  "r"),
        ("p50",        9,  "r"),
        ("p99",        9,  "r"),
        ("TokIN",      8,  "r"),
        ("TokOUT",     8,  "r"),
        ("CacheR",     9,  "r"),
        ("CacheW",     8,  "r"),
        ("Cost",       9,  "r"),
    ]

    print()
    rows = []
    for model_name in sorted(by_model.keys()):
        m = by_model[model_name]
        rows.append([
            trunc(model_name, 30),
            str(m["count"]),
            fmt_ms(avg(m["ttfts"])),
            fmt_ms(percentile(m["ttfts"], 0.5)),
            fmt_ms(percentile(m["ttfts"], 0.99)),
            fmt_ms(avg(m["totals"])),
            fmt_ms(percentile(m["totals"], 0.5)),
            fmt_ms(percentile(m["totals"], 0.99)),
            fmt_tpot(avg(m["tpots"])),
            fmt_tpot(percentile(m["tpots"], 0.5)),
            fmt_tpot(percentile(m["tpots"], 0.99)),
            fmt_tokens(m["tokens_in"]),
            fmt_tokens(m["tokens_out"]),
            fmt_tokens(m["cache_read"]),
            fmt_tokens(m["cache_write"]),
            fmt_cost(m["cost"]),
        ])
    print_table(cols, rows)

    print()


# ─── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="opencode 性能统计 metrics 分析")
    parser.add_argument("date", nargs="?", default=None, help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--dir", default=None, help="metrics 目录，默认 ~/.opencode/perf")
    parser.add_argument("--detail", action="store_true", help="额外打印单笔明细和用户输入摘要（默认只打印汇总）")
    parser.add_argument("--model", default=None, help="只看指定模型（子串匹配）")
    parser.add_argument("--list", action="store_true", help="列出所有可用日期")
    args = parser.parse_args()

    perf_dir = args.dir or default_perf_dir()

    if args.list:
        dates = list_available_dates(perf_dir)
        if not dates:
            print(f"在 {perf_dir} 下未找到任何 metrics 文件")
            sys.exit(1)
        print("可用日期:")
        for d in dates:
            print(f"  {d}")
        sys.exit(0)

    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    records = load_metrics(perf_dir, date_str)
    if records is None:
        print(f"未找到 {date_str} 的 metrics 文件: {os.path.join(perf_dir, f'metrics-{date_str}.jsonl')}")
        available = list_available_dates(perf_dir)
        if available:
            print(f"可用日期: {', '.join(available)}")
        sys.exit(1)

    if not records:
        print(f"{date_str} 没有 metrics 记录")
        sys.exit(0)

    print()
    print(f"  opencode 性能报告 — {date_str}")
    print(f"  数据目录: {perf_dir}")
    cancelled_count = sum(1 for r in records if is_cancelled(r))
    active_count = len(records) - cancelled_count
    print(f"  记录数:   {len(records)}（有效 {active_count}，已取消/超时/错误 {cancelled_count}）")

    by_model = aggregate(records, args.model)

    if not by_model:
        print(f"没有匹配模型 '{args.model}' 的记录")
        sys.exit(0)

    print_summary(by_model, date_str)

    if args.detail:
        print_detail(records, args.model)


if __name__ == "__main__":
    main()