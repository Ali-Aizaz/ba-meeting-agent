# T15 — Docker Compose and deployment to the GPU VM

**Priority:** Must · **Milestone:** M1 (skeleton) → M4 (final) · **Depends on:** T01, T06 (grows with every task) · **Estimate:** 2 days total

## Why

- "Deploy" row: **`docker compose up`, deployed on our cloud VM; LLM swappable by config, self-hosted by default.**
- Submission: **Code + Docker + config**.
- Resources: TechSol provides a **cloud GPU VM (24 GB GPU)**. Everything (bot, STT, LLM, vector DB) must run there with no hosted bot or transcription services (C2).
- Deploying early (M1) exposes VM, driver and GPU problems while there is still time to fix them.

## What

**In scope**
- `docker-compose.yml` with all services, GPU reservations, volumes, health checks, restart policies.
- Agent image: Python app + Chromium/Playwright + Xvfb + PulseAudio + CUDA/cuDNN runtime for faster-whisper.
- First-run initialisation: model pulls, pack ingest, Notion schema validation.
- Control interface to make the bot join a meeting.
- VM setup guide (driver, Docker, NVIDIA Container Toolkit) and a smoke-test script.
- Debug profile (noVNC) for one-time sign-in and watching the bot's browser.

**Out of scope**
- CI/CD to production (not required). Kubernetes.

## Details

### Services
| Service | Image | GPU | Purpose | Volume(s) |
|---|---|---|---|---|
| `ollama` | `ollama/ollama` | yes | LLM serving (T06) | `ollama_models` |
| `ollama-init` | `ollama/ollama` (one-shot) | no | Pull configured models, then exit | `ollama_models` |
| `qdrant` | `qdrant/qdrant` | no | Vector store (T07) | `qdrant_data` |
| `agent` | built from `./Dockerfile` | yes | Bot + audio + STT + agent + Notion | `pack` (ro), `state`, `browser_profile`, `hf_cache`, `debug` |
| `novnc` (profile `debug`) | noVNC image or built in | no | View/drive the bot's Xvfb display for sign-in and debugging | — |

### GPU reservation (Compose spec)
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

### Agent image
- Base: `nvidia/cuda:12.x-cudnn-runtime-ubuntu22.04` (or 24.04). Recent faster-whisper/CTranslate2 releases need CUDA 12 + cuDNN 9; check the faster-whisper README for the exact pairing.
- Install Python 3.11, `uv`, project deps (locked), and `playwright install --with-deps chromium`. Also `xvfb`, `pulseaudio` (+ `pulseaudio-utils` for `pactl`/`parec`), `ffmpeg`, fonts.
- Entrypoint script: start PulseAudio (user mode, `--exit-idle-time=-1`) → create the `meet_out` null sink and set it as default (T03) → start Xvfb on `:99` → run pack ingest if changed (T07) → validate the Notion schema (T11) → warm models → start the app (control API).
- Run as a non-root user. Chromium inside containers often needs `--no-sandbox` or a seccomp profile; prefer the seccomp profile if it works on the VM, and document whichever is used.
- Set `shm_size: 2gb` for the agent (Chromium crashes with the default 64 MB `/dev/shm`).

### Control interface
A small FastAPI app inside `agent`, bound to `control.host` (`127.0.0.1`) and never published on the VM's public interface:
- `POST /join {"url": "https://meet.google.com/xxx-xxxx-xxx", "title": "Project Falcon review"}`
- `POST /leave` (triggers `save_meeting()` first, posts the link, then leaves)
- `GET /health`, `GET /status` (in meeting? participants, `chat_enabled`, reconnects, last trigger latency, Meeting Notes URL)

CLI wrapper: `docker compose exec agent ba join <url> --title "Project Falcon review"`.
`BA_MEETING__URL` in `.env` can also auto-join at start.

**One meeting at a time.** The agent handles a single meeting per process (one browser, one audio sink, one meeting clock). `POST /join` while in a meeting returns `409`. The assignment needs nothing more; running two meetings would mean two `agent` containers with separate volumes.

### Exposure and security on the VM
- Only SSH is reachable from outside. The noVNC debug service and the control API bind to `127.0.0.1`; reach them through an SSH tunnel (`ssh -L 6080:127.0.0.1:6080 …`). noVNC gets a password (`NOVNC_PASSWORD` in `.env`). The VM has a signed-in Google session and a Notion token on it; an open VNC port would expose both.
- `ollama` and `qdrant` ports stay on the compose network (no `ports:` mapping) unless debugging.

