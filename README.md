# opencode-perf-stat

[简体中文](README.zh-CN.md)

Request-level performance metrics for [opencode](https://opencode.ai): TTFT / TPOT / total latency / tokens / cache / cost, collected by a plugin into JSONL, plus an analysis script that renders summaries and per-request details.

```
  汇总统计 — 2026-08-12

  总请求数:      2
  总 Tokens:     input=35,543  output=220  reasoning=272
  总 Cache:      read=49,408  write=0
  总 Cost:       $0.000000

模型                            请求数   TTFT avg        p50        p99   Total avg         p50         p99   TPOT avg        p50        p99     TokIN    TokOUT    CacheR    CacheW       Cost
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
aliyun-bailian/qwen3.8-max           2     3.166s     3.166s     4.692s      8.490s      8.490s      8.862s     99.2ms    99.2ms   174.2ms    35,543       220     49,408         0         $0
```

Per-request details (`--detail`):

```
  单笔请求明细 (2 笔)
   #  发起时间                  模型                             TTFT       TPOT     总耗时        Gen    TokIN   TokOUT    CacheR   CacheW       Cost  Tools  Finish
-----------------------------------------------------------------------------------------------------------------------------------------------------------------
   1  2026-08-12 23:25:11       aliyun-bailian/qwen3.8-max     4.723s     22.7ms     8.870s     4.147s   35,177      183     7,168        0         $0      1  tool-calls
   2  2026-08-12 23:25:19       aliyun-bailian/qwen3.8-max     1.608s    175.7ms     8.110s     6.502s      366       37    42,240        0         $0      0  stop
```

## How it works

Two data sources, both supported by the analysis script:

- **opencode's SQLite database (default)** — reads `message` / `part` tables directly from `~/.local/share/opencode/opencode.db` (or `$XDG_DATA_HOME/opencode/opencode.db`). No plugin needed; works on all historical requests.
- **plugin JSONL (optional)** — `perf_stats.ts` listens to `message.updated` / `message.part.updated` events and appends one record per completed assistant request to `metrics-YYYY-MM-DD.jsonl`. Use it when you want precise streaming-derived TTFT/TPOT or a standalone content log.

`perf_stats.py` reads either source and prints a per-model summary (avg / p50 / p99) and optional per-request details.

## Requirements

Tested with:

| Component | Version |
| --- | --- |
| opencode | 1.18.16 |
| Python (analysis script) | 3.13 (works on 3.8+) |

## Install the plugin (optional)

Only needed for the JSONL data source. Drop `perf_stats.ts` into the global plugins directory (auto-discovered, no config needed):

```sh
mkdir -p ~/.config/opencode/plugins
cp perf_stats.ts ~/.config/opencode/plugins/
```

Or into the project-level `.opencode/plugins/`. Plugins are loaded at startup only — **restart opencode** after changes.

## Data output

The SQLite source reads opencode's own database — nothing is written. When using the plugin, it writes to `~/.opencode/perf/` by default, overridable via environment variables:

- `OPENCODE_PERF_DIR=/path/to/dir` — directory for metrics and content files
- `OPENCODE_PERF_NO_CONTENT=1` — **privacy switch**: omits `user_prompt` / `output_text` / `reasoning_text` from metrics and skips `content-*.log` entirely

Plugin files:

- `metrics-YYYY-MM-DD.jsonl` — one JSON line per request: `ttft_ms`, `tpot_ms`, `total_ms`, `generation_ms`, `tokens_*`, `cache_*`, `cost`, `finish`, `tool_calls`, `error`, ...
- `content-YYYY-MM-DD.log` — human-readable record of prompts and outputs (subject to the privacy switch)

## Usage

```sh
# Today's summary from the SQLite database (default source)
python3 perf_stats.py

# A specific date
python3 perf_stats.py 2026-08-12

# Also print per-request details and user-prompt snippets
python3 perf_stats.py --detail

# Filter by model (substring match)
python3 perf_stats.py --model deepseek

# Use the plugin's JSONL files instead of the database
python3 perf_stats.py --source jsonl

# Point at a specific database or metrics directory
python3 perf_stats.py --db /path/to/opencode.db
python3 perf_stats.py --source jsonl --dir /path/to/perf

# List available dates
python3 perf_stats.py --list
```

Cancelled / timed-out / errored requests are listed separately and excluded from the summary.

Notes:

- `tokens_input` does not include `cache_read` (as reported by the provider).
- `cost` is only populated when opencode has pricing configured for the model; otherwise it is 0.
- Percentiles use linear interpolation; small samples (n < 5) are indicative only.
- Metrics derived from the database (TTFT = first part time − message created, total = created → completed) match the plugin's event-derived values, but cover historical requests the plugin never saw.

## Tests

```sh
python3 -m unittest test_perf_stats -v
```

## License

MIT, see [LICENSE](LICENSE).
