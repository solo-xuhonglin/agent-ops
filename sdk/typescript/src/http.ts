import { TimeoutError, fromResponse } from "./errors.ts";

interface Frame {
  id: string | null;
  payload: unknown;
}

/** The SSE link went silent past the idle timeout or hit EOF — the stream
 * never ends on its own, so either way the caller's cue is the same:
 * reconnect with the cursor. */
export class LinkDroppedError extends Error {}

export interface FrameSource {
  next(): Promise<Frame>;
  close(): void;
}

interface ChunkRead {
  done: boolean;
  value?: Uint8Array;
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

  /** Open the SSE stream; resolves once the response headers are in. */
  async openEvents(path: string, params: Record<string, string>, idleTimeoutMs: number): Promise<FrameSource> {
    const url = new URL(path, this.baseUrl);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
    const response = await fetch(url, { headers: { Authorization: `Bearer ${this.key}` } });
    if (!response.ok) {
      const payload: any = await response.json().catch(() => null);
      throw fromResponse(response.status, payload?.error?.code ?? "", payload?.error?.message ?? "");
    }

    const reader = (response.body as unknown as ReadableStream<Uint8Array>).getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let frameId: string | null = null;
    let dataLines: string[] = [];
    // A read that lost an idle race stays pending here, so restarting the
    // timer never discards a chunk already in flight.
    let pending: Promise<ChunkRead> | null = null;
    const nextChunk = () => (pending ??= reader.read().finally(() => (pending = null)));

    const readLine = async (): Promise<string> => {
      for (;;) {
        const newline = buffer.indexOf("\n");
        if (newline >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          return line.endsWith("\r") ? line.slice(0, -1) : line;
        }
        let timer: ReturnType<typeof setTimeout> | undefined;
        const idle = new Promise<null>((resolve) => {
          timer = setTimeout(() => resolve(null), idleTimeoutMs);
        });
        let result: ChunkRead | null;
        try {
          result = await Promise.race([nextChunk(), idle]);
        } finally {
          clearTimeout(timer);
        }
        if (result === null) throw new LinkDroppedError(); // silent past the idle timeout
        if (result.done) throw new LinkDroppedError();
        buffer += decoder.decode(result.value, { stream: true });
      }
    };

    return {
      async next(): Promise<Frame> {
        for (;;) {
          const line = await readLine();
          if (line === "") {
            const frame = dataLines.length > 0 ? { id: frameId, payload: JSON.parse(dataLines.join("\n")) } : null;
            frameId = null;
            dataLines = [];
            if (frame !== null) return frame;
          } else if (line.startsWith("id: ")) frameId = line.slice(4);
          else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
        }
      },
      close() {
        void reader.cancel();
      },
    };
  }
}
