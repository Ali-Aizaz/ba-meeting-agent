import { Container } from "@cloudflare/containers";

// Secrets are read from the Worker env at start. Values stay in `wrangler secret put`.
declare global {
  interface Env {
    MEET_URL?: string;
    BA_WEBHOOK_SECRET?: string;
    BA_MEETING__GOOGLE_EMAIL?: string;
    BA_MEETING__GOOGLE_PASSWORD?: string;
    BA_MEETING__GOOGLE_COOKIES_B64?: string;
    BA_LLM__API_KEY?: string;
    BA_LLM__MODEL?: string;
    BA_NOTION__TOKEN?: string;
    BA_NOTION__DATABASES__SUMMARY?: string;
    BA_NOTION__DATABASES__TASKS?: string;
  }
}

const SECRET_KEYS = [
  "MEET_URL",
  "BA_MEETING__GOOGLE_EMAIL",
  "BA_MEETING__GOOGLE_PASSWORD",
  "BA_MEETING__GOOGLE_COOKIES_B64",
  "BA_LLM__API_KEY",
  "BA_LLM__MODEL",
  "BA_NOTION__TOKEN",
  "BA_NOTION__DATABASES__SUMMARY",
  "BA_NOTION__DATABASES__TASKS",
] as const satisfies readonly (keyof Env)[];

// The status server listens before Chromium starts. Half a vCPU can still
// spend longer than the 20s default on the first process boot.
const INSTANCE_GET_TIMEOUT_MS = 30_000;
const PORT_READY_TIMEOUT_MS = 120_000;
const STOP_WAIT_TIMEOUT_MS = 10_000;
const STOP_POLL_INTERVAL_MS = 500;
const STORAGE_MEET_URL_KEY = "meet_url";
const STORAGE_ENV_SIG_KEY = "env_sig";
const WORKER_BUILD_SIG = "v7";
const MEET_HOST = ["meet", "google", "com"].join(".");
const MEET_CODE_PATH = /^(?:\/_?meet)?\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/i;

const CORS_HEADERS: Record<string, string> = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, HEAD, POST, OPTIONS",
  "access-control-allow-headers": "Content-Type, Authorization",
};

const CONTAINER_ENV: Record<string, string> = {
  BA_SERVER__STATUS_HOST: "0.0.0.0",
  BA_SERVER__STATUS_PORT: "8080",
  BA_MEETING__JOIN_MODE: "signed_in",
  BA_BROWSER__HEADLESS: "false",
  BA_BROWSER__KEEP_ALIVE: "true",
  BA_BROWSER__RECEIVE_VIDEO: "false",
  BA_LLM__MODEL: "gpt-5.5",
};

export class BaContainer extends Container<Env> {
  defaultPort = 8080;
  pingEndpoint = "localhost/status";
  // The base class sleeps after 10 minutes. A rehearsal needs longer.
  sleepAfter = "3h";
  // Outbound Meet, OpenAI, and Notion stay direct. Do not intercept HTTPS.
  enableInternet = true;

  private lastMonitorError: string | null = null;

  constructor(ctx: ConstructorParameters<typeof Container<Env>>[0], env: Env) {
    super(ctx, env);
    if (ctx.container) {
      const rawContainer = ctx.container;
      (this as unknown as { container: typeof rawContainer }).container = {
        get running() {
          return rawContainer.running;
        },
        get images() {
          return rawContainer.images;
        },
        start: (options?: Parameters<typeof rawContainer.start>[0]) => {
          this.lastMonitorError = null;
          const image = rawContainer.images?.bot;
          return rawContainer.start({
            ...options,
            enableInternet: options?.enableInternet ?? true,
            instance: "standard-1",
            ...(image ? { image } : {}),
          } as Parameters<typeof rawContainer.start>[0]);
        },
        monitor: () => {
          const p = rawContainer.monitor();
          p.catch((err: unknown) => {
            this.lastMonitorError = err instanceof Error ? err.message : String(err);
          });
          return p;
        },
        destroy(error?: unknown) {
          return rawContainer.destroy(error);
        },
        signal(signo: number) {
          return rawContainer.signal(signo);
        },
        getTcpPort(port: number) {
          return rawContainer.getTcpPort(port);
        },
        setInactivityTimeout(durationMs: number | bigint) {
          return rawContainer.setInactivityTimeout(durationMs);
        },
        interceptOutboundHttp(addr: string, binding: Fetcher) {
          return rawContainer.interceptOutboundHttp(addr, binding);
        },
        interceptAllOutboundHttp(binding: Fetcher) {
          return rawContainer.interceptAllOutboundHttp(binding);
        },
        interceptOutboundHttps(addr: string, binding: Fetcher) {
          return rawContainer.interceptOutboundHttps(addr, binding);
        },
      } as typeof rawContainer;
    }
  }

