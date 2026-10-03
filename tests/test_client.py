"""Tests run against a fake transport, so they never touch the live API and
never need a key. Anyone can clone and `pytest` this repo.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from fxnewsbias import (
    AuthError,
    Client,
    PlanError,
    RateLimitError,
    ServerError,
)

SENTIMENT_BODY = {
    "schema": "fxnb.sentiment.v1",
    "generated_at": "2026-08-23T03:00:12Z",
    "next_update_expected": "2026-08-23T06:00:00Z",
    "attribution": {"required": True, "text": "Data by FXNewsBias", "url": "https://fxnewsbias.com"},
    "data": [
        {"currency": "AUD", "score": 68, "bias": "Bullish"},
        {"currency": "USD", "score": 55, "bias": "Neutral"},
        {"currency": "EUR", "score": 52, "bias": "Neutral"},
        {"currency": "GBP", "score": 50, "bias": "Neutral"},
        {"currency": "NZD", "score": 50, "bias": "Neutral"},
        {"currency": "JPY", "score": 48, "bias": "Bearish"},
        {"currency": "CHF", "score": 45, "bias": "Bearish"},
        {"currency": "CAD", "score": 35, "bias": "Bearish"},
    ],
}

VALID_KEY = "fxnb_live_" + "a" * 64


class FakeSession:
    """Stands in for requests.Session."""

    def __init__(self, status=200, body=None, headers=None, fail_times=0):
        self.status = status
        self.body = SENTIMENT_BODY if body is None else body
        self.headers = headers or {}
        self.fail_times = fail_times
        self.calls = 0
        self.seen_headers = None

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        self.seen_headers = headers
        if self.calls <= self.fail_times:
            raise OSError("connection reset")
        return FakeResponse(self.status, self.body, self.headers)

    def close(self):
        pass


class FakeResponse:
    def __init__(self, status, body, headers):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = headers


def make(**kw):
    return Client(VALID_KEY, session=FakeSession(**kw), max_retries=kw.pop("max_retries", 2))


# ------------------------------------------------------------------ key checks

def test_missing_key_is_caught_before_any_request(monkeypatch):
    monkeypatch.delenv("FXNEWSBIAS_API_KEY", raising=False)
    with pytest.raises(AuthError) as e:
        Client()
    assert "developers" in str(e.value)


def test_obviously_wrong_key_is_caught_locally(monkeypatch):
    monkeypatch.delenv("FXNEWSBIAS_API_KEY", raising=False)
    with pytest.raises(AuthError) as e:
        Client("sk_live_oops")
    assert "fxnb_live_" in str(e.value)


def test_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("FXNEWSBIAS_API_KEY", VALID_KEY)
    assert Client(session=FakeSession())._key == VALID_KEY


def test_repr_never_leaks_the_key():
    assert VALID_KEY not in repr(make())


def test_bearer_header_is_sent():
    c = make()
    c.sentiment()
    assert c._session.seen_headers["Authorization"] == f"Bearer {VALID_KEY}"


# --------------------------------------------------------------- happy parsing

def test_sentiment_parses_all_eight():
    s = make().sentiment()
    assert len(s) == 8
    assert s["AUD"].score == 68
    assert s["aud"].is_bullish          # lookup is case-insensitive
    assert s["CAD"].is_bearish


def test_scores_dict_and_iteration():
    s = make().sentiment()
    assert s.scores()["JPY"] == 48
    assert [c.currency for c in s if c.is_bullish] == ["AUD"]


def test_unknown_currency_says_what_was_available():
    # XTS is the ISO 4217 code reserved for testing, so it can never be added.
    s = make().sentiment()
    with pytest.raises(KeyError) as e:
        s["XTS"]
    assert "AUD" in str(e.value)
    assert "markets()" not in str(e.value)         # no gold hint for a plain typo


def test_gold_lookup_points_to_markets():
    s = make().sentiment()
    with pytest.raises(KeyError) as e:
        s["xau"]
    assert "AUD" in str(e.value) and "fx.markets()" in str(e.value)


def test_gold_spread_points_to_markets():
    s = make().sentiment()
    with pytest.raises(ValueError) as e:
        s.spread("XAU/USD")
    assert "currency pair" in str(e.value)
    assert "fx.markets()" in str(e.value) and "pair.gap" in str(e.value)
    with pytest.raises(ValueError) as e:
        s.favours("usdxau")
    assert "fx.markets()" in str(e.value)


def test_raw_is_kept_for_fields_this_version_does_not_know():
    s = make().sentiment()
    assert s.raw["attribution"]["url"] == "https://fxnewsbias.com"


# ------------------------------------------------------------------- the maths

@pytest.mark.parametrize("pair", ["AUD/USD", "AUDUSD", "aud-usd", "aud_usd"])
def test_pair_formats_all_accepted(pair):
    assert make().sentiment().spread(pair) == 13     # 68 - 55


def test_spread_is_directional():
    s = make().sentiment()
    assert s.spread("USD/AUD") == -13


def test_favours_respects_the_threshold():
    s = make().sentiment()
    assert s.favours("AUD/USD", threshold=10) == "long"
    assert s.favours("AUD/USD", threshold=20) is None   # 13 < 20
    assert s.favours("USD/AUD", threshold=10) == "short"


def test_favours_is_none_when_the_news_is_flat():
    s = make().sentiment()
    assert s.favours("GBP/NZD") is None                 # 50 vs 50


def test_malformed_pair_is_rejected_clearly():
    with pytest.raises(ValueError) as e:
        make().sentiment().spread("BANANA")
    assert "currency pair" in str(e.value)


# -------------------------------------------------------------- polite polling

def test_seconds_until_next_update_never_negative():
    body = dict(SENTIMENT_BODY)
    body["next_update_expected"] = (
        datetime.now(timezone.utc) - timedelta(hours=5)
    ).isoformat().replace("+00:00", "Z")
    s = Client(VALID_KEY, session=FakeSession(body=body)).sentiment()
    assert s.seconds_until_next_update() == 0.0


def test_seconds_until_next_update_is_none_when_absent():
    body = {k: v for k, v in SENTIMENT_BODY.items() if k != "next_update_expected"}
    s = Client(VALID_KEY, session=FakeSession(body=body)).sentiment()
    assert s.seconds_until_next_update() is None


# ----------------------------------------------------------------- error paths

def test_401_falls_back_when_the_server_says_nothing():
    with pytest.raises(AuthError) as e:
        make(status=401, body={"error": "unauthorized"}).sentiment()
    # An ended subscription downgrades the key, it never 401s, so the
    # fallback must not blame the subscription.
    assert "revoked" in str(e.value) and "subscription" not in str(e.value)
    assert "fxnewsbias.com/developers" in str(e.value)


def test_401_prefers_the_server_message():
    # The server distinguishes missing, malformed and inactive. Guessing
    # locally threw that away, which is the whole point of this test.
    msg = ("This key is not active. Regenerating a key replaces the previous "
           "one, and a revoked key stops working immediately.")
    with pytest.raises(AuthError) as e:
        make(status=401, body={"error": "unauthorized", "message": msg}).sentiment()
    assert "Regenerating a key replaces the previous one" in str(e.value)
    assert "may have ended" not in str(e.value)


def test_402_is_a_plan_answer_not_a_server_error():
    with pytest.raises(PlanError) as e:
        make(status=402, body={"error": "upgrade-required",
                               "message": "Sentiment history is included with FXNewsBias Pro."}).sentiment()
    assert "included with FXNewsBias Pro" in str(e.value)
    assert e.value.status == 402


def test_403_carries_the_server_message():
    with pytest.raises(PlanError) as e:
        make(status=403, body={"error": "pro-only", "message": "Pro tier only."}).sentiment()
    assert "Pro tier only." in str(e.value)


def test_429_exposes_retry_after_from_the_header():
    c = make(status=429, body={"error": "rate-limited"}, headers={"retry-after": "3600"})
    with pytest.raises(RateLimitError) as e:
        c.sentiment()
    assert e.value.retry_after == 3600


def test_429_falls_back_to_the_body_when_no_header():
    c = make(status=429, body={"error": "rate-limited", "retry_after_seconds": 120})
    with pytest.raises(RateLimitError) as e:
        c.sentiment()
    assert e.value.retry_after == 120


def test_answers_are_never_retried():
    """A 401 is a fact. Retrying it wastes time and, for 429, allowance."""
    c = make(status=401, body={"error": "unauthorized"})
    with pytest.raises(AuthError):
        c.sentiment()
    assert c._session.calls == 1


def test_network_failure_is_retried_then_succeeds():
    c = Client(VALID_KEY, session=FakeSession(fail_times=2), max_retries=2)
    assert len(c.sentiment()) == 8
    assert c._session.calls == 3


def test_network_failure_past_the_retries_raises():
    c = Client(VALID_KEY, session=FakeSession(fail_times=9), max_retries=1)
    with pytest.raises(ServerError):
        c.sentiment()


def test_non_json_body_does_not_explode():
    c = make(status=502, body="<html>bad gateway</html>")
    with pytest.raises(ServerError) as e:
        c.sentiment()
    assert e.value.status == 502


# ------------------------------------------------------------ rate headers

def test_rate_headers_are_exposed_after_a_call():
    c = make(headers={
        "x-ratelimit-limit": "1000",
        "x-ratelimit-remaining": "994",
        "x-ratelimit-reset": "1787529600",
    })
    c.sentiment()
    assert (c.rate_limit, c.rate_remaining) == (1000, 994)


def test_missing_rate_headers_leave_the_values_alone():
    c = make()
    c.sentiment()
    assert c.rate_limit is None


# ---------------------------------------------------------------- session bias

def test_session_bias_parses():
    body = {
        "schema": "fxnb.session_bias.v1",
        "generated_at": "2026-08-23T03:00:12Z",
        "session": "asean",
        "session_date": "2026-08-23",
        "data": [{"pair": "AUD/USD", "tone": "Bullish", "strength": 4}],
    }
    sb = Client(VALID_KEY, session=FakeSession(body=body)).session_bias()
    assert sb.session == "asean"
    assert len(sb) == 1
    assert sb.data[0].strength == 4


def test_context_manager_closes():
    with Client(VALID_KEY, session=FakeSession()) as c:
        assert len(c.sentiment()) == 8


# ------------------------------------------------------------------ history

from urllib.parse import parse_qs, urlparse  # noqa: E402

from fxnewsbias import (  # noqa: E402
    SentimentHistory,
    SessionBiasHistory,
)


def _hist_page(rows, offset, limit, total):
    consumed = offset + len(rows)
    more = consumed < total
    return {
        "schema": "fxnb.sentiment.history.v1",
        "generated_at": "2026-09-23T10:00:00Z",
        "query": {"from": "2026-09-01", "to": "2026-09-02", "currency": None},
        "coverage_from": "2026-05-19",
        "paging": {
            "offset": offset, "limit": limit, "returned": len(rows),
            "total_matching": total, "has_more": more,
            "next_offset": consumed if more else None,
        },
        "data": rows,
    }


class PagingSession:
    """Serves a fixed list of rows in pages, honouring limit and offset."""

    def __init__(self, rows, total=None):
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        q = parse_qs(urlparse(url).query)
        limit = int(q.get("limit", ["500"])[0])
        offset = int(q.get("offset", ["0"])[0])
        body = _hist_page(self.rows[offset:offset + limit], offset, limit, self.total)
        return FakeResponse(200, body, {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "990"})

    def close(self):
        pass


READINGS = [
    {"currency": c, "score": 40 + i, "bias": "Neutral", "scored_at": f"2026-09-01T{h:02d}:00:00+00:00"}
    for h in (0, 3, 6) for i, c in enumerate(["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"])
]


def test_sentiment_history_builds_the_query():
    sess = PagingSession(READINGS)
    fx = Client(VALID_KEY, session=sess)
    h = fx.sentiment_history("eur", start="2026-09-01", end=datetime(2026, 9, 2, 15, tzinfo=timezone.utc), limit=100)
    q = parse_qs(urlparse(sess.urls[0]).query)
    assert urlparse(sess.urls[0]).path == "/api/v1/sentiment/history"
    assert q == {"currency": ["EUR"], "from": ["2026-09-01"], "to": ["2026-09-02"], "limit": ["100"]}
    assert isinstance(h, SentimentHistory)
    assert h.coverage_from == "2026-05-19"


def test_sentiment_history_parses_rows_and_groups():
    fx = Client(VALID_KEY, session=PagingSession(READINGS))
    h = fx.sentiment_history()
    assert len(h) == 24
    assert h.paging.total_matching == 24 and h.paging.next_offset is None
    first = h.data[0]
    assert first.currency == "USD" and first.score == 40
    assert first.scored_at == datetime(2026, 9, 1, 0, tzinfo=timezone.utc)
    groups = h.by_currency()
    assert set(groups) == {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
    assert [r.scored_at.hour for r in groups["EUR"]] == [0, 3, 6]


def test_no_params_sends_a_bare_path():
    sess = PagingSession(READINGS)
    Client(VALID_KEY, session=sess).sentiment_history()
    assert sess.urls[0].endswith("/api/v1/sentiment/history")


def test_iter_sentiment_history_follows_every_page_once():
    sess = PagingSession(READINGS)
    fx = Client(VALID_KEY, session=sess)
    got = list(fx.iter_sentiment_history(page_size=10))
    assert len(got) == 24
    assert [r.raw for r in got] == READINGS          # no row lost or repeated
    assert len(sess.urls) == 3                         # 10 + 10 + 4
    offsets = [parse_qs(urlparse(u).query).get("offset", ["0"])[0] for u in sess.urls]
    assert offsets == ["0", "10", "20"]


def test_iter_stops_on_an_empty_page_even_if_server_says_more():
    sess = PagingSession([], total=50)                 # claims more, returns none
    assert list(Client(VALID_KEY, session=sess).iter_sentiment_history()) == []
    assert len(sess.urls) == 1


@pytest.mark.parametrize("kw", [
    {"start": "2026-13-01"}, {"end": "yesterday"}, {"limit": 0}, {"limit": 5001}, {"offset": -1},
])
def test_bad_history_arguments_fail_before_any_request(kw):
    sess = PagingSession(READINGS)
    with pytest.raises(ValueError):
        Client(VALID_KEY, session=sess).sentiment_history(**kw)
    assert sess.urls == []


SESSIONS_BODY = {
    "schema": "fxnb.session_bias.history.v1",
    "generated_at": "2026-09-23T10:00:00Z",
    "query": {"from": "2026-09-01", "to": "2026-09-23", "pair": "GBP/JPY", "status": "settled"},
    "coverage_from": "2026-08-06",
    "paging": {"offset": 0, "limit": 500, "returned": 3, "total_matching": 3, "has_more": False, "next_offset": None},
    "summary": {"settled": 3, "aligned": 1, "contra": 1, "directional": 2, "aligned_pct": 50.0},
    "data": [
        {"pair": "GBP/JPY", "session": "london", "session_date": "2026-09-01", "tone": "Bullish", "strength": 2,
         "entry_price": "209.10", "entry_time": "2026-09-01T07:00:00+00:00", "result_price": 209.9,
         "result_time": "2026-09-01T12:00:00+00:00", "move_pct": 0.38, "move_pips": 80, "alignment": "aligned", "status": "settled"},
        {"pair": "GBP/JPY", "session": "newyork", "session_date": "2026-09-01", "tone": "Bearish", "strength": 1,
         "entry_price": 209.9, "entry_time": "2026-09-01T12:00:00+00:00", "result_price": 210.4,
         "result_time": "2026-09-01T21:00:00+00:00", "move_pct": 0.24, "move_pips": 50, "alignment": "contra", "status": "settled"},
        {"pair": "GBP/JPY", "session": "asean", "session_date": "2026-09-02", "tone": "Neutral", "strength": 0,
         "entry_price": 210.4, "entry_time": None, "result_price": None,
         "result_time": None, "move_pct": None, "move_pips": None, "alignment": "na", "status": "settled"},
    ],
}


def test_session_bias_history_parses_summary_and_rows():
    sess = FakeSession(body=SESSIONS_BODY)
    fx = Client(VALID_KEY, session=sess)
    h = fx.session_bias_history("gbpjpy", start="2026-09-01")
    assert isinstance(h, SessionBiasHistory)
    s = h.summary
    assert (s.settled, s.aligned, s.contra, s.directional, s.aligned_pct) == (3, 1, 1, 2, 50.0)
    assert s.aligned + s.contra == s.directional
    a, c, n = h.data
    assert a.is_aligned and a.is_directional and a.entry_price == 209.10 and a.move_pips == 80
    assert not c.is_aligned and c.is_directional
    assert not n.is_directional and n.result_price is None and n.entry_time is None
    assert h.coverage_from == "2026-08-06"


def test_session_bias_history_missing_summary_is_none_not_zeros():
    body = dict(SESSIONS_BODY, summary=None, summary_unavailable="A count could not be computed")
    h = Client(VALID_KEY, session=FakeSession(body=body)).session_bias_history()
    assert h.summary is None


def test_free_key_on_history_raises_plan_error_with_server_message():
    body = {"error": "upgrade-required", "message": "Sentiment history is included with FXNewsBias Pro."}
    sess = FakeSession(status=402, body=body)
    with pytest.raises(PlanError) as e:
        Client(VALID_KEY, session=sess).sentiment_history()
    assert e.value.status == 402 and "included with FXNewsBias Pro" in e.value.message
    assert sess.calls == 1                             # a plan answer is never retried


def test_bad_request_from_server_is_not_retried_and_keeps_body():
    body = {"error": "bad-pair", "message": "pair must be two of USD, EUR ..."}
    sess = FakeSession(status=400, body=body)
    with pytest.raises(ServerError) as e:
        Client(VALID_KEY, session=sess).session_bias_history("XXXYYY")
    assert e.value.status == 400 and e.value.body["error"] == "bad-pair"
    assert sess.calls == 1


@pytest.mark.parametrize("raw,micro", [
    ("2026-09-15T00:13:19.49+00:00", 490000),
    ("2026-09-22T00:00:46.129317+00:00", 129317),
    ("2026-09-23T10:00:40.826Z", 826000),
    ("2026-09-15T00:13:19.1234567+00:00", 123456),
    ("2026-09-15T00:13:19+00:00", 0),
])
def test_timestamps_parse_with_any_fraction_length(raw, micro):
    from fxnewsbias.models import _parse_ts

    ts = _parse_ts(raw)
    assert ts is not None and ts.tzinfo is not None and ts.microsecond == micro


# ------------------------------------------------------------------ markets
#
# Fixtures mirror the server's response shapes for the markets endpoints
# (schema fxnb.markets.*.v1). Gold is the first market; the list grows, so the
# client must never assume its length or that XAU is the only symbol.

from fxnewsbias import (  # noqa: E402
    Market,
    MarketHistory,
    Markets,
    MarketSessionBias,
    MarketSessionBiasHistory,
    MarketSettledSession,
    SettledSession,
)

ATTRIBUTION = {"required": True, "text": "Data by FXNewsBias", "url": "https://fxnewsbias.com"}

MARKETS_BODY = {
    "schema": "fxnb.markets.v1",
    "generated_at": "2026-10-05T06:05:12.481Z",
    "next_update_expected": "2026-10-05T09:00:00.000Z",
    "attribution": ATTRIBUTION,
    "data": [
        {
            "symbol": "XAU", "name": "Gold", "score": 66, "bias": "Bullish",
            "drivers": ["Fed cut bets lift bullion", "Safe-haven demand on trade tension"],
            "updated_at": "2026-10-05T06:02:00+00:00",
            "pair": {"name": "XAU/USD", "quote": "USD", "quote_score": 52, "gap": 14, "bias": "Bullish"},
        },
    ],
}

MARKET_SESSION_BODY = {
    "schema": "fxnb.markets.session_bias.v1",
    "generated_at": "2026-10-05T06:30:00.000Z",
    "symbol": "XAU",
    "attribution": ATTRIBUTION,
    "data": {"pair": "XAU/USD", "tone": "Bullish", "strength": 2, "session": "london",
             "session_date": "2026-10-05", "entry_time": "2026-10-05T06:20:00+00:00"},
}

MARKET_SESSIONS_HISTORY_BODY = {
    "schema": "fxnb.markets.session_bias.history.v1",
    "generated_at": "2026-10-09T10:00:00.000Z",
    "query": {"from": "2026-10-05", "to": "2026-10-09", "symbol": "XAU", "status": "settled"},
    "coverage_from": "2026-10-05",
    "pip_convention": "XAU/USD: 1 pip = $0.10 per ounce, so move_pips = move_usd * 10",
    "paging": {"offset": 0, "limit": 500, "returned": 3, "total_matching": 3, "has_more": False, "next_offset": None},
    "summary": {"settled": 3, "aligned": 1, "contra": 1, "directional": 2, "aligned_pct": 50.0},
    "attribution": ATTRIBUTION,
    "data": [
        {"pair": "XAU/USD", "session": "london", "session_date": "2026-10-05", "tone": "Bullish", "strength": 2,
         "entry_price": "2650.40", "entry_time": "2026-10-05T06:20:00+00:00", "result_price": 2662.4,
         "result_time": "2026-10-05T12:13:00+00:00", "move_pct": 0.453, "move_usd": 12, "move_pips": 120,
         "alignment": "aligned", "status": "settled"},
        {"pair": "XAU/USD", "session": "newyork", "session_date": "2026-10-05", "tone": "Bearish", "strength": 1,
         "entry_price": 2662.4, "entry_time": "2026-10-05T12:13:00+00:00", "result_price": 2670.15,
         "result_time": "2026-10-05T21:13:00+00:00", "move_pct": 0.291, "move_usd": 7.75, "move_pips": 77.5,
         "alignment": "contra", "status": "settled"},
        {"pair": "XAU/USD", "session": "asean", "session_date": "2026-10-06", "tone": "Neutral", "strength": 0,
         "entry_price": 2670.15, "entry_time": "2026-10-05T23:13:00+00:00", "result_price": None,
         "result_time": None, "move_pct": None, "move_usd": None, "move_pips": None,
         "alignment": "na", "status": "settled"},
    ],
}


class RoutingSession:
    """Answers each path with its own body, and records every URL asked for."""

    def __init__(self, routes, status=200):
        self.routes = routes
        self.status = status
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        body = self.routes.get(urlparse(url).path, {"error": "not-found"})
        return FakeResponse(self.status, body, {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "990"})

    def close(self):
        pass


def _markets_client(**routes):
    sess = RoutingSession({
        "/api/v1/markets": MARKETS_BODY,
        "/api/v1/markets/session-bias": MARKET_SESSION_BODY,
        "/api/v1/markets/session-bias/history": MARKET_SESSIONS_HISTORY_BODY,
        **routes,
    })
    return Client(VALID_KEY, session=sess), sess


def test_markets_parses_gold():
    fx, sess = _markets_client()
    m = fx.markets()
    assert urlparse(sess.urls[0]).path == "/api/v1/markets" and urlparse(sess.urls[0]).query == ""
    assert isinstance(m, Markets) and len(m) == 1
    g = m["XAU"]
    assert isinstance(g, Market)
    assert (g.symbol, g.name, g.score, g.bias) == ("XAU", "Gold", 66, "Bullish")
    assert g.is_bullish and not g.is_bearish
    assert g.drivers == ["Fed cut bets lift bullion", "Safe-haven demand on trade tension"]
    assert g.updated_at == datetime(2026, 10, 5, 6, 2, tzinfo=timezone.utc)
    p = g.pair
    assert (p.name, p.quote, p.quote_score, p.gap, p.bias) == ("XAU/USD", "USD", 52, 14, "Bullish")
    assert p.gap == g.score - p.quote_score          # same base minus quote rule as spread()
    assert m.generated_at.tzinfo is not None and m.next_update_expected.hour == 9
    assert m.raw["attribution"]["url"] == "https://fxnewsbias.com"
    assert fx.rate_limit == 1000


def test_markets_lookup_by_symbol_or_pair_any_case():
    fx, _ = _markets_client()
    m = fx.markets()
    assert m["xau"] is m["XAU/USD"] is m["xauusd"] is m.get("XAU")
    assert "XAU" in m and "BTC" not in m
    assert m.symbols() == ["XAU"] and m.scores() == {"XAU": 66}
    assert m.get("BTC") is None
    with pytest.raises(KeyError) as e:
        m["BTC"]
    assert "XAU" in str(e.value)


def test_markets_list_can_grow_without_a_release():
    """A market this version has never heard of still parses, fields and all."""
    body = json.loads(json.dumps(MARKETS_BODY))
    body["data"].append({
        "symbol": "ZZZ", "name": "Future market", "score": 41, "bias": "Neutral", "drivers": [],
        "updated_at": "2026-10-05T06:02:00Z", "new_field": "kept",
        "pair": {"name": "ZZZ/USD", "quote": "USD", "quote_score": 52, "gap": -11, "bias": "Bearish"},
    })
    fx, _ = _markets_client(**{"/api/v1/markets": body})
    m = fx.markets()
    assert m.symbols() == ["XAU", "ZZZ"]
    assert m["ZZZ"].pair.bias == "Bearish" and m["ZZZ"].raw["new_field"] == "kept"
    assert m["XAU"].score == 66                     # adding a market moves nothing else


def test_markets_pair_fields_are_none_without_a_quote_score():
    body = json.loads(json.dumps(MARKETS_BODY))
    body["data"][0]["pair"].update(quote_score=None, gap=None, bias=None)
    body["data"][0]["drivers"] = None
    fx, _ = _markets_client(**{"/api/v1/markets": body})
    g = fx.markets()["XAU"]
    assert (g.pair.quote_score, g.pair.gap, g.pair.bias) == (None, None, None)
    assert g.drivers == [] and g.score == 66


def test_markets_empty_list_is_not_an_error():
    body = dict(MARKETS_BODY, data=[])
    fx, _ = _markets_client(**{"/api/v1/markets": body})
    m = fx.markets()
    assert len(m) == 0 and m.get("XAU") is None


def test_markets_seconds_until_next_update_never_negative():
    body = dict(MARKETS_BODY, next_update_expected=(
        datetime.now(timezone.utc) - timedelta(hours=5)).isoformat().replace("+00:00", "Z"))
    fx, _ = _markets_client(**{"/api/v1/markets": body})
    assert fx.markets().seconds_until_next_update() == 0.0
    body = {k: v for k, v in MARKETS_BODY.items() if k != "next_update_expected"}
    fx, _ = _markets_client(**{"/api/v1/markets": body})
    assert fx.markets().seconds_until_next_update() is None


PRO_ONLY = {"error": "pro-only", "message": "Gold and other markets are a Pro feature.",
            "upgrade": "https://fxnewsbias.com/pricing"}


@pytest.mark.parametrize("call", [
    lambda fx: fx.markets(),
    lambda fx: fx.markets_history("XAU"),
    lambda fx: fx.market_session_bias("XAU"),
    lambda fx: fx.market_session_bias_history("XAU"),
])
def test_free_key_on_markets_raises_plan_error(call):
    sess = FakeSession(status=403, body=PRO_ONLY)
    with pytest.raises(PlanError) as e:
        call(Client(VALID_KEY, session=sess))
    assert e.value.status == 403 and "Pro feature" in e.value.message
    assert e.value.body["upgrade"] == "https://fxnewsbias.com/pricing"
    assert sess.calls == 1                             # a plan answer is never retried


MARKET_READINGS = [
    {"symbol": "XAU", "score": 60 + i, "bias": "Bullish", "pair_gap": 8 + i,
     "pair_bias": "Bullish" if 8 + i > 10 else "Neutral",
     "scored_at": f"2026-10-0{5 + i // 8}T{(i % 8) * 3:02d}:02:00.{i}+00:00"}
    for i in range(12)
]


class MarketPagingSession(PagingSession):
    """PagingSession with the markets history envelope."""

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        q = parse_qs(urlparse(url).query)
        limit = int(q.get("limit", ["500"])[0])
        offset = int(q.get("offset", ["0"])[0])
        body = _hist_page(self.rows[offset:offset + limit], offset, limit, self.total)
        body.update(schema="fxnb.markets.history.v1", coverage_from="2026-10-05",
                    query={"from": "2026-10-05", "to": "2026-10-06", "symbol": q.get("symbol", [""])[0]},
                    attribution=ATTRIBUTION)
        return FakeResponse(200, body, {})


def test_markets_history_builds_the_query():
    sess = MarketPagingSession(MARKET_READINGS)
    h = Client(VALID_KEY, session=sess).markets_history(
        " xau ", start="2026-10-05", end=datetime(2026, 10, 6, 15, tzinfo=timezone.utc), limit=100)
    assert urlparse(sess.urls[0]).path == "/api/v1/markets/history"
    q = parse_qs(urlparse(sess.urls[0]).query)
    assert q == {"symbol": ["XAU"], "from": ["2026-10-05"], "to": ["2026-10-06"], "limit": ["100"]}
    assert isinstance(h, MarketHistory) and h.coverage_from == "2026-10-05"


def test_markets_history_parses_rows():
    h = Client(VALID_KEY, session=MarketPagingSession(MARKET_READINGS)).markets_history("XAU")
    assert len(h) == 12 and h.paging.total_matching == 12 and h.paging.next_offset is None
    first, last = h.data[0], h.data[-1]
    assert (first.symbol, first.score, first.bias, first.pair_gap, first.pair_bias) == ("XAU", 60, "Bullish", 8, "Neutral")
    assert first.scored_at == datetime(2026, 10, 5, 0, 2, tzinfo=timezone.utc)
    assert last.pair_gap == 19 and last.pair_bias == "Bullish" and last.is_bullish
    assert last.scored_at.day == 6


def test_iter_markets_history_follows_every_page_once():
    sess = MarketPagingSession(MARKET_READINGS)
    got = list(Client(VALID_KEY, session=sess).iter_markets_history("XAU", page_size=5))
    assert [r.raw for r in got] == MARKET_READINGS     # no row lost or repeated
    assert len(sess.urls) == 3                          # 5 + 5 + 2
    qs = [parse_qs(urlparse(u).query) for u in sess.urls]
    assert [q.get("offset", ["0"])[0] for q in qs] == ["0", "5", "10"]
    assert all(q["symbol"] == ["XAU"] for q in qs)


def test_iter_markets_history_stops_on_an_empty_page():
    sess = MarketPagingSession([], total=50)
    assert list(Client(VALID_KEY, session=sess).iter_markets_history("XAU")) == []
    assert len(sess.urls) == 1


@pytest.mark.parametrize("name", [
    "markets_history", "market_session_bias", "market_session_bias_history",
])
@pytest.mark.parametrize("symbol", ["", "   ", None])
def test_missing_symbol_fails_before_any_request(name, symbol):
    sess = MarketPagingSession(MARKET_READINGS)
    with pytest.raises(ValueError) as e:
        getattr(Client(VALID_KEY, session=sess), name)(symbol)
    assert "fx.markets()" in str(e.value)
    assert sess.urls == []


@pytest.mark.parametrize("kw", [
    {"start": "2026-13-01"}, {"end": "yesterday"}, {"limit": 0}, {"limit": 5001}, {"offset": -1},
])
@pytest.mark.parametrize("name", ["markets_history", "market_session_bias_history"])
def test_bad_market_history_arguments_fail_before_any_request(name, kw):
    sess = MarketPagingSession(MARKET_READINGS)
    with pytest.raises(ValueError):
        getattr(Client(VALID_KEY, session=sess), name)("XAU", **kw)
    assert sess.urls == []


def test_unknown_symbol_is_left_to_the_server_and_not_retried():
    """No local symbol list: a new market must work on an old client."""
    body = {"error": "bad-symbol", "message": "symbol is required and must be one of XAU."}
    sess = FakeSession(status=400, body=body)
    with pytest.raises(ServerError) as e:
        Client(VALID_KEY, session=sess).markets_history("btc")
    assert e.value.status == 400 and e.value.body["error"] == "bad-symbol"
    assert sess.calls == 1


def test_a_400_is_a_request_error_not_an_outage():
    """A wrong request raises RequestError, still catchable as ServerError so
    1.1.0 code behaves as before, and is never retried. It is not a ValueError,
    so an `except ValueError` ahead of `except ServerError` routes as in 1.1.0."""
    from fxnewsbias import RequestError
    body = {"error": "bad-symbol", "message": "symbol is required and must be one of XAU."}
    sess = FakeSession(status=400, body=body)
    with pytest.raises(RequestError) as e:
        Client(VALID_KEY, session=sess).market_session_bias("xag")
    assert isinstance(e.value, ServerError) and not isinstance(e.value, ValueError)
    assert e.value.message == "symbol is required and must be one of XAU."
    assert e.value.status == 400 and sess.calls == 1


def test_a_5xx_is_not_a_request_error():
    from fxnewsbias import RequestError
    sess = FakeSession(status=503, body={"error": "unavailable"})
    with pytest.raises(ServerError) as e:
        Client(VALID_KEY, session=sess, max_retries=0).markets()
    assert not isinstance(e.value, RequestError)


@pytest.mark.parametrize("given", ["XAU/USD", "xau/usd", "XAUUSD", "xau-usd", " XAU_USD ", "xau"])
def test_the_pair_name_works_as_a_symbol(given):
    fx, sess = _markets_client()
    fx.market_session_bias(given)
    assert parse_qs(urlparse(sess.urls[0]).query) == {"symbol": ["XAU"]}


def test_a_non_pair_symbol_is_sent_as_given():
    fx, sess = _markets_client()
    fx.market_session_bias("btc")
    assert parse_qs(urlparse(sess.urls[0]).query) == {"symbol": ["BTC"]}


def test_market_session_bias_parses_the_newest_call():
    fx, sess = _markets_client()
    sb = fx.market_session_bias("xau")
    assert urlparse(sess.urls[0]).path == "/api/v1/markets/session-bias"
    assert parse_qs(urlparse(sess.urls[0]).query) == {"symbol": ["XAU"]}
    assert isinstance(sb, MarketSessionBias) and sb.has_call
    assert (sb.symbol, sb.pair, sb.tone, sb.strength, sb.session, sb.session_date) == (
        "XAU", "XAU/USD", "Bullish", 2, "london", "2026-10-05")
    assert sb.entry_time == datetime(2026, 10, 5, 6, 20, tzinfo=timezone.utc)


def test_market_session_bias_with_no_call_yet():
    body = dict(MARKET_SESSION_BODY, data=None)
    fx, _ = _markets_client(**{"/api/v1/markets/session-bias": body})
    sb = fx.market_session_bias("XAU")
    assert not sb.has_call
    assert (sb.symbol, sb.pair, sb.tone, sb.strength, sb.entry_time) == ("XAU", None, None, None, None)


def test_market_session_bias_history_parses_summary_and_rows():
    fx, sess = _markets_client()
    h = fx.market_session_bias_history("XAU", start="2026-10-05", end="2026-10-09")
    assert urlparse(sess.urls[0]).path == "/api/v1/markets/session-bias/history"
    assert parse_qs(urlparse(sess.urls[0]).query) == {"symbol": ["XAU"], "from": ["2026-10-05"], "to": ["2026-10-09"]}
    assert isinstance(h, MarketSessionBiasHistory)
    s = h.summary
    assert (s.settled, s.aligned, s.contra, s.directional, s.aligned_pct) == (3, 1, 1, 2, 50.0)
    assert s.aligned + s.contra == s.directional
    assert "$0.10" in h.pip_convention and h.coverage_from == "2026-10-05"
    a, c, n = h.data
    assert isinstance(a, MarketSettledSession) and isinstance(a, SettledSession)
    assert a.is_aligned and a.is_directional and a.entry_price == 2650.40
    assert a.move_usd == 12.0 and a.move_pips == 120.0
    assert c.move_pips == c.move_usd * 10                # gold: 1 pip = $0.10 per ounce
    assert not c.is_aligned and c.is_directional
    assert not n.is_directional and n.move_usd is None and n.result_price is None and n.session == "asean"


def test_market_session_bias_history_missing_summary_is_none_not_zeros():
    body = dict(MARKET_SESSIONS_HISTORY_BODY, summary=None,
                summary_unavailable="A count could not be computed")
    fx, _ = _markets_client(**{"/api/v1/markets/session-bias/history": body})
    h = fx.market_session_bias_history("XAU")
    assert h.summary is None and h.raw["summary_unavailable"].startswith("A count")


def test_iter_market_session_bias_history_follows_pages():
    rows = MARKET_SESSIONS_HISTORY_BODY["data"]

    class Pages(PagingSession):
        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            q = parse_qs(urlparse(url).query)
            limit, offset = int(q.get("limit", ["500"])[0]), int(q.get("offset", ["0"])[0])
            body = _hist_page(self.rows[offset:offset + limit], offset, limit, self.total)
            body.update(schema="fxnb.markets.session_bias.history.v1",
                        summary=MARKET_SESSIONS_HISTORY_BODY["summary"])
            return FakeResponse(200, body, {})

    sess = Pages(rows)
    got = list(Client(VALID_KEY, session=sess).iter_market_session_bias_history("XAU", page_size=2))
    assert [r.raw for r in got] == rows and all(isinstance(r, MarketSettledSession) for r in got)
    assert len(sess.urls) == 2
    assert all(parse_qs(urlparse(u).query)["symbol"] == ["XAU"] for u in sess.urls)


def test_markets_calls_leave_the_currency_endpoints_alone():
    """Gold arrives on its own paths: sentiment() still returns exactly the 8."""
    fx, sess = _markets_client(**{"/api/v1/sentiment": SENTIMENT_BODY})
    fx.markets()
    s = fx.sentiment()
    assert len(s) == 8 and "XAU" not in s.scores()
    assert [urlparse(u).path for u in sess.urls] == ["/api/v1/markets", "/api/v1/sentiment"]


def test_user_agent_carries_the_version():
    import fxnewsbias

    c = make()
    c.sentiment()
    assert c._session.seen_headers["User-Agent"] == f"fxnewsbias-python/{fxnewsbias.__version__}"
    assert fxnewsbias.__version__ == "1.2.0"


def test_package_and_pyproject_versions_match():
    """The User-Agent and the PyPI upload must name the same release."""
    import pathlib
    import re

    import fxnewsbias

    toml = (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.M)
    assert m and m.group(1) == fxnewsbias.__version__
