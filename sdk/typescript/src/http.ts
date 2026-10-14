import { TimeoutError, fromResponse } from "./errors.ts";

interface Frame {
  id: string | null;
  payload: unknown;
}

export class Http {
  private baseUrl: string;
  private key: string;
  private timeoutMs: number;

  constructor(baseUrl: string, key: string, timeoutMs = 10_000) {
    this.baseUrl = baseUrl;
    this.key = key;
    this.timeoutMs = timeoutMs;
  }

  async json(method: string, path: string, body?: unknown): Promise<any> {
    let response: Response;
    try {
      response = await fetch(this.baseUrl + path, {
        method,
        headers: {
          Authorization: `Bearer ${this.key}`,
          ...(body !== undefined && { "Content-Type": "application/json" }),
        },
        ...(body !== undefined && { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (error) {
      if (error instanceof Error && error.name === "TimeoutError") {
        throw new TimeoutError(`${method} ${path} timed out after ${this.timeoutMs}ms`);
      }
      throw error;
    }
    const payload: any = await response.json().catch(() => null);
    if (!response.ok) {
      // A proxy in front (nginx) can answer with an HTML error page; the
      // envelope is best-effort, the status always maps.
      throw fromResponse(response.status, payload?.error?.code ?? "", payload?.error?.message ?? `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  /** Yield SSE frames; the stream never ends on its own, so end-of-stream means the link dropped. */
  async *events(path: string, params: Record<string, string>): AsyncGenerator<Frame> {
    const url = new URL(path, this.baseUrl);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
    const response = await fetch(url, { headers: { Authorization: `Bearer ${this.key}` } });
    if (!response.ok) {
      const payload: any = await response.json().catch(() => null);
      throw fromResponse(response.status, payload?.error?.code ?? "", payload?.error?.message ?? "");
    }

    let buffer = "";
    let frameId: string | null = null;
    let dataLines: string[] = [];
    const decoder = new TextDecoder();
    const handleLine = (line: string): Frame | null => {
      if (line === "") {
        const frame = dataLines.length > 0 ? { id: frameId, payload: JSON.parse(dataLines.join("\n")) } : null;
        frameId = null;
        dataLines = [];
        return frame;
      }
      if (line.startsWith("id: ")) frameId = line.slice(4);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      return null;
    };

    const body = response.body as unknown as AsyncIterable<Uint8Array>;
    for await (const chunk of body) {
      buffer += decoder.decode(chunk, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        const frame = handleLine(line);
        if (frame !== null) yield frame;
        newline = buffer.indexOf("\n");
      }
    }
  }
}
