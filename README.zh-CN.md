# opencode-perf-stat

[English](README.md)

为 opencode 收集请求级性能指标（TTFT / TPOT / 总耗时 / tokens / cache / cost），并提供汇总分析脚本。

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

单笔明细（`--detail`）：

```
  单笔请求明细 (2 笔)
   #  发起时间                  模型                             TTFT       TPOT     总耗时        Gen    TokIN   TokOUT    CacheR   CacheW       Cost  Tools  Finish
-----------------------------------------------------------------------------------------------------------------------------------------------------------------
   1  2026-08-12 23:25:11       aliyun-bailian/qwen3.8-max     4.723s     22.7ms     8.870s     4.147s   35,177      183     7,168        0         $0      1  tool-calls
   2  2026-08-12 23:25:19       aliyun-bailian/qwen3.8-max     1.608s    175.7ms     8.110s     6.502s      366       37    42,240        0         $0      0  stop
```

## 工作原理

- **`perf_stats.ts`** — opencode 插件：监听 `message.updated` / `message.part.updated` 事件，每次助手请求完成时往 `metrics-YYYY-MM-DD.jsonl` 追加一条 JSON 记录。
- **`perf_stats.py`** — 分析脚本：读取 JSONL 文件，输出按模型汇总（avg / p50 / p99）与可选的单笔明细。

## 环境要求

自测环境：

| 组件 | 版本 |
| --- | --- |
| opencode | 1.18.16 |
| Python（分析脚本） | 3.13（3.8+ 可用） |

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

- `metrics-YYYY-MM-DD.jsonl` — 每请求一行 JSON，含 `ttft_ms`、`tpot_ms`、`total_ms`、`generation_ms`、`tokens_*`、`cache_*`、`cost`、`finish`、`tool_calls`、`error` 等
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

注意：

- `tokens_input` 不含 `cache_read`（与 provider 上报口径一致）。
- `cost` 仅在 opencode 配置了该模型价格时才有值，否则为 0。
- 百分位采用线性插值；样本量少（n < 5）时仅供参考。

## 测试

```sh
python3 -m unittest test_perf_stats -v
```

## License

MIT，见 [LICENSE](LICENSE)。
