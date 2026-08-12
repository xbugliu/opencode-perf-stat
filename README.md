# opencode perf stats

为 opencode 收集请求级性能指标（TTFT / TPOT / 总耗时 / tokens / cache / cost），并提供汇总分析脚本。

## 文件

| 文件 | 说明 |
| --- | --- |
| `perf_stats.ts` | opencode 插件：监听 `message.updated` / `message.part.updated`，每次助手消息完成写入一条 JSONL |
| `perf_stats.py` | 分析脚本：读取 metrics JSONL，输出汇总与（可选）单笔明细 |
| `test_perf_stats.py` | `perf_stats.py` 的单元测试 |

## 安装插件

把 `perf_stats.ts` 放到全局插件目录（自动发现，无需配置）：

```sh
mkdir -p ~/.config/opencode/plugins
cp perf_stats.ts ~/.config/opencode/plugins/
```

或放到项目级 `.opencode/plugins/`。插件只在启动时加载，改完需**重启 opencode**。

## 数据输出

默认写 `~/.opencode/perf/`，可用环境变量覆盖：

- `OPENCODE_PERF_DIR=/path/to/dir` — metrics 与 content 文件目录
- `OPENCODE_PERF_NO_CONTENT=1` — **隐私开关**：不写入 `user_prompt` / `output_text` / `reasoning_text`，也不写 `content-*.log`

文件命名：

- `metrics-YYYY-MM-DD.jsonl` — 每请求一行 JSON，含 `ttft_ms`、`tpot_ms`、`total_ms`、`tokens_*`、`cache_*`、`cost`、`finish`、`error` 等
- `content-YYYY-MM-DD.log` — 人类可读的请求/输出记录（受隐私开关控制）

## 使用

```sh
# 今天汇总（默认只打印汇总）
python3 perf_stats.py

# 指定日期
python3 perf_stats.py 2026-08-12

# 额外打印单笔明细与用户输入摘要
python3 perf_stats.py --detail

# 只看某模型（子串匹配）
python3 perf_stats.py --model deepseek

# 指定数据目录
python3 perf_stats.py --dir /path/to/perf

# 列出所有可用日期
python3 perf_stats.py --list
```

已取消/超时/错误的请求单独列出，不参与汇总统计。

## 测试

```sh
python3 -m unittest test_perf_stats -v
```

## License

MIT，见 [LICENSE](LICENSE)。