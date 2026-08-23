"""Response types.

Plain dataclasses, no pydantic, no dependencies. Every one keeps the raw dict
it came from in ``.raw``, so a field added to the API later is still reachable
without waiting for a release of this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse the API's ISO-8601 timestamps into aware datetimes."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def _split_pair(pair: str) -> tuple:
    """Accept 'AUD/USD', 'AUDUSD', 'aud_usd', 'aud-usd'."""
    cleaned = pair.upper().replace("/", "").replace("_", "").replace("-", "").strip()
    if len(cleaned) != 6:
        raise ValueError(
            f"Cannot read {pair!r} as a currency pair. Try 'AUD/USD' or 'AUDUSD'."
        )
    return cleaned[:3], cleaned[3:]
