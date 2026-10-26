# T15 — Run on the cloud server

**Priority:** Must · **Depends on:** T01, T02 · **Estimate:** part of the one-day build

## Why

- They provide a cloud server. The bot has to join Meet from somewhere with a browser.
- The new brief does not require a GPU, Docker-as-submission, or a self-hosted model. Docker is still the simplest way to get Chromium and Xvfb onto a fresh server.

## What

**In scope**
- A container (or a documented venv) with Python, Playwright’s Chromium, and Xvfb.
- `.env` on the server for `BA_LLM__API_KEY` and `BA_NOTION__TOKEN`.
- A command to join a URL: `python -m ba_agent join <meet-url>`.
- Optional noVNC (or the server’s console) for the one-time Google sign-in. Guest join does not need it.
- A `/status` or log line for `captions_enabled` and `chat_enabled`.

**Out of scope**
- NVIDIA drivers, Ollama, Qdrant, CUDA images.

## Details

- Image base: a current Ubuntu with the Playwright OS dependencies (`playwright install --with-deps chromium`).
- The browser profile is a volume so the Google session survives restarts.
- Outbound HTTPS to `api.openai.com` and `api.notion.com`, and to Google for Meet.
- One meeting at a time per process.
- Restart policy: unless-stopped is fine. A crash mid-meeting should reload SQLite state (T10) and be able to rejoin.

### Server assumptions (N4)

Ubuntu, SSH, we may install Docker. Adjust the README if the machine they give us differs.

## Acceptance criteria

- [ ] On a clean Ubuntu machine, the documented steps end with the bot joining a Meet URL.
- [ ] The image does not include a GPU runtime or an LLM server.
- [ ] Secrets stay in `.env`, which is git-ignored.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T15-U1 | unit | `docker compose config` or the run script’s env check | Missing key fails before the browser starts |
| T15-E1 | manual | Join a Meet from the server | Bot appears, consent posted |

## Dependencies

- T01, T02. Grows as the agent is wired. T16 runs against this setup.

## Related resources

- Playwright system dependencies: https://playwright.dev/python/docs/intro
- Xvfb: the `xvfb` package on Ubuntu