  private async ensureContainerReady(abortSignal?: AbortSignal): Promise<void> {
    const deadline = Date.now() + PORT_READY_TIMEOUT_MS;
    let lastError: unknown;
    while (Date.now() < deadline) {
      try {
        await this.startAndWaitForPorts({
          cancellationOptions: {
            instanceGetTimeoutMS: INSTANCE_GET_TIMEOUT_MS,
            portReadyTimeoutMS: PORT_READY_TIMEOUT_MS,
            ...(abortSignal ? { abort: abortSignal } : {}),
          },
          startOptions: {
            envVars: this.envVars,
          },
        });
        return;
      } catch (error) {
        lastError = error;
        const message = (error instanceof Error ? error.message : String(error)).toLowerCase();
        if (
          !message.includes("no container instance") &&
          !message.includes("try again later")
        ) {
          throw error;
        }
        if (abortSignal?.aborted) {
          throw error;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    }
    throw lastError ?? new Error("Timed out waiting for container instance");
  }

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/join") {
      return this.handleJoin(request);
    }

    const storedMeetUrl = await this.ctx.storage.get<string>(STORAGE_MEET_URL_KEY);
    this.envVars = containerEnv(this.env, storedMeetUrl);
    if (url.searchParams.get("peek") === "1") {
      const state = await this.getState();
      const isRunning =
        Boolean(this.ctx.container?.running) &&
        (state.status === "running" || state.status === "healthy");
      if (!isRunning) {
        return jsonResponse({
          meeting_id: "",
          captions_enabled: false,
          chat_enabled: false,
          idle: true,
          meet_url: this.envVars.MEET_URL || "",
        });
      }
      try {
        const statusRes = await super.fetch(new Request("http://localhost/status"));
        if (statusRes.ok) {
          const payload = (await statusRes.json()) as Record<string, unknown>;
          return jsonResponse({
            ...payload,
            idle: false,
            meet_url: this.envVars.MEET_URL || "",
          });
        }
      } catch {
        // Fall through to idle response if container port is not yet ready.
      }
      return jsonResponse({
        meeting_id: "",
        captions_enabled: false,
        chat_enabled: false,
        idle: true,
        meet_url: this.envVars.MEET_URL || "",
      });
    }

    if (!this.envVars.MEET_URL) {
      return jsonResponse({
        meeting_id: "",
        captions_enabled: false,
        chat_enabled: false,
        idle: true,
      });
    }
    try {
      await this.ensureContainerReady(request.signal);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const detail = this.lastMonitorError ? ` (monitor: ${this.lastMonitorError})` : "";
      return withCors(
        new Response(`Failed to start container: ${message}${detail}\n`, {
          status: 500,
          headers: { "content-type": "text/plain; charset=utf-8" },
        }),
      );
    }
    const response = await super.fetch(request);
    return withCors(response);
  }

  private async handleJoin(request: Request): Promise<Response> {
    let body: { url?: unknown; restart?: unknown };
    try {
      body = (await request.json()) as { url?: unknown; restart?: unknown };
    } catch {
      return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400);
    }

    const normalizedUrl = normalizeMeetUrl(typeof body.url === "string" ? body.url : "");
    if (!normalizedUrl) {
      return jsonResponse({ ok: false, error: "Invalid Google Meet code URL" }, 400);
    }

    const currentEnvSig = `${WORKER_BUILD_SIG}:${(this.env.BA_MEETING__GOOGLE_COOKIES_B64 || "").length}`;
    const previousEnvSig = await this.ctx.storage.get<string>(STORAGE_ENV_SIG_KEY);
    const previousUrl = await this.ctx.storage.get<string>(STORAGE_MEET_URL_KEY);
    const state = await this.getState();
    const isRunning =
      Boolean(this.ctx.container?.running) &&
      (state.status === "running" || state.status === "healthy");
    const switchedMeeting = Boolean(previousUrl && previousUrl !== normalizedUrl && isRunning);
    const needsRestart = Boolean(body.restart) || previousEnvSig !== currentEnvSig;

