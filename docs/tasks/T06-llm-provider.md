# T06 — Self-hosted LLM provider, swappable by config

**Priority:** Must · **Milestone:** M1 Foundations · **Depends on:** T01 · **Estimate:** 1.5 days

## Why

- "Deploy" row: **LLM swappable by config, self-hosted by default** (C1).
- "Reasoning (LLM)" part: plans the answer, **decides which tool to call**, writes the reply. The model must support tool/function calling.
- No public chatbots on meeting data (C3): the default setup must never send meeting content to a hosted service.
- Suggested stack: **Qwen2.5 7B / Llama 3.1 8B via Ollama** on the provided 24 GB GPU VM.
- One GPU is shared by STT, embeddings and the LLM. Answers to "Hey BA" must not queue behind background work, or we miss the 10 s target (C8).

## What

**In scope**
- Ollama service (GPU) in Docker Compose with the default model pulled and kept warm.
- `LLMClient` abstraction over the **OpenAI-compatible Chat Completions API**. That API works with Ollama, vLLM, llama.cpp server and hosted providers, so switching is a config change.
- Tool calling and JSON/structured output helpers.
- Priority lanes: `interactive` (trigger answers) and `background` (classifier, summaries).
- Remote-endpoint guard (`llm.allow_remote`).
- Model selection benchmark (accuracy vs latency).

**Out of scope**
- Prompts (T09, T12), embeddings (T07).

## Details

### Serving
- `ollama` container with the NVIDIA GPU reserved (T15). Persist models in a volume.
- An `ollama-init` one-shot service runs `ollama pull qwen2.5:7b-instruct` (and the alternative model) on first start.
- **Context length:** Ollama picks a default context length from available VRAM (per its docs: 4k below 24 GiB, 32k for 24–48 GiB). A "24 GB" card can land on either side of that line, and anything longer than the context is silently truncated. **Set it explicitly** to 16k (enough for persona + tools + meeting digest + retrieved chunks, and smaller than 32k so the KV cache leaves room for Whisper). Use the `OLLAMA_CONTEXT_LENGTH=16384` server env var, or a derived model (`Modelfile` with `PARAMETER num_ctx 16384`). The OpenAI-compatible endpoint can't set the context size per request. Check the value with `ollama ps` (`CONTEXT` column).
- **Keep warm:** `OLLAMA_KEEP_ALIVE=-1` (never unload) plus a warm-up request at agent start, so the first "Hey BA" doesn't pay model-load time.
- `OLLAMA_NUM_PARALLEL=2` so one interactive request can run alongside one background request.

### Client
```python
class LLMClient:
    async def chat(self, messages, tools=None, response_format=None,
                   lane: Literal["interactive", "background"] = "background",
                   max_tokens=None, timeout_s=None) -> LLMResponse
```
- Implemented with the `openai` Python SDK (`base_url=llm.base_url`, `api_key=llm.api_key`). Ollama exposes this API at `/v1`.
- Returns text, parsed `tool_calls` (name + JSON args), token usage and latency.
- Validates tool-call arguments against the tool's pydantic schema. On invalid JSON or a schema error it makes one repair attempt (send the error back to the model) before failing.
- Structured output (classifier, guardrail checks): request JSON with a schema. Ollama supports JSON-schema `format` on its native API, and `response_format` on the OpenAI-compatible endpoint. Always validate with pydantic regardless.
- Timeouts: `llm.timeout_s` for interactive calls. Exceeding it triggers T14's fallback path.

### Swapping models / providers (config only)
| Target | Config |
|---|---|
| Default (self-hosted) | `llm.base_url=http://ollama:11434/v1`, `llm.model=qwen2.5:7b-instruct` |
| Llama 3.1 8B | `llm.model=llama3.1:8b` |
| vLLM on the same VM | `llm.base_url=http://vllm:8000/v1`, `llm.model=Qwen/Qwen2.5-7B-Instruct` |
| Hosted API (allowed only for non-meeting testing) | `llm.base_url=https://…`, `llm.allow_remote=true` |

### Remote guard (protects C3)
At startup, resolve the `llm.base_url` host. If it isn't a container name, localhost or a private IP, and `llm.allow_remote` is `false`, **refuse to start** with an explicit error. The README explains that enabling remote providers on real meeting data breaks the assignment rules.