### Config and secrets
- `.env.example` documents every variable: `BA_NOTION__TOKEN`, `BA_NOTION__DATABASES__*`, `BA_LLM__*`, `BA_MEETING__*`, optional `HF_TOKEN` (only for pyannote fallback model download, T04).
- `config/*.yaml` is mounted read-only. Changing the LLM = edit `.env` + `docker compose up -d`.
- No secrets in images or the repo (T17 runs a secrets scan).

### First run on the VM
```bash
# once per VM
#  1. NVIDIA driver installed; `nvidia-smi` works on the host
#  2. Docker Engine + Compose plugin
#  3. NVIDIA Container Toolkit; `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` works
git clone <private repo> && cd ba-meeting-agent
cp .env.example .env   # fill Notion token + DB IDs
docker compose up -d
docker compose --profile debug up -d novnc   # only for first Google sign-in (T02)
./scripts/smoke_test.sh
docker compose exec agent ba join https://meet.google.com/...
```

### Resilience
- `restart: unless-stopped` for long-running services. The agent resumes its meeting state from SQLite (T10) and rejoins if it was in a meeting.
- Health checks: `ollama` (`ollama list`), `qdrant` (HTTP readiness endpoint), `agent` (`/health`). Use `depends_on: condition: service_healthy` (and `service_completed_successfully` for `ollama-init`).
- Logs: JSON to stdout (`docker compose logs agent`). Spans also go to SQLite (T14).

### Offline after first pull
Models (Ollama, Whisper, embeddings) are cached in volumes, so later starts need no internet except Meet and Notion.

## Acceptance criteria

- [ ] On a fresh VM that meets the prerequisites, following the README only: `docker compose up -d` brings every service to healthy without manual edits other than `.env`.
- [ ] `nvidia-smi` works inside `agent` and `ollama`. faster-whisper runs on CUDA (not a silent CPU fallback; log the device at start).
- [ ] Changing `BA_LLM__MODEL` and running `docker compose up -d` switches the model.
- [ ] `ba join <url>` makes the bot join (T02). `ba leave` saves notes and leaves.
- [ ] Restarting `agent` mid-meeting resumes state.
- [ ] Images build reproducibly from the lockfile. `docker compose config` validates.
- [ ] From outside the VM, only SSH is reachable (`nmap`/`ss` check); noVNC needs the tunnel plus a password.
- [ ] Smoke test passes on the TechSol VM.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T15-C1 | CI | `docker compose config -q`; build `agent` image | Success |
| T15-S1 | smoke (VM) | `scripts/smoke_test.sh`: all services healthy; GPU visible in `agent` and `ollama`; default model listed; Qdrant collection exists with chunks; Notion schema validation OK; `/health` 200 | All green |
| T15-S2 | smoke (VM) | Agent self-test: transcribe a fixture WAV on GPU; one LLM tool call; one `search_knowledge` | Pass; devices logged as CUDA |
| T15-R1 | resilience | `docker compose restart agent` during a replayed meeting | Rejoins/resumes; no duplicate Notion rows |
| T15-F1 | fresh install | Teammate follows the README on a clean VM | Done in ≤ 45 min (excluding model download time) with no undocumented steps |

## Dependencies

- T01 (layout, config), T06 (Ollama). Grows with T02/T03 (browser/audio deps), T07 (Qdrant, ingest), T11 (schema check), T14 (spans).
- Used by T16 (rehearsals on the VM), T17 (README setup section).

## Related resources

- Docker Compose GPU support: https://docs.docker.com/compose/how-tos/gpu-support/
- NVIDIA Container Toolkit install guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- Compose `depends_on` conditions / health checks: https://docs.docker.com/compose/how-tos/startup-order/
- NVIDIA CUDA images: https://hub.docker.com/r/nvidia/cuda
- Ollama Docker image: https://hub.docker.com/r/ollama/ollama
- Qdrant Docker / installation: https://qdrant.tech/documentation/guides/installation/
- Playwright Docker notes: https://playwright.dev/python/docs/docker
- faster-whisper GPU requirements: https://github.com/SYSTRAN/faster-whisper#gpu
- FastAPI: https://fastapi.tiangolo.com/
- noVNC: https://github.com/novnc/noVNC
