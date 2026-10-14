export class AgentOpsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class HttpError extends AgentOpsError {
  readonly statusCode: number;
  readonly code: string;

  constructor(statusCode: number, code: string, message: string) {
    super(message);
    this.statusCode = statusCode;
    this.code = code;
  }
}

export class AuthError extends HttpError {}
export class NotFoundError extends HttpError {}
export class ConflictError extends HttpError {}
export class ContractError extends HttpError {}
export class QuotaError extends HttpError {}

const BY_STATUS: Record<number, new (statusCode: number, code: string, message: string) => HttpError> = {
  401: AuthError,
  404: NotFoundError,
  409: ConflictError,
  422: ContractError,
};

export function fromResponse(statusCode: number, code: string, message: string): HttpError {
  const cls = BY_STATUS[statusCode] ?? HttpError;
  return new cls(statusCode, code, message);
}

export class RunFailedError extends AgentOpsError {
  readonly error: string;

  constructor(error: string) {
    super(error);
    this.error = error;
  }
}

export class RunCancelledError extends AgentOpsError {}

export class TimeoutError extends AgentOpsError {}