### Priority lanes
- An asyncio scheduler in front of the client: `interactive` requests start immediately. `background` requests wait while any interactive request is in flight, and at most 1 background request runs at a time.
- The classifier tolerates delay (T09 batches). Answers do not.

### GPU memory budget (24 GB, approximate; verify with `nvidia-smi` on the VM)
| Component | Approx. VRAM |
|---|---|
| Qwen2.5-7B-Instruct, Ollama default 4-bit quant (~4.7 GB weights) + KV cache at 16k ctx × 2 parallel | ~7–9 GB |
| faster-whisper `large-v3-turbo` fp16 | ~2–3 GB |
| Embedding model (bge-small) | < 1 GB |
| Headroom | > 10 GB |

The headroom means a **14B model (e.g. `qwen2.5:14b`, 4-bit)** is an option if 7B tool calling turns out unreliable. Benchmark it for latency before switching.

### Model selection benchmark
Run the T16 evaluation set (tool choice accuracy, argument accuracy, guardrail behaviour, latency) on `qwen2.5:7b-instruct`, `llama3.1:8b` and `qwen2.5:14b`. Choose the most accurate model whose p95 interactive latency fits T14's budget. Record the results for the README "tech choices" section.

## Acceptance criteria

- [ ] `docker compose up` starts Ollama with the default model available, without manual steps.
- [ ] Changing only `BA_LLM__MODEL` / `BA_LLM__BASE_URL` switches models or providers, with no code change.
- [ ] Tool calls are parsed and validated. Invalid arguments get one repair attempt.
- [ ] The context window is confirmed ≥ 16k: a 12k-token prompt is not truncated (test below).
- [ ] The remote guard blocks a public endpoint unless `allow_remote=true`.
- [ ] An interactive request is never delayed by more than one in-flight background request (measured).
- [ ] The benchmark table is produced and the default model chosen.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T06-U1 | unit | Mock OpenAI-compatible server (`respx`): tool call response parsed into name + args | Correct |
| T06-U2 | unit | Invalid tool args → repair round → valid | One retry, then success; second failure raises |
| T06-U3 | unit | Timeout handling | Raises `LLMTimeout` within `timeout_s` + 100 ms |
| T06-U4 | unit | Remote guard: `http://ollama:11434`, `http://10.0.0.5`, `https://api.example.com` | allow, allow, block |
| T06-U5 | unit | Priority scheduler with fake slow calls | Interactive starts ≤ 10 ms after submit when only background is running |
| T06-I1 | integration (Ollama) | For each tool schema (T12), 10 natural-language prompts | ≥ 90% correct tool + valid args on the default model |
| T06-I2 | integration (Ollama) | "Needle" test: fact placed at the start of a ~12k-token prompt, asked about at the end | Answered correctly (proves no truncation) |
| T06-I3 | integration | Swap to `llama3.1:8b` via env only; rerun T06-I1 | Runs without code change |
| T06-P1 | performance (VM) | Typical interactive prompts (~5k and ~9k tokens in with a cached ~2.5k prefix, ~150 out) | Record p50/p95; feeds T14 stage 7 |

## Dependencies

- T01. Used by T07 (optional query rewriting), T09, T10, T12, T13.

## Related resources

- Ollama: https://github.com/ollama/ollama
- Ollama OpenAI compatibility (supported fields; setting context size via Modelfile): https://docs.ollama.com/api/openai-compatibility
- Ollama context length (VRAM-based defaults, `OLLAMA_CONTEXT_LENGTH`): https://docs.ollama.com/context-length
- Ollama tool calling: https://docs.ollama.com/capabilities/tool-calling · announcement: https://ollama.com/blog/tool-support
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Ollama FAQ (keep-alive, parallel requests): https://docs.ollama.com/faq
- Ollama Docker image: https://hub.docker.com/r/ollama/ollama
- Qwen2.5 on Ollama: https://ollama.com/library/qwen2.5 · Llama 3.1 on Ollama: https://ollama.com/library/llama3.1
- vLLM OpenAI-compatible server: https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html
- OpenAI Python SDK: https://github.com/openai/openai-python
- respx (HTTP mocking for tests): https://lundberg.github.io/respx/
