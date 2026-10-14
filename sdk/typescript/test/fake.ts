import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

export interface RecordedRequest {
  method: string;
  path: string;
  query: Record<string, string>;
  headers: Record<string, string>;
  json?: unknown;
}

interface JsonRoute {
  kind: "json";
  status: number;
  body: unknown;
  handler?: (request: RecordedRequest) => [number, unknown];
}

interface RawRoute {
  kind: "raw";
  scripts: Buffer[][];
  hold: number;
  drop: boolean;
}

type Route = JsonRoute | RawRoute;

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class FakeApi {
  requests: RecordedRequest[] = [];
  private routes = new Map<string, Route>();
  private server = createServer((req, res) => void this.dispatch(req, res));
  private listening: Promise<void>;

  private constructor() {
    this.listening = new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
  }

  static async start(): Promise<FakeApi> {
    const api = new FakeApi();
    await api.listening;
    return api;
  }

  get baseUrl(): string {
    const address = this.server.address();
    if (address === null || typeof address === "string") throw new Error("not listening");
    return `http://127.0.0.1:${address.port}`;
  }

  route(method: string, path: string, opts: { status?: number; body?: unknown; handler?: (r: RecordedRequest) => [number, unknown] } = {}): void {
    this.routes.set(`${method} ${path}`, { kind: "json", status: opts.status ?? 200, body: opts.body, handler: opts.handler });
  }

  /** Serve GET as text/event-stream; each connection pops the next script (last repeats). */
  routeRaw(path: string, scripts: Buffer[][], opts: { hold?: number; drop?: boolean } = {}): void {
    this.routes.set(`GET ${path}`, { kind: "raw", scripts, hold: opts.hold ?? 5, drop: opts.drop ?? false });
  }

  close(): void {
    this.server.close();
  }

  private async dispatch(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://fake");
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    const record: RecordedRequest = {
      method: req.method ?? "GET",
      path: url.pathname,
      query: Object.fromEntries(url.searchParams.entries()),
      headers: Object.fromEntries(
        Object.entries(req.headers).map(([key, value]) => [key, Array.isArray(value) ? value.join(",") : (value ?? "")]),
      ),
      json: chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString()) : undefined,
    };
    this.requests.push(record);

    const route = this.routes.get(`${record.method} ${record.path}`);
    if (route === undefined) {
      sendJson(res, 404, { error: { code: "not_found", message: `no route for ${record.method} ${record.path}` } });
      return;
    }
    if (route.kind === "raw") {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      const script = route.scripts.length > 1 ? (route.scripts.shift() as Buffer[]) : route.scripts[0];
      try {
        for (const chunk of script) res.write(chunk);
      } catch {
        return; // the client went away
      }
      if (route.drop) {
        res.end();
      } else {
        await sleep(route.hold * 1000);
        res.end();
      }
      return;
    }
    const [status, body] = route.handler ? route.handler(record) : [route.status, route.body];
    sendJson(res, status, body);
  }
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": payload.length });
  res.end(payload);
}
