#!/usr/bin/env python3
"""OpenCode 性能统计 metrics 分析脚本

用法:
  python3 perf_stats.py [date] [--source auto|db|jsonl] [--dir PERF_DIR] [--db DB_PATH] [--detail] [--model MODEL] [--list]

参数:
  date            日期，格式 YYYY-MM-DD，默认今天
  --source        数据来源：db（opencode SQLite 数据库）/ jsonl（插件 metrics 文件）/ auto（优先数据库，默认）
  --db            指定 opencode 数据库路径（默认自动探测 ~/.local/share/opencode/opencode.db）
  --dir           metrics 文件所在目录，默认 ~/.opencode/perf
  --detail        额外打印单笔明细和用户输入摘要（默认只打印汇总）
  --model         只看指定模型（子串匹配）
  --list          列出所有可用日期

示例:
  python3 perf_stats.py
  python3 perf_stats.py 2026-08-12
  python3 perf_stats.py 2026-08-12 --detail
  python3 perf_stats.py 2026-08-12 --model deepseek
  python3 perf_stats.py --source jsonl
  python3 perf_stats.py --db /path/to/opencode.db
  python3 perf_stats.py --list
"""

import json
import os
import sys
import glob
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta
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


# ─── db source ────────────────────────────────────────────────────────────────

