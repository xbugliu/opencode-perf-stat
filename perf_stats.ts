import type { Plugin, Hooks } from "@opencode-ai/plugin"
import { mkdirSync, appendFileSync, existsSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"

const PERF_DIR = process.env.OPENCODE_PERF_DIR || join(homedir(), ".opencode", "perf")
const INCLUDE_CONTENT = !/^(1|true|yes|on)$/i.test(process.env.OPENCODE_PERF_NO_CONTENT ?? "")

interface PendingRequest {
  sessionID: string
  assistantMessageID: string
  parentMessageID: string
  modelID: string
  providerID: string
  agent: string
  userPrompt: string
  userCreated: number
  firstTokenTime: number | null
  lastTokenTime: number | null
  outputText: string
  reasoningText: string
  toolCalls: number
  toolPartIDs: Set<string>
  assistantCreated: number | null
  assistantCompleted: number | null
  tokensInput: number
  tokensOutput: number
  tokensReasoning: number
  cacheRead: number
  cacheWrite: number
  cost: number
  finish: string | null
  error: string | null
}

const pending = new Map<string, PendingRequest>()

const userPromptCache = new Map<string, string>()
const userPromptPending = new Set<string>()
const userCreatedCache = new Map<string, number>()
const sessionAgentCache = new Map<string, string>()

function ensureDir() {
  if (!existsSync(PERF_DIR)) mkdirSync(PERF_DIR, { recursive: true })
}

function dayStr(ms: number): string {
  const d = new Date(ms)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function tsFmt(ms: number | null | undefined): string {
  if (!ms) return "N/A"
  return new Date(ms).toISOString()
}

function fmtDur(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return "N/A"
  return (ms / 1000).toFixed(3) + "s"
}

function appendContent(dateStr: string, content: string) {
  ensureDir()
  appendFileSync(join(PERF_DIR, `content-${dateStr}.log`), content, "utf8")
}

function appendMetrics(dateStr: string, json: object) {
  ensureDir()
  appendFileSync(join(PERF_DIR, `metrics-${dateStr}.jsonl`), JSON.stringify(json) + "\n", "utf8")
}

async function fetchUserPrompt(ctx: any, sessionID: string, messageID: string): Promise<{ text: string; created: number }> {
  try {
    const res = await ctx.client.session.message({
      path: { id: sessionID, messageID },
    })
    let text = ""
    if (res?.data?.parts) {
      for (const p of res.data.parts) {
        if (p.type === "text") text += p.text
      }
    }
    const created = res?.data?.info?.time?.created || 0
    return { text, created }
  } catch {
    return { text: "", created: 0 }
  }
}

async function fetchSessionAgent(ctx: any, sessionID: string): Promise<string> {
  const cached = sessionAgentCache.get(sessionID)
  if (cached) return cached
  try {
    const res = await ctx.client.session.get({ path: { id: sessionID } })
    const agent = res?.data?.agent || ""
    if (agent) sessionAgentCache.set(sessionID, agent)
    return agent
  } catch {
    return ""
  }
}

function finalizeRequest(key: string) {
  const req = pending.get(key)
  if (!req) return
  pending.delete(key)

  if (!req.assistantCreated || !req.assistantCompleted) return

  const totalMs = req.assistantCompleted - req.assistantCreated
  const ttftMs = req.firstTokenTime ? req.firstTokenTime - req.assistantCreated! : 0
  const generationMs = ttftMs > 0 ? totalMs - ttftMs : 0
  const outputTokens = req.tokensOutput || 0
  const tpotMs = outputTokens > 0 && generationMs > 0 ? generationMs / outputTokens : 0

  // Detect cancelled/timeout: no finish, no tokens, no output text
  let finish = req.finish
  let error = req.error
  if (!finish && outputTokens === 0 && req.tokensInput === 0 && req.outputText.length === 0) {
    finish = "cancelled"
    if (!error) error = totalMs > 30000 ? "timeout" : "cancelled"
  }

  const dateStr = dayStr(req.assistantCreated)

  const record = {
    timestamp: tsFmt(req.assistantCreated),
    session_id: req.sessionID,
    message_id: req.assistantMessageID,
    parent_message_id: req.parentMessageID,
    agent: req.agent,
    provider_id: req.providerID,
    model_id: req.modelID,
    request_time: tsFmt(req.userCreated),
    response_start: tsFmt(req.assistantCreated),
    response_end: tsFmt(req.assistantCompleted),
    ttft_ms: Math.round(ttftMs),
    total_ms: Math.round(totalMs),
    generation_ms: Math.round(generationMs),
    tpot_ms: +tpotMs.toFixed(2),
    tokens_input: req.tokensInput,
    tokens_output: req.tokensOutput,
    tokens_reasoning: req.tokensReasoning,
    cache_read: req.cacheRead,
    cache_write: req.cacheWrite,
    cost: +req.cost.toFixed(6),
    finish: finish,
    tool_calls: req.toolCalls,
    error: error,
    output_chars: req.outputText.length,
    reasoning_chars: req.reasoningText.length,
    ...(INCLUDE_CONTENT
      ? {
          user_prompt: req.userPrompt,
          output_text: req.outputText,
          reasoning_text: req.reasoningText,
        }
      : {}),
  }

  appendMetrics(dateStr, record)

  if (!INCLUDE_CONTENT) return

  const contentBlock = [
    "====================================================================================================",
    `# Request: ${req.assistantMessageID}  |  ${tsFmt(req.assistantCreated)}`,
    `# Model: ${req.providerID}/${req.modelID}  |  Agent: ${req.agent}  |  Session: ${req.sessionID}`,
    `# TTFT: ${fmtDur(ttftMs)}  |  Total: ${fmtDur(totalMs)}  |  TPOT: ${tpotMs.toFixed(1)}ms  |  Tokens: ${req.tokensInput} in / ${req.tokensOutput} out / ${req.tokensReasoning} reasoning`,
    `# Cache: read=${req.cacheRead} write=${req.cacheWrite}  |  Cost: $${req.cost.toFixed(6)}  |  Tools: ${req.toolCalls}  |  Finish: ${req.finish || "N/A"}`,
    "====================================================================================================",
    "",
    "## [USER INPUT]",
    "",
    req.userPrompt || "(empty)",
    "",
    "## [ASSISTANT OUTPUT]",
    "",
    req.outputText || "(empty)",
    "",
  ]
  if (req.reasoningText) {
    contentBlock.push("## [REASONING]", "", req.reasoningText, "")
  }
  if (req.error) {
    contentBlock.push("## [ERROR]", "", req.error, "")
  }
  appendContent(dateStr, contentBlock.join("\n") + "\n")
}

export const PerfStatsPlugin: Plugin = async (ctx) => {
  ensureDir()

  const hooks: Hooks = {
    event: async ({ event }) => {
      try {
        switch (event.type) {
          case "message.updated": {
            const info = event.properties.info
            if (info.role === "user") {
              userCreatedCache.set(info.id, info.time.created)
              if (!userPromptCache.has(info.id) && !userPromptPending.has(info.id)) {
                userPromptPending.add(info.id)
                fetchUserPrompt(ctx, info.sessionID, info.id).then(({ text, created }) => {
                  userPromptCache.set(info.id, text)
                  if (created) userCreatedCache.set(info.id, created)
                  userPromptPending.delete(info.id)
                })
              }
            } else if (info.role === "assistant") {
              const key = `${info.sessionID}:${info.id}`
              if (!pending.has(key) && !info.time.completed) {
                const userPrompt = userPromptCache.get(info.parentID) || ""
                const userCreated = userCreatedCache.get(info.parentID) || 0
                pending.set(key, {
                  sessionID: info.sessionID,
                  assistantMessageID: info.id,
                  parentMessageID: info.parentID,
                  modelID: info.modelID || "unknown",
                  providerID: info.providerID || "unknown",
                  agent: "",
                  userPrompt,
                  userCreated,
                  firstTokenTime: null,
                  lastTokenTime: null,
                  outputText: "",
                  reasoningText: "",
                  toolCalls: 0,
                  toolPartIDs: new Set<string>(),
                  assistantCreated: info.time.created,
                  assistantCompleted: null,
                  tokensInput: 0,
                  tokensOutput: 0,
                  tokensReasoning: 0,
                  cacheRead: 0,
                  cacheWrite: 0,
                  cost: 0,
                  finish: null,
                  error: null,
                })
                fetchSessionAgent(ctx, info.sessionID).then((agent) => {
                  const r = pending.get(key)
                  if (r && !r.agent) r.agent = agent
                })
              }
              const req = pending.get(key)
              if (req) {
                req.assistantCreated = req.assistantCreated || info.time.created
                req.tokensInput = info.tokens?.input || 0
                req.tokensOutput = info.tokens?.output || 0
                req.tokensReasoning = info.tokens?.reasoning || 0
                req.cacheRead = info.tokens?.cache?.read || 0
                req.cacheWrite = info.tokens?.cache?.write || 0
                req.cost = info.cost || 0
                req.finish = info.finish || null
                if (info.error) {
                  req.error = JSON.stringify(info.error)
                }
                if (!req.userPrompt) {
                  const cached = userPromptCache.get(req.parentMessageID)
                  if (cached) req.userPrompt = cached
                  const uc = userCreatedCache.get(req.parentMessageID)
                  if (uc) req.userCreated = uc
                }
                if (info.time.completed) {
                  req.assistantCompleted = info.time.completed
                  finalizeRequest(key)
                }
              }
            }
            break
          }
          case "message.part.updated": {
            const part = event.properties.part
            const key = `${part.sessionID}:${part.messageID}`
            const req = pending.get(key)
            if (!req) break
            if (part.type === "text") {
              if (part.time?.start && !req.firstTokenTime) {
                req.firstTokenTime = part.time.start
              }
              if (part.time?.end) {
                req.lastTokenTime = part.time.end
              }
              if (event.properties.delta) {
                req.outputText += event.properties.delta
              } else if (part.text && !req.outputText) {
                req.outputText = part.text
              }
            } else if (part.type === "reasoning") {
              if (part.time?.start && !req.firstTokenTime) {
                req.firstTokenTime = part.time.start
              }
              if (part.time?.end) {
                req.lastTokenTime = part.time.end
              }
              if (event.properties.delta) {
                req.reasoningText += event.properties.delta
              } else if (part.text && !req.reasoningText) {
                req.reasoningText = part.text
              }
            } else if (part.type === "tool") {
              if (!req.toolPartIDs.has(part.id)) {
                req.toolPartIDs.add(part.id)
                req.toolCalls++
              }
              const toolStart = (part as any).state?.time?.start
              if (toolStart && !req.firstTokenTime) {
                req.firstTokenTime = toolStart
              }
            } else if (part.type === "step-finish") {
              const sf = part as any
              if (sf.tokens) {
                req.tokensInput = sf.tokens.input
                req.tokensOutput = sf.tokens.output
                req.tokensReasoning = sf.tokens.reasoning
                req.cacheRead = sf.tokens.cache?.read || 0
                req.cacheWrite = sf.tokens.cache?.write || 0
              }
              if (sf.cost) req.cost = sf.cost
            }
            break
          }
          default:
            break
        }
      } catch {}
    },
  }

  return hooks
}

export default PerfStatsPlugin