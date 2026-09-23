"""The HTTP client.

Uses ``requests`` when it is installed and falls back to the standard library
otherwise, so the package works in a bare environment without pulling
dependencies into someone's trading stack.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Dict, Iterator, Optional, Union

from .errors import AuthError, FXNewsBiasError, PlanError, RateLimitError, ServerError
from .models import (
    SentimentHistory,
    SentimentReading,
    Sentiment,
    SessionBias,
    SessionBiasHistory,
    SettledSession,
)

try:  # pragma: no cover - depends on the host environment
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None

DEFAULT_BASE_URL = "https://fxnewsbias.com"
_KEY_HELP = "Get a key at https://fxnewsbias.com/developers"

DateLike = Union[str, date, datetime]


class Client:
    """Talks to the FXNewsBias API.

        fx = Client("fxnb_live_...")
        fx = Client()  # reads FXNEWSBIAS_API_KEY from the environment

    Args:
        api_key: your key. Falls back to ``$FXNEWSBIAS_API_KEY``.
        timeout: seconds per request.
        max_retries: retries for 5xx and network failures only. A 401, 402, 403
            or 429 is never retried: those are answers, not failures, and hammering
            a 429 only spends the allowance you are already out of.
        base_url: override for testing.
        session: an existing ``requests.Session`` to reuse the connection pool.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        timeout: float = 20.0,
        max_retries: int = 2,
        base_url: str = DEFAULT_BASE_URL,
        session: Any = None,
    ) -> None:
        key = api_key or os.environ.get("FXNEWSBIAS_API_KEY", "")
        key = key.strip()
        if not key:
            raise AuthError(
                "No API key. Pass Client('fxnb_live_...') or set FXNEWSBIAS_API_KEY. "
                + _KEY_HELP
            )
        # Caught here rather than as a 401 forty lines into someone's backtest.
        if not key.startswith("fxnb_live_"):
            raise AuthError(
                f"That does not look like an API key (expected one starting "
                f"'fxnb_live_', got {key[:12]!r}...). " + _KEY_HELP
            )
        self._key = key
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = base_url.rstrip("/")
        self._session = session or (_requests.Session() if _requests else None)

        # Populated from the last response's headers.
        self.rate_limit: Optional[int] = None
        self.rate_remaining: Optional[int] = None
        self.rate_reset: Optional[int] = None

    # ---------------------------------------------------------------- public

    def sentiment(self) -> Sentiment:
        """Current news sentiment for the 8 majors. Available on every plan."""
        return Sentiment._from(self._get("/api/v1/sentiment"))

    def session_bias(self) -> SessionBias:
        """Per-pair directional read for the latest session. Pro plans only.

        Raises ``PlanError`` if the key's plan does not include it.
        """
        return SessionBias._from(self._get("/api/v1/session-bias"))

    def sentiment_history(
        self,
        currency: Optional[str] = None,
        *,
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> SentimentHistory:
        """One page of past readings, every 3-hour cycle. Pro plans only.

            h = fx.sentiment_history("EUR", start="2026-09-01", end="2026-09-07")
            for r in h:
                print(r.scored_at, r.score, r.bias)

        ``start`` and ``end`` are inclusive UTC dates, as ``"YYYY-MM-DD"`` or a
        ``date``. Leave both out for the last 30 days. Leave ``currency`` out
        for all 8. ``limit`` is up to 5000 rows per page (server default 500).

        One call is one request against the daily allowance. For a range longer
        than a page, ``iter_sentiment_history`` follows the pages for you.
        Raises ``PlanError`` on a free key.
        """
        params = _history_params(start, end, limit, offset)
        if currency:
            params["currency"] = currency.strip().upper()
        return SentimentHistory._from(self._get("/api/v1/sentiment/history", params))

    def iter_sentiment_history(
        self,
        currency: Optional[str] = None,
        *,
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        page_size: int = 5000,
    ) -> Iterator[SentimentReading]:
        """Every reading in the range, oldest first, fetching pages as needed.

        Each page is one request. At the default page size a full year for all
        8 currencies (about 23,000 rows) takes 5 requests.
        """
        offset = 0
        while True:
            page = self.sentiment_history(
                currency, start=start, end=end, limit=page_size, offset=offset
            )
            for row in page:
                yield row
            if page.paging.next_offset is None or not page.data:
                return
            offset = page.paging.next_offset

    def session_bias_history(
        self,
        pair: Optional[str] = None,
        *,
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> SessionBiasHistory:
        """One page of the settled session scorecard. Pro plans only.

            h = fx.session_bias_history("GBP/JPY", start="2026-09-01")
            print(h.summary.aligned_pct)       # over the whole range
            for s in h:
                print(s.session_date, s.session, s.tone, s.alignment)

        Settled sessions only, misses included: each row has the call, the
        entry and result prices, the move, and whether the move agreed with
        the call. ``summary`` covers the whole requested range, not just this
        page, so paging never changes the hit rate you are shown.
        Raises ``PlanError`` on a free key.
        """
        params = _history_params(start, end, limit, offset)
        if pair:
            params["pair"] = pair.strip().upper()
        return SessionBiasHistory._from(self._get("/api/v1/session-bias/history", params))

    def iter_session_bias_history(
        self,
        pair: Optional[str] = None,
        *,
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        page_size: int = 5000,
    ) -> Iterator[SettledSession]:
        """Every settled session in the range, oldest first, one request per page."""
        offset = 0
        while True:
            page = self.session_bias_history(
                pair, start=start, end=end, limit=page_size, offset=offset
            )
            for row in page:
                yield row
            if page.paging.next_offset is None or not page.data:
                return
            offset = page.paging.next_offset

    def follow(self, min_seconds: float = 60.0) -> Iterator[Sentiment]:
        """Yield a fresh reading each time the data actually changes.

            for s in fx.follow():
                print(s["AUD"].score)

        Sleeps until the server's own ``next_update_expected`` rather than
        polling on a timer. The scores only move every few hours, so a fixed
        60-second loop would spend a day's allowance on identical answers.
        ``min_seconds`` is a floor in case that hint is missing or already past.

        Blocks forever. Stop it with a break, or by killing the process.
        """
        while True:
            reading = self.sentiment()
            yield reading
            wait = reading.seconds_until_next_update()
            # A few seconds past the stated time: the value is written at that
            # instant and asking exactly on the boundary tends to return the
            # previous one.
            time.sleep(max(min_seconds, (wait or 0) + 15.0))

    def close(self) -> None:
        if self._session is not None and hasattr(self._session, "close"):
            self._session.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # never print the key
        return f"<fxnewsbias.Client key=***{self._key[-4:]} base={self.base_url}>"

    # --------------------------------------------------------------- private

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "User-Agent": _user_agent(),
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                status, body_text, resp_headers = self._request(url, headers)
            except Exception as exc:  # network-level failure
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.6 * (2 ** attempt))
                    continue
                raise ServerError(f"Could not reach {url}: {exc}") from exc

            self._read_rate_headers(resp_headers)

            try:
                body = json.loads(body_text) if body_text else {}
            except ValueError:
                body = {"raw": body_text}

            if status == 200:
                return body

            # Answers, not failures. Never retried.
            if status == 401:
                # Prefer the server's reason. It distinguishes a missing header
                # from a malformed one from a key that is no longer active, and
                # the last of those names the cause a working integration
                # actually hits: regenerating a key replaces the previous one.
                # Guessing locally threw all three away.
                raise AuthError(
                    (body.get("message")
                     or "Key rejected. It may have been revoked or replaced "
                        "by a newer one.") + " " + _KEY_HELP,
                    status=status,
                    body=body,
                )
            if status == 402:
                # Authenticated, but the key's plan does not include the
                # endpoint. A plan answer, not a server fault: without this it
                # fell through to ServerError and looked like an outage.
                raise PlanError(
                    body.get("message")
                    or "This endpoint is not included in your plan. "
                    "See https://fxnewsbias.com/pricing",
                    status=status,
                    body=body,
                )
            if status == 403:
                raise PlanError(
                    body.get("message")
                    or "This endpoint is not included in your plan. "
                    "See https://fxnewsbias.com/pricing",
                    status=status,
                    body=body,
                )
            if status == 429:
                retry_after = _int_or(resp_headers.get("retry-after"), 0) or int(
                    body.get("retry_after_seconds") or 0
                )
                raise RateLimitError(
                    f"Daily allowance spent. Resets in about "
                    f"{max(1, retry_after // 60)} minutes.",
                    retry_after=retry_after,
                    status=status,
                    body=body,
                )

            if 500 <= status < 600 and attempt < self.max_retries:
                time.sleep(0.6 * (2 ** attempt))
                continue

            raise ServerError(
                f"HTTP {status} from {path}: {body_text[:200]}",
                status=status,
                body=body,
            )

        raise ServerError(f"Request to {path} failed: {last_error}")

    def _request(self, url: str, headers: Dict[str, str]) -> tuple:
        if self._session is not None:
            r = self._session.get(url, headers=headers, timeout=self.timeout)
            return r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()}

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, text, hdrs
        except urllib.error.HTTPError as exc:
            # A 4xx is a real answer with a body worth reading, not an outage.
            text = exc.read().decode("utf-8", "replace")
            hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
            return exc.code, text, hdrs

    def _read_rate_headers(self, headers: Dict[str, str]) -> None:
        self.rate_limit = _int_or(headers.get("x-ratelimit-limit"), self.rate_limit)
        self.rate_remaining = _int_or(headers.get("x-ratelimit-remaining"), self.rate_remaining)
        self.rate_reset = _int_or(headers.get("x-ratelimit-reset"), self.rate_reset)


def _as_date(value: DateLike, name: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # Checked here so a typo fails before it spends a request. The server
    # rejects the same inputs; this just says so sooner.
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be a date as 'YYYY-MM-DD', got {value!r}.") from None


def _history_params(
    start: Optional[DateLike], end: Optional[DateLike], limit: Optional[int], offset: int
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if start is not None:
        params["from"] = _as_date(start, "start")
    if end is not None:
        params["to"] = _as_date(end, "end")
    if limit is not None:
        if not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError(f"limit must be a whole number from 1 to 5000, got {limit!r}.")
        params["limit"] = limit
    if offset:
        if not isinstance(offset, int) or offset < 0:
            raise ValueError(f"offset must be a whole number of 0 or more, got {offset!r}.")
        params["offset"] = offset
    return params


def _int_or(value: Any, default: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _user_agent() -> str:
    from . import __version__

    return f"fxnewsbias-python/{__version__}"