def default_db_path():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    candidates = [
        os.path.join(base, "opencode", "opencode.db"),
        os.path.join(os.path.expanduser("~"), ".opencode", "opencode.db"),
        os.path.join(os.path.expanduser("~"), ".config", "opencode", "opencode.db"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def connect_db(db_path):
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _local_day_range_ms(date_str):
    local = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    start = int(local.timestamp() * 1000)
    end = int((local + timedelta(days=1)).timestamp() * 1000)
    return start, end


def _iso_ms(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def list_db_dates(conn):
    dates = set()
    for (t,) in conn.execute("SELECT time_created FROM message"):
        if isinstance(t, (int, float)) and t > 0:
            dates.add(datetime.fromtimestamp(t / 1000, LOCAL_TZ).strftime("%Y-%m-%d"))
    return sorted(dates)


def _user_prompt_map(conn, parent_ids):
    """message_id -> {text, created} for the user messages referenced by assistant messages."""
    parent_ids = [p for p in parent_ids if isinstance(p, str) and p]
    out = {}
    if not parent_ids:
        return out
    ph = ",".join("?" * len(parent_ids))
    user_ids = []
    for mid, data in conn.execute(f"SELECT id, data FROM message WHERE id IN ({ph})", parent_ids):
        try:
            d = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("role") == "user":
            user_ids.append(mid)
    if not user_ids:
        return out
    ph2 = ",".join("?" * len(user_ids))
    for pmid, tcreated, data in conn.execute(
        f"SELECT message_id, time_created, data FROM part WHERE message_id IN ({ph2}) ORDER BY time_created",
        user_ids,
    ):
        try:
            pd = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        if pd.get("type") == "text" and pd.get("text"):
            entry = out.setdefault(pmid, {"text": "", "created": tcreated})
            entry["text"] += pd["text"]
    return out


def _db_record_to_metrics(mid, sid, d, parts, prompts):
    """Convert one DB assistant message + its parts into a metrics record."""
    t = d.get("time") or {}
    created = t.get("created") or 0
    completed = t.get("completed")
    if not created or not completed:
        return None
    tok = d.get("tokens") or {}
    cache = tok.get("cache") or {}
    in_tokens = tok.get("input") or 0
    out_tokens = tok.get("output") or 0
    reas_tokens = tok.get("reasoning") or 0

    part_times = [ptc for ptc, pd in parts if pd.get("type") in ("text", "reasoning", "tool")]
    ttft = min(part_times) - created if part_times else 0
    total = completed - created
    gen = total - ttft if ttft > 0 else 0
    tpot = gen / out_tokens if out_tokens and gen > 0 else 0

    output_text = "".join(pd.get("text", "") for _, pd in parts if pd.get("type") == "text")
    reasoning_text = "".join(pd.get("text", "") for _, pd in parts if pd.get("type") == "reasoning")
    tool_calls = sum(1 for _, pd in parts if pd.get("type") == "tool")

    finish = d.get("finish")
    error = d.get("error")
    if isinstance(error, dict):
        error = str(error.get("message") or error.get("type") or json.dumps(error, ensure_ascii=False))
    if not finish and not out_tokens and not in_tokens and not output_text:
        finish = "cancelled"
        if not error:
            error = "timeout" if total > 30000 else "cancelled"

    parent = prompts.get(d.get("parentID") or "")
    user_prompt = parent["text"] if parent else ""
    request_time = parent["created"] if parent else created

    return {
        "timestamp": _iso_ms(created),
        "session_id": sid,
        "message_id": mid,
        "parent_message_id": d.get("parentID") or "",
        "agent": d.get("agent") or "",
        "provider_id": d.get("providerID") or "?",
        "model_id": d.get("modelID") or "?",
        "request_time": _iso_ms(request_time),
        "response_start": _iso_ms(created),
        "response_end": _iso_ms(completed),
        "ttft_ms": round(ttft),
        "total_ms": round(total),
        "generation_ms": round(gen),
        "tpot_ms": round(tpot, 2),
        "tokens_input": in_tokens,
        "tokens_output": out_tokens,
        "tokens_reasoning": reas_tokens,
        "cache_read": cache.get("read") or 0,
        "cache_write": cache.get("write") or 0,
        "cost": round(d.get("cost") or 0, 6),
        "finish": finish,
        "tool_calls": tool_calls,
        "error": error,
        "output_chars": len(output_text),
        "reasoning_chars": len(reasoning_text),
        "user_prompt": user_prompt,
        "output_text": output_text,
        "reasoning_text": reasoning_text,
    }


def load_db_records(conn, date_str):
    start_ms, end_ms = _local_day_range_ms(date_str)
    rows = conn.execute(
        "SELECT id, session_id, data FROM message WHERE time_created >= ? AND time_created < ?",
        (start_ms, end_ms),
    ).fetchall()

    msgs = []
    parent_ids = []
    for mid, sid, data in rows:
        try:
            d = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("role") != "assistant":
            continue
        msgs.append((mid, sid, d))
        pid = d.get("parentID")
        if isinstance(pid, str) and pid:
            parent_ids.append(pid)
    if not msgs:
        return []

    prompts = _user_prompt_map(conn, parent_ids)
    ph = ",".join("?" * len(msgs))
    parts = defaultdict(list)
    for pmid, ptc, pdata in conn.execute(
        f"SELECT message_id, time_created, data FROM part WHERE message_id IN ({ph}) ORDER BY time_created",
        [m[0] for m in msgs],
    ):
        try:
            pd = json.loads(pdata)
        except (json.JSONDecodeError, TypeError):
            continue
        parts[pmid].append((ptc, pd))

    records = []
    for mid, sid, d in msgs:
        rec = _db_record_to_metrics(mid, sid, d, parts.get(mid, []), prompts)
        if rec:
            records.append(rec)
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
    s = str(s).replace("\n", " ").strip()
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

def _resolve_source(args):
    """Return ('db', db_path) or ('jsonl', perf_dir)."""
    if args.dir:
        return ("jsonl", args.dir)
    if args.db:
        return ("db", args.db)
    if args.source == "jsonl":
        return ("jsonl", None)
    if args.source == "db":
        return ("db", default_db_path())
    db_path = default_db_path()
    return ("db", db_path) if db_path else ("jsonl", None)


def main():
    parser = argparse.ArgumentParser(description="opencode 性能统计 metrics 分析")
    parser.add_argument("date", nargs="?", default=None, help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--source", choices=["auto", "db", "jsonl"], default="auto",
                        help="数据来源：db/jsonl/auto（默认 auto，优先数据库）")
    parser.add_argument("--db", default=None, help="opencode 数据库路径，默认自动探测")
    parser.add_argument("--dir", default=None, help="metrics 目录，默认 ~/.opencode/perf")
    parser.add_argument("--detail", action="store_true", help="额外打印单笔明细和用户输入摘要（默认只打印汇总）")
    parser.add_argument("--model", default=None, help="只看指定模型（子串匹配）")
    parser.add_argument("--list", action="store_true", help="列出所有可用日期")
    args = parser.parse_args()

    source, source_path = _resolve_source(args)

    if args.list:
        if source == "db":
            if not source_path:
                print("未找到 opencode 数据库（可用 --db 指定路径）")
                sys.exit(1)
            try:
                conn = connect_db(source_path)
            except (FileNotFoundError, sqlite3.Error) as e:
                print(f"打开数据库失败: {e}")
                sys.exit(1)
            try:
                dates = list_db_dates(conn)
            finally:
                conn.close()
        else:
            perf_dir = source_path or default_perf_dir()
            if not os.path.isdir(perf_dir):
                print(f"目录不存在: {perf_dir}")
                sys.exit(1)
            dates = list_available_dates(perf_dir)
        if not dates:
            print(f"未找到任何 metrics 记录")
            sys.exit(1)
        print("可用日期:")
        for d in dates:
            print(f"  {d}")
        sys.exit(0)

    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"无效的日期格式: {date_str}（应为 YYYY-MM-DD）")
        sys.exit(1)

    if source == "db":
        if not source_path:
            print(f"未找到 opencode 数据库（可用 --db 指定路径）")
            sys.exit(1)
        try:
            conn = connect_db(source_path)
        except (FileNotFoundError, sqlite3.Error) as e:
            print(f"打开数据库失败: {e}")
            sys.exit(1)
        try:
            records = load_db_records(conn, date_str)
        except sqlite3.Error as e:
            print(f"读取数据库失败: {e}")
            sys.exit(1)
        finally:
            conn.close()
        perf_dir = source_path
    else:
        perf_dir = source_path or default_perf_dir()
        records = load_metrics(perf_dir, date_str)

    if records is None:
        print(f"未找到 {date_str} 的 metrics 文件: {os.path.join(perf_dir, f'metrics-{date_str}.jsonl')}")
        available = list_available_dates(perf_dir)
        if available:
            print(f"可用日期: {', '.join(available)}")
        sys.exit(1)

    if not records:
        print(f"{date_str} 没有 metrics 记录")
        if source == "db":
            conn = connect_db(perf_dir)
            try:
                available = list_db_dates(conn)
            finally:
                conn.close()
            if available:
                print(f"可用日期: {', '.join(available)}")
        sys.exit(0)

    print()
    print(f"  opencode 性能报告 — {date_str}")
    if source == "db":
        print(f"  数据库: {perf_dir}")
    else:
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