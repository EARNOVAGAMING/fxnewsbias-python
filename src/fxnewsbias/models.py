"""Response types.

Plain dataclasses, no pydantic, no dependencies. Every one keeps the raw dict
it came from in ``.raw``, so a field added to the API later is still reachable
without waiting for a release of this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional


_FRACTION = re.compile(r"\.(\d+)")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse the API's ISO-8601 timestamps into aware datetimes.

    History rows carry anywhere from 0 to 6 fractional digits ('.49',
    '.129317'). Python before 3.11 only accepts exactly 3 or 6, so the
    fraction is padded or trimmed to 6 first; without that, those rows
    silently came back as None on 3.8 to 3.10.
    """
    if not value:
        return None
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], value.replace("Z", "+00:00"), count=1)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class Currency:
    """One currency's news-sentiment reading."""

    currency: str
    score: int
    bias: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_bullish(self) -> bool:
        return self.bias == "Bullish"

    @property
    def is_bearish(self) -> bool:
        return self.bias == "Bearish"

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "Currency":
        return cls(
            currency=str(d.get("currency", "")),
            score=int(d.get("score", 0)),
            bias=str(d.get("bias", "")),
            raw=d,
        )


@dataclass(frozen=True)
class Sentiment:
    """A full /api/v1/sentiment response.

    Iterate it for the currencies, or index it by code:

        s = fx.sentiment()
        s["AUD"].score
        [c.currency for c in s if c.is_bullish]
    """

    generated_at: Optional[datetime]
    next_update_expected: Optional[datetime]
    data: List[Currency]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[Currency]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, code: str) -> Currency:
        want = code.upper()
        for c in self.data:
            if c.currency.upper() == want:
                return c
        raise KeyError(f"{code!r} not in this response: {[c.currency for c in self.data]}")

    def scores(self) -> Dict[str, int]:
        """{'AUD': 68, 'USD': 55, ...} for when a plain dict is easier."""
        return {c.currency: c.score for c in self.data}

    def spread(self, pair: str) -> int:
        """Base score minus quote score for a pair like 'AUD/USD' or 'audusd'.

        Positive means the news leans toward the base currency strengthening.
        This is the number most strategies actually want: one figure that says
        which way the news points for the thing you are about to trade.
        """
        base, quote = _split_pair(pair)
        # Checked here rather than letting the lookup raise KeyError: a caller
        # who mistyped a pair wants "that is not a pair", not a dict error.
        for code in (base, quote):
            if code not in self.scores():
                raise ValueError(
                    f"Cannot read {pair!r} as a currency pair: {code!r} is not one of "
                    f"{sorted(self.scores())}."
                )
        return self[base].score - self[quote].score

    def favours(self, pair: str, threshold: int = 10) -> Optional[str]:
        """'long', 'short', or None when the news does not lean far enough.

        The threshold is deliberately yours to choose. Ten points is a starting
        point, not a recommendation; tune it against your own results.
        """
        s = self.spread(pair)
        if s >= threshold:
            return "long"
        if s <= -threshold:
            return "short"
        return None

    def seconds_until_next_update(self) -> Optional[float]:
        """How long until the data changes, or None if the API did not say.

        Never negative: a stale hint returns 0.0 rather than a negative sleep.
        """
        if not self.next_update_expected:
            return None
        delta = (self.next_update_expected - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "Sentiment":
        return cls(
            generated_at=_parse_ts(d.get("generated_at")),
            next_update_expected=_parse_ts(d.get("next_update_expected")),
            data=[Currency._from(x) for x in (d.get("data") or [])],
            raw=d,
        )


@dataclass(frozen=True)
class PairBias:
    """One pair's directional read for a session."""

    pair: str
    tone: str
    strength: int
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "PairBias":
        return cls(
            pair=str(d.get("pair", "")),
            tone=str(d.get("tone", "")),
            strength=int(d.get("strength", 0)),
            raw=d,
        )


@dataclass(frozen=True)
class SessionBias:
    """A full /api/v1/session-bias response. Pro plans only."""

    session: Optional[str]
    session_date: Optional[str]
    generated_at: Optional[datetime]
    data: List[PairBias]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[PairBias]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "SessionBias":
        return cls(
            session=d.get("session"),
            session_date=d.get("session_date"),
            generated_at=_parse_ts(d.get("generated_at")),
            data=[PairBias._from(x) for x in (d.get("data") or [])],
            raw=d,
        )


@dataclass(frozen=True)
class Paging:
    """Where a history page sits in the full result."""

    offset: int
    limit: int
    returned: int
    total_matching: Optional[int]
    has_more: bool
    next_offset: Optional[int]

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "Paging":
        total = d.get("total_matching")
        nxt = d.get("next_offset")
        return cls(
            offset=int(d.get("offset") or 0),
            limit=int(d.get("limit") or 0),
            returned=int(d.get("returned") or 0),
            total_matching=None if total is None else int(total),
            has_more=bool(d.get("has_more")),
            next_offset=None if nxt is None else int(nxt),
        )


@dataclass(frozen=True)
class SentimentReading:
    """One currency's score from one past 3-hour cycle."""

    currency: str
    score: int
    bias: str
    scored_at: Optional[datetime]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_bullish(self) -> bool:
        return self.bias == "Bullish"

    @property
    def is_bearish(self) -> bool:
        return self.bias == "Bearish"

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "SentimentReading":
        return cls(
            currency=str(d.get("currency", "")),
            score=int(d.get("score", 0)),
            bias=str(d.get("bias", "")),
            scored_at=_parse_ts(d.get("scored_at")),
            raw=d,
        )


@dataclass(frozen=True)
class SentimentHistory:
    """One page of /api/v1/sentiment/history. Pro plans only.

    Oldest first. Iterate it for the readings, or group them:

        h = fx.sentiment_history(start="2026-09-01")
        h.by_currency()["EUR"]      # EUR readings in time order
    """

    generated_at: Optional[datetime]
    coverage_from: Optional[str]
    paging: Paging
    data: List[SentimentReading]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[SentimentReading]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def by_currency(self) -> Dict[str, List[SentimentReading]]:
        out: Dict[str, List[SentimentReading]] = {}
        for r in self.data:
            out.setdefault(r.currency, []).append(r)
        return out

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "SentimentHistory":
        return cls(
            generated_at=_parse_ts(d.get("generated_at")),
            coverage_from=d.get("coverage_from"),
            paging=Paging._from(d.get("paging") or {}),
            data=[SentimentReading._from(x) for x in (d.get("data") or [])],
            raw=d,
        )


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SettledSession:
    """One pair's call for one finished session, and how it turned out.

    ``alignment`` is ``'aligned'`` when price moved the way the call said,
    ``'contra'`` when it moved against it, ``'quiet'`` when there was a call
    but the move was too small to count, and ``'na'`` when the call was
    Neutral, so there was nothing to score.
    """

    pair: str
    session: str
    session_date: str
    tone: str
    strength: int
    entry_price: Optional[float]
    entry_time: Optional[datetime]
    result_price: Optional[float]
    result_time: Optional[datetime]
    move_pct: Optional[float]
    move_pips: Optional[float]
    alignment: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_aligned(self) -> bool:
        return self.alignment == "aligned"

    @property
    def is_directional(self) -> bool:
        """True for calls that count toward the hit rate (aligned or contra)."""
        return self.alignment in ("aligned", "contra")

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "SettledSession":
        return cls(
            pair=str(d.get("pair", "")),
            session=str(d.get("session", "")),
            session_date=str(d.get("session_date", "")),
            tone=str(d.get("tone", "")),
            strength=int(d.get("strength") or 0),
            entry_price=_num(d.get("entry_price")),
            entry_time=_parse_ts(d.get("entry_time")),
            result_price=_num(d.get("result_price")),
            result_time=_parse_ts(d.get("result_time")),
            move_pct=_num(d.get("move_pct")),
            move_pips=_num(d.get("move_pips")),
            alignment=str(d.get("alignment", "")),
            raw=d,
        )


@dataclass(frozen=True)
class ScorecardSummary:
    """Hit rate over the whole requested range, not just the returned page.

    ``aligned + contra == directional``. Quiet and Neutral (``'na'``) sessions
    are settled but not directional, so they are left out of ``aligned_pct``.
    """

    settled: int
    aligned: int
    contra: int
    directional: int
    aligned_pct: Optional[float]

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "ScorecardSummary":
        pct = d.get("aligned_pct")
        return cls(
            settled=int(d.get("settled") or 0),
            aligned=int(d.get("aligned") or 0),
            contra=int(d.get("contra") or 0),
            directional=int(d.get("directional") or 0),
            aligned_pct=None if pct is None else float(pct),
        )


@dataclass(frozen=True)
class SessionBiasHistory:
    """One page of /api/v1/session-bias/history. Pro plans only.

    ``summary`` is None when the server could not count the range, rather than
    a breakdown whose parts might not add up.
    """

    generated_at: Optional[datetime]
    coverage_from: Optional[str]
    summary: Optional[ScorecardSummary]
    paging: Paging
    data: List[SettledSession]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[SettledSession]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def _from(cls, d: Dict[str, Any]) -> "SessionBiasHistory":
        summ = d.get("summary")
        return cls(
            generated_at=_parse_ts(d.get("generated_at")),
            coverage_from=d.get("coverage_from"),
            summary=ScorecardSummary._from(summ) if isinstance(summ, dict) else None,
            paging=Paging._from(d.get("paging") or {}),
            data=[SettledSession._from(x) for x in (d.get("data") or [])],
            raw=d,
        )


def _split_pair(pair: str) -> tuple:
    """Accept 'AUD/USD', 'AUDUSD', 'aud_usd', 'aud-usd'."""
    cleaned = pair.upper().replace("/", "").replace("_", "").replace("-", "").strip()
    if len(cleaned) != 6:
        raise ValueError(
            f"Cannot read {pair!r} as a currency pair. Try 'AUD/USD' or 'AUDUSD'."
        )
    return cleaned[:3], cleaned[3:]
