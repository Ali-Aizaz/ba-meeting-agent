import { Container } from "@cloudflare/containers";

// Secrets are read from the Worker env at start. Values stay in `wrangler secret put`.
declare global {
  interface Env {
    MEET_URL?: string;
    BA_LLM__API_KEY?: string;
    BA_LLM__MODEL?: string;
    BA_NOTION__TOKEN?: string;
    BA_NOTION__DATABASES__SUMMARY?: string;
    BA_NOTION__DATABASES__TASKS?: string;
  }
}

const SECRET_KEYS = [
  "MEET_URL",
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

const CONTAINER_ENV: Record<string, string> = {
  BA_SERVER__STATUS_HOST: "0.0.0.0",
  BA_SERVER__STATUS_PORT: "8080",
  BA_MEETING__JOIN_MODE: "guest",
  BA_BROWSER__HEADLESS: "false",
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

  override async fetch(request: Request): Promise<Response> {
    this.envVars = containerEnv(this.env);
    try {
      await this.startAndWaitForPorts({
        cancellationOptions: {
          instanceGetTimeoutMS: INSTANCE_GET_TIMEOUT_MS,
          portReadyTimeoutMS: PORT_READY_TIMEOUT_MS,
          abort: request.signal,
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return new Response(`Failed to start container: ${message}\n`, {
        status: 500,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    return super.fetch(request);
  }
}

function containerEnv(env: Env): Record<string, string> {
  const values = { ...CONTAINER_ENV };
  for (const key of SECRET_KEYS) {
    const value = env[key];
    if (value) values[key] = value;
  }
  return values;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const container = env.BA_CONTAINER.get(env.BA_CONTAINER.idFromName("meeting"));
    return container.fetch(request);
  },
} satisfies ExportedHandler<Env>;
