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

def test_401_explains_the_likely_cause():
    with pytest.raises(AuthError) as e:
        make(status=401, body={"error": "unauthorized"}).sentiment()
    assert "subscription" in str(e.value)


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
