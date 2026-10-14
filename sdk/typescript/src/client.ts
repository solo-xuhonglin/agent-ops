import { RunCancelledError, RunFailedError, TimeoutError } from "./errors.ts";
import { Http } from "./http.ts";

export interface InputParam {
  key: string;
  type: string;
  required: boolean;
  description: string;
  default?: unknown;
}

export interface AgentView {
  input: InputParam[];
}

export interface Event {
  /** "state" for state pushes; otherwise the event type. */
  type: string;
  runId: string | null;
  seq: number | null;
  data: any;
}

export interface Result {
  output: unknown;
}

interface RunView {
  run_id: string;
  session_id: string;
  status: string;
  result?: unknown;
  error?: string | null;
  cancel_requested?: boolean;
}

export interface ClientOptions {
  baseUrl: string;
  pollIntervalMs?: number;
  reconnectDelayMs?: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class Client {
  readonly http: Http;
  readonly pollIntervalMs: number;
  readonly reconnectDelayMs: number;

  constructor(key: string, options: ClientOptions) {
    this.http = new Http(options.baseUrl, key);
    this.pollIntervalMs = options.pollIntervalMs ?? 500;
    this.reconnectDelayMs = options.reconnectDelayMs ?? 1000;
  }

  async getAgent(): Promise<AgentView> {
    return this.http.json("GET", "/v1/agent");
  }

  async newSession(): Promise<Session> {
    const data = await this.http.json("POST", "/v1/sessions");
    return new Session(this, data.session_id);
  }

  getSession(sessionId: string): Session {
    return new Session(this, sessionId);
  }

  async getRun(runId: string): Promise<Run> {
    return this.run(await this.http.json("GET", `/v1/runs/${runId}`));
  }

  run(view: RunView): Run {
    return new Run(this, view.run_id, view.session_id, view);
  }
}

export class Session {
  private client: Client;
  readonly id: string;
  currentRun: Run | null = null;

  constructor(client: Client, id: string) {
    this.client = client;
    this.id = id;
  }

  async submit(
    content: string,
    options: { params?: Record<string, unknown>; outputSchema?: unknown; requestId?: string } = {},
  ): Promise<Run> {
    const view = await this.client.http.json("POST", `/v1/sessions/${this.id}/runs`, {
      content,
      params: options.params ?? {},
      output_schema: options.outputSchema ?? null,
      request_id: options.requestId ?? null,
    });
    return this.client.run(view);
  }

  async *stream(_options: { fromRun?: string; fromSeq?: number } = {}): AsyncGenerator<Event> {
    throw new Error("not implemented");
  }
}

export class Run {
  private client: Client;
  readonly id: string;
  readonly sessionId: string;
  status?: string;
  result?: unknown;
  error?: string | null;
  cancelRequested?: boolean;

  constructor(client: Client, id: string, sessionId: string, view?: RunView) {
    this.client = client;
    this.id = id;
    this.sessionId = sessionId;
    if (view !== undefined) this.adopt(view);
  }

  private adopt(view: RunView): void {
    this.status = view.status;
    this.result = view.result;
    this.error = view.error;
    this.cancelRequested = view.cancel_requested;
  }

  async cancel(): Promise<void> {
    this.adopt(await this.client.http.json("POST", `/v1/runs/${this.id}/cancel`));
  }

  /** Poll the run until terminal; polling reads the log tail, the source of truth. */
  async wait(options: { timeoutMs?: number } = {}): Promise<Result> {
    const deadline = options.timeoutMs === undefined ? null : Date.now() + options.timeoutMs;
    for (;;) {
      this.adopt(await this.client.http.json("GET", `/v1/runs/${this.id}`));
      if (this.status === "succeeded") return { output: this.result };
      if (this.status === "failed") throw new RunFailedError(this.error ?? "run failed");
      if (this.status === "cancelled") throw new RunCancelledError(`run ${this.id} was cancelled`);
      if (deadline !== null && Date.now() >= deadline) throw new TimeoutError(`run ${this.id} still ${this.status}`);
      await sleep(this.client.pollIntervalMs);
    }
  }
}

export function createClient(key: string, options: ClientOptions): Client {
  return new Client(key, options);
}
