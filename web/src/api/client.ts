export class ApiClientError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

// Same-origin fetch against the config API; unwraps the error envelope.
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  }).catch(() => {
    throw new ApiClientError('网络错误，请稍后重试', 'network', 0)
  })

  if (response.status === 204) {
    return undefined as T
  }

  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const envelope = body?.error
    throw new ApiClientError(envelope?.message ?? '请求失败', envelope?.code ?? 'unknown', response.status)
  }
  return body as T
}