    await this.ctx.storage.put(STORAGE_MEET_URL_KEY, normalizedUrl);
    await this.ctx.storage.put(STORAGE_ENV_SIG_KEY, currentEnvSig);
    const nextEnv = containerEnv(this.env, normalizedUrl);
    this.envVars = nextEnv;

    if (isRunning && needsRestart) {
      await this.stopForNextMeeting();
    } else if (isRunning) {
      try {
        const joinRes = await super.fetch(
          new Request("http://localhost/join", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ url: normalizedUrl }),
          }),
        );
        if (joinRes.ok) {
          const payload = (await joinRes.json()) as Record<string, unknown>;
          return jsonResponse({
            ...payload,
            ok: true,
            meet_url: normalizedUrl,
            switched: switchedMeeting,
          });
        }
      } catch {
        // Fall back to container restart below if the running container port did not respond.
      }
      if (switchedMeeting) {
        await this.stopForNextMeeting();
      }
    }

    try {
      // Do not bind request.signal here so a tab refresh never aborts container boot.
      await this.ensureContainerReady();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const detail = this.lastMonitorError ? ` (monitor: ${this.lastMonitorError})` : "";
      return jsonResponse(
        { ok: false, error: `Failed to start container: ${message}${detail}` },
        500,
      );
    }

    let statusPayload: Record<string, unknown> = {};
    try {
      const statusRes = await super.fetch(new Request("http://localhost/status"));
      if (statusRes.ok) {
        statusPayload = (await statusRes.json()) as Record<string, unknown>;
      }
    } catch {
      // Status server just came up; return join metadata even if status parse fails.
    }

    return jsonResponse({
      ok: true,
      meet_url: normalizedUrl,
      switched: switchedMeeting,
      ...statusPayload,
    });
  }

  private async stopForNextMeeting(): Promise<void> {
    try {
      await this.stop("SIGTERM");
    } catch {
      // Ignore if already stopping.
    }
    const deadline = Date.now() + STOP_WAIT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (!this.ctx.container?.running) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, STOP_POLL_INTERVAL_MS));
    }
    try {
      await this.destroy();
    } catch {
      // Container already exited.
    }
    const destroyDeadline = Date.now() + 5_000;
    while (Date.now() < destroyDeadline && this.ctx.container?.running) {
      await new Promise((resolve) => setTimeout(resolve, STOP_POLL_INTERVAL_MS));
    }
  }
}

function containerEnv(env: Env, meetUrlOverride?: string): Record<string, string> {
  const values = { ...CONTAINER_ENV };
  for (const key of SECRET_KEYS) {
    const value = env[key];
    if (key === "MEET_URL" && value && value.endsWith("/abc-defg-hij")) {
      continue;
    }
    if (value) values[key] = value;
  }
  if (meetUrlOverride) {
    values.MEET_URL = meetUrlOverride;
  }
  return values;
}

function normalizeMeetUrl(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.hostname.toLowerCase() !== MEET_HOST) {
    return null;
  }
  const match = MEET_CODE_PATH.exec(parsed.pathname);
  if (!match) {
    return null;
  }
  return `https://${MEET_HOST}/${match[1].toLowerCase()}`;
}

function isAuthorized(request: Request, secret: string | undefined): boolean {
  const expected = secret?.trim();
  if (!expected) return true;
  const header = request.headers.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  const encoder = new TextEncoder();
  const a = encoder.encode(token);
  const b = encoder.encode(expected);
  if (a.byteLength !== b.byteLength) return false;
  return crypto.subtle.timingSafeEqual(a, b);
}

function withCors(response: Response): Response {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(CORS_HEADERS)) {
    headers.set(key, value);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function jsonResponse(payload: Record<string, unknown>, status = 200): Response {
  return withCors(
    new Response(JSON.stringify(payload), {
      status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    }),
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/join") {
      if (!isAuthorized(request, env.BA_WEBHOOK_SECRET)) {
        return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
      }
      const container = env.BA_CONTAINER.get(env.BA_CONTAINER.idFromName("meeting"));
      return container.fetch(request);
    }

    if (
      (request.method !== "GET" && request.method !== "HEAD") ||
      (pathname !== "/" && pathname !== "/status")
    ) {
      return withCors(new Response("Not Found\n", { status: 404 }));
    }
    const container = env.BA_CONTAINER.get(env.BA_CONTAINER.idFromName("meeting"));
    return container.fetch(request);
  },
} satisfies ExportedHandler<Env>;
