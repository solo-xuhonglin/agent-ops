import { RunCancelledError, RunFailedError, TimeoutError } from "./errors.ts";
import { Http, LinkDroppedError, type FrameSource } from "./http.ts";

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
  type: string;
  /** Null on stream frames. */
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
  idleTimeoutMs?: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class Client {
  readonly http: Http;
  readonly pollIntervalMs: number;
  readonly reconnectDelayMs: number;
  readonly idleTimeoutMs: number;

  constructor(key: string, options: ClientOptions) {
    this.http = new Http(options.baseUrl, key);
    this.pollIntervalMs = options.pollIntervalMs ?? 500;
    this.reconnectDelayMs = options.reconnectDelayMs ?? 1000;
    this.idleTimeoutMs = options.idleTimeoutMs ?? 30_000;
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

  private adoptState(data: any): void {
    const runId: string | null = data.run ?? null;
    this.currentRun = runId === null ? null : new Run(this.client, runId, this.id);
  }

  /** Subscribe to the session and resolve to an events-only generator.

   * Resolves once the subscription is live, so a submit right after cannot
   * miss stream frames. State frames, silence detection, and reconnects
   * stay internal; duplicates are suppressed by a per-run seq watermark.
   * HTTP errors raise, no retry.
   */
  async stream(options: { fromRun?: string; fromSeq?: number } = {}): Promise<AsyncGenerator<Event>> {
    const path = `/v1/sessions/${this.id}/events`;
    const connect = async (cursor: [string, number] | null): Promise<FrameSource> => {
      const params: Record<string, string> = {};
      if (cursor !== null) {
        params.from_run = cursor[0];
        params.from_seq = String(cursor[1]);
      }
      const link = await this.client.http.openEvents(path, params, this.client.idleTimeoutMs);
      try {
        const first = (await link.next()).payload as { type: string; data: any };
        this.adoptState(first.data); // first frame is always the state push
        return link;
      } catch (error) {
        link.close();
        throw error;
      }
    };

    let cursor: [string, number] | null = options.fromRun !== undefined ? [options.fromRun, options.fromSeq ?? 0] : null;
    let link = await connect(cursor);
    const watermark = new Map<string, number>();
    const session = this;

    return (async function* (): AsyncGenerator<Event> {
      try {
        for (;;) {
          let payload: { type: string; data: any };
          try {
            payload = (await link.next()).payload as { type: string; data: any };
          } catch (error) {
            // A dropped or silent link reconnects with the cursor; undici
            // surfaces network-level failures as TypeError ("fetch failed" /
            // "terminated"). Everything else (malformed payloads, bugs)
            // propagates, mirroring the Python SDK.
            if (!(error instanceof LinkDroppedError || (error instanceof TypeError && /fetch failed|terminated/.test(error.message)))) {
              throw error;
            }
            link.close();
            await sleep(session.client.reconnectDelayMs);
            link = await connect(cursor);
            continue;
          }
          if (payload.type === "state") {
            session.adoptState(payload.data);
            continue;
          }
          const data = payload.data;
          if (data.seq === undefined) {
            // Stream frames apply directly: no watermark, no cursor.
            yield { type: payload.type, runId: data.run_id ?? null, seq: null, data };
            continue;
          }
          const { run_id: runId, seq } = data;
          if (seq <= (watermark.get(runId) ?? 0)) continue;
          watermark.set(runId, seq);
          cursor = [runId, seq];
          yield { type: payload.type, runId, seq, data };
        }
      } finally {
        link.close();
      }
    })();
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
