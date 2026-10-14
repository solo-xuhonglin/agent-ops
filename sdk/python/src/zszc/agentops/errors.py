class AgentOpsError(Exception):
    """Base for everything this SDK raises."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class HttpError(AgentOpsError):
    """A non-2xx response carrying the contract's error envelope."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AuthError(HttpError):
    pass


class NotFoundError(HttpError):
    pass


class ConflictError(HttpError):
    pass


class ContractError(HttpError):
    pass


class QuotaError(HttpError):
    pass


_BY_STATUS = {401: AuthError, 404: NotFoundError, 409: ConflictError, 422: ContractError}


def from_response(status_code: int, code: str, message: str) -> HttpError:
    return _BY_STATUS.get(status_code, HttpError)(status_code, code, message)


class RunFailed(AgentOpsError):
    """The run ended failed; raised by wait()."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class RunCancelled(AgentOpsError):
    """The run ended cancelled; raised by wait()."""


class Timeout(AgentOpsError):
    """wait() gave up; the run keeps running."""
