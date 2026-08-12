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

## How it works

- **`perf_stats.ts`** — opencode plugin. Listens to `message.updated` / `message.part.updated` events and appends one JSON record per completed assistant request to `metrics-YYYY-MM-DD.jsonl`.
- **`perf_stats.py`** — analysis script. Reads the JSONL files and prints a per-model summary (avg / p50 / p99) and optional per-request details.

## Requirements

Tested with:

| Component | Version |
| --- | --- |
| opencode | 1.18.16 |
| Python (analysis script) | 3.13 (works on 3.8+) |

## Install the plugin

Drop `perf_stats.ts` into the global plugins directory (auto-discovered, no config needed):

```sh
mkdir -p ~/.config/opencode/plugins
cp perf_stats.ts ~/.config/opencode/plugins/
```

Or into the project-level `.opencode/plugins/`. Plugins are loaded at startup only — **restart opencode** after changes.

## Data output

Writes to `~/.opencode/perf/` by default. Override via environment variables:

- `OPENCODE_PERF_DIR=/path/to/dir` — directory for metrics and content files
- `OPENCODE_PERF_NO_CONTENT=1` — **privacy switch**: omits `user_prompt` / `output_text` / `reasoning_text` from metrics and skips `content-*.log` entirely

Files produced:

- `metrics-YYYY-MM-DD.jsonl` — one JSON line per request: `ttft_ms`, `tpot_ms`, `total_ms`, `generation_ms`, `tokens_*`, `cache_*`, `cost`, `finish`, `tool_calls`, `error`, ...
- `content-YYYY-MM-DD.log` — human-readable record of prompts and outputs (subject to the privacy switch)

## Usage

```sh
# Today's summary (default: summary only)
python3 perf_stats.py

# A specific date
python3 perf_stats.py 2026-08-12

# Also print per-request details and user-prompt snippets
python3 perf_stats.py --detail

# Filter by model (substring match)
python3 perf_stats.py --model deepseek

# Custom data directory
python3 perf_stats.py --dir /path/to/perf

# List available dates
python3 perf_stats.py --list
```

Cancelled / timed-out / errored requests are listed separately and excluded from the summary.

Notes:

- `tokens_input` does not include `cache_read` (as reported by the provider).
- `cost` is only populated when opencode has pricing configured for the model; otherwise it is 0.
- Percentiles use linear interpolation; small samples (n < 5) are indicative only.

## Tests

```sh
python3 -m unittest test_perf_stats -v
```

## License

MIT, see [LICENSE](LICENSE).
