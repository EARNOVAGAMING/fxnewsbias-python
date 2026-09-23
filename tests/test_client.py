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
    s = make().sentiment()
    with pytest.raises(KeyError) as e:
        s["XAU"]
    assert "AUD" in str(e.value)


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
        "session": "asia",
        "session_date": "2026-08-23",
        "data": [{"pair": "AUD/USD", "tone": "Bullish", "strength": 4}],
    }
    sb = Client(VALID_KEY, session=FakeSession(body=body)).session_bias()
    assert sb.session == "asia"
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
        {"pair": "GBP/JPY", "session": "asia", "session_date": "2026-09-02", "tone": "Neutral", "strength": 0,
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
