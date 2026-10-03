"""Exceptions.

Every one carries the HTTP status and the parsed body, because when an
integration breaks at 3am the useful question is "what did the server actually
say", not "which exception class was this".
"""

from __future__ import annotations

from typing import Any, Optional


class FXNewsBiasError(Exception):
    """Base class. Catch this to catch everything from the client."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body


class AuthError(FXNewsBiasError):
    """401. The key is missing, malformed, revoked, or replaced by a newer one.

    A Pro subscription ending does not cause this: the key stays active and
    moves to the free tier, so Pro-only calls raise ``PlanError`` instead.
    """


class PlanError(FXNewsBiasError):
    """402 or 403. The key is valid but this endpoint is not on its plan."""


class RateLimitError(FXNewsBiasError):
    """429. The daily allowance is spent.

    ``retry_after`` is seconds until the window resets, taken from the server's
    Retry-After header rather than guessed.
    """

    def __init__(self, message: str, *, retry_after: int = 0, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ServerError(FXNewsBiasError):
    """5xx, or a network failure that survived the retries.

    A 400 raises ``RequestError``, a subclass of this one so that code written
    for 1.1.0 (where a 400 was a plain ``ServerError``) behaves exactly as
    before. Catch ``RequestError`` first to tell a wrong request from an outage.
    """


class RequestError(ServerError):
    """400. The request itself was wrong, for example an unknown market symbol.

    An answer about the call, not an outage, so it is never retried. It
    subclasses ``ServerError`` so code written for 1.1.0, where a 400 was a
    plain ``ServerError``, catches it exactly as before. ``message`` is the
    server's reason.
    """
