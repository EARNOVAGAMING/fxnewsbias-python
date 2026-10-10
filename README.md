# fxnewsbias

Python client for the [FXNewsBias](https://fxnewsbias.com) API: AI-scored news sentiment for the 8 major currencies, gold and the other markets (silver, oil, crypto and the US indices), as JSON.

One number per currency, 0 to 100, refreshed every three hours. Currency labels are 0-40 Bearish, 41-59 Neutral and 60-100 Bullish. Scores describe selected headline tone, not the probability of a price move. A currency without a supported catalyst receives 50 Neutral with an explicit explanation. See the [scoring methodology](https://fxnewsbias.com/how).

Free tier available: any account can create a key at [fxnewsbias.com/developers](https://fxnewsbias.com/developers), no card required. Gold and other markets are on Pro plans, with the same key (see Gold and other markets below).

[![PyPI](https://img.shields.io/pypi/v/fxnewsbias.svg)](https://pypi.org/project/fxnewsbias/)
[![Python](https://img.shields.io/pypi/pyversions/fxnewsbias.svg)](https://pypi.org/project/fxnewsbias/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/EARNOVAGAMING/fxnewsbias-python/blob/main/LICENSE)

```bash
pip install fxnewsbias
```

```python
from fxnewsbias import Client

fx = Client("fxnb_live_...")

for c in fx.sentiment():
    print(c.currency, c.score, c.bias)
```

```
AUD 68 Bullish
USD 55 Neutral
EUR 52 Neutral
GBP 50 Neutral
NZD 50 Neutral
JPY 48 Neutral
CHF 45 Neutral
CAD 35 Bearish
```

## The one method that matters

Most strategies don't want eight numbers. They want a yes or no on the pair they're about to trade.

```python
s = fx.sentiment()

s.spread("AUD/USD")     # 13   (AUD 68 - USD 55, positive favours the base)
s.favours("AUD/USD")    # 'long'
s.favours("GBP/NZD")    # None, the news is flat, stand aside
```

Used as a gate:

```python
if fx.sentiment().favours("AUD/USD") == "long":
    place_trade()
```

The default threshold is 10 points. Tune it against your own results:

```python
s.favours("AUD/USD", threshold=25)   # only act on strong disagreement
```

## Don't poll on a timer

The scores only move every few hours, and each response tells you when the next one lands. `follow()` sleeps until then instead of burning your daily allowance on identical answers.

```python
for s in fx.follow():
    print(s.generated_at, s["AUD"].score)
    # blocks until the data actually changes
```

Doing it by hand:

```python
import time

while True:
    s = fx.sentiment()
    handle(s)
    time.sleep(s.seconds_until_next_update() or 3600)
```

A bot polling every 15 minutes uses 96 calls a day. `follow()` uses about 8.

## Getting a key

Create a free account, sign in at [fxnewsbias.com/developers](https://fxnewsbias.com/developers), and the key panel creates one instantly. No card, no application form.

Two tiers, same key format, same client code:

| | Free | Pro |
|---|---|---|
| Price | $0 | from $20/month |
| Requests per UTC day | 25 | 1,000 |
| Data freshness | previous 3-hour cycle | current cycle, real-time |
| `sentiment()` | yes, delayed | yes |
| `session_bias()` | no | yes |
| `sentiment_history()` | no | yes, every cycle since 2026-05-19 |
| `session_bias_history()` | no | yes, settled scorecard since 2026-08-06 |
| `markets()` (gold, silver, oil, crypto, US indices) | no | yes |
| `markets_history()` | no | yes, every cycle since the market was added (`coverage_from`) |
| `market_session_bias()` | no | yes |
| `market_session_bias_history()` | no | yes, the market's settled calls since they began (`coverage_from`) |
| Use | non-commercial, with attribution | commercial, in your own product |

Free responses carry `delayed: true` and `delay_hours: 3` (reachable via `.raw`), so the freshness is never ambiguous. `follow()` fits the free tier well: it spends about 8 of the 25 daily calls. Upgrading later changes nothing in your code; the same key switches to real-time automatically.

Pass it directly, or set `FXNEWSBIAS_API_KEY` and let the client find it:

```python
fx = Client()                       # reads FXNEWSBIAS_API_KEY
fx = Client("fxnb_live_...")        # or pass it
```

The key is never printed, including in `repr()` and tracebacks.

## Errors

Every exception carries the HTTP status and the parsed body, because the useful question when something breaks is what the server actually said.

```python
from fxnewsbias import AuthError, RateLimitError, PlanError, RequestError, ServerError

try:
    s = fx.sentiment()
except RequestError as e:
    print(f"the request was wrong: {e.message}")   # e.g. an unknown market symbol
except RateLimitError as e:
    print(f"allowance spent, resets in {e.retry_after}s")
except AuthError:
    print("key missing, invalid, replaced or revoked")
except PlanError:
    print("that endpoint is not on this plan")
except ServerError as e:
    print(f"upstream problem: {e.status}")
```

A 400, 401, 402, 403 or 429 is an answer, not a failure, so none of them are retried. `RequestError` (400) is also a `ValueError`, like the argument checks the client makes before sending. It subclasses `ServerError` so code written for 1.1.0 keeps working, which is why it is caught first above.

When a Pro subscription ends the key keeps working: it moves to the free tier in place, so `sentiment()` carries on with delayed data and Pro-only calls raise `PlanError`. Nothing needs changing in your code either way. A 5xx or a dropped connection is retried twice with backoff.

Rate limit state from the last call is on the client:

```python
fx.sentiment()
fx.rate_remaining    # 994
fx.rate_limit        # 1000
```

## Endpoints

### `fx.sentiment()`

Current reading for USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD.

```python
s = fx.sentiment()

s["AUD"].score        # 68
s["AUD"].bias         # 'Bullish'
s["AUD"].is_bullish   # True
s.scores()            # {'AUD': 68, 'USD': 55, ...}
len(s)                # 8
s.generated_at        # datetime, tz-aware
s.raw                 # the untouched response dict
```

Lookup is case-insensitive. `.raw` is kept on every object, so a field added to the API later is reachable without waiting for a release of this package.

### `fx.session_bias()`

Per-pair directional read for the most recent session. Pro plans only; raises `PlanError` otherwise.

```python
sb = fx.session_bias()
sb.session            # 'asean', 'london' or 'newyork'
sb.session_date       # '2026-08-23'

for p in sb:
    print(p.pair, p.tone, p.strength)
```

### `fx.sentiment_history()`

Every past 3-hour reading, oldest first. Pro plans only; raises `PlanError` on a free key.

```python
h = fx.sentiment_history("EUR", start="2026-09-01", end="2026-09-07")

for r in h:
    print(r.scored_at, r.score, r.bias)

h.by_currency()       # {'EUR': [...]} when no currency is given, all 8
h.paging.has_more     # True when the range is longer than one page
```

`start` and `end` are inclusive UTC dates (`"YYYY-MM-DD"` or a `date`). Leave them out for the last 30 days, and leave out the currency for all 8. A page holds up to 5,000 rows.

Labels have been set from the score since 23 Sep 2026. Earlier rows keep the label the scorer gave at the time, which sometimes sat a few points outside the bands above. If you need one consistent rule across the whole history, derive the label from `score`.

For a long range, `iter_sentiment_history()` follows the pages for you. Each page is one request, so a full year for all 8 currencies costs about 5:

```python
for r in fx.iter_sentiment_history(start="2026-05-19"):
    store(r.currency, r.scored_at, r.score)
```

### `fx.session_bias_history()`

The settled session scorecard: what was called for each pair, what price did next, and whether it agreed. Misses included. Pro plans only.

```python
h = fx.session_bias_history("GBP/JPY", start="2026-09-01")

h.summary.aligned_pct   # hit rate over the whole range, not just this page
h.summary.directional   # aligned + contra, the calls that count

for s in h:
    print(s.session_date, s.session, s.tone, s.alignment, s.move_pips)
```

`alignment` is `'aligned'` (price went the called way), `'contra'` (it went against), `'quiet'` (a call, but the move was too small to count) or `'na'` (a Neutral call, nothing to score). Only aligned and contra count toward the hit rate. `iter_session_bias_history()` pages the same way as sentiment.

## Gold and other markets (Pro)

Markets are the instruments beyond the 8 currencies: gold (`XAU`) first, and since 10 October 2026 silver (`XAG`), WTI crude (`WTI`), Brent crude (`XBR`), Bitcoin (`BTC`), Ethereum (`ETH`), Solana (`SOL`), XRP (`XRP`), BNB (`BNB`) and the US 500 (`SPX`), US Tech 100 (`NDX`) and Dow 30 (`DJI`), the last three read through their ETF proxies (SPY, QQQ and DIA), so an index read follows the ETF and is not an index level. The website shows each market on its own page, gold on the [XAU/USD page](https://fxnewsbias.com/markets/xau-usd), and lists every market on the [Markets page](https://fxnewsbias.com/markets). Markets are on Pro plans only: same key, same daily allowance (each call is one request from the same 1,000 a day), same errors. A free key raises `PlanError`, and an unknown symbol raises `RequestError`. The pair name works as a symbol too: `"XAU/USD"` means `"XAU"` and `"BTC/USD"` means `"BTC"`; the US indices are addressed by symbol (`"SPX"`, `"NDX"`, `"DJI"`).

They have their own methods and endpoints, so nothing above changes: `sentiment()` still returns exactly the 8 currencies, and `s["XAU"]` or `s.spread("XAU/USD")` raise with a pointer to `fx.markets()`.

The list of markets grows over time. Look a market up by symbol rather than relying on how many there are or their order, and use `m.symbols()` to see what is available.

### `fx.markets()`

The latest reading for every market, refreshed every three hours like the currencies.

```python
m = fx.markets()
gold = m.get("XAU")        # or m.get("XAU/USD"); None before a market's first reading
                           # m["XAU"] works too, and raises KeyError if it is absent

gold.score                 # 66, the same 0 to 100 scale and labels as the currencies
gold.bias                  # 'Bullish'
gold.drivers               # the short reasons behind the score
gold.updated_at            # datetime, tz-aware

gold.pair.name             # 'XAU/USD'
gold.pair.quote_score      # 52, the latest USD score when this reading was made
gold.pair.gap              # 14, gold minus USD, the same convention as spread()
gold.pair.bias             # 'Bullish' above +10, 'Bearish' below -10, else 'Neutral'

m.symbols()                # every market with a reading, for example ['XAU', 'XAG', 'BTC', ...]; look up by symbol, never by position
m.seconds_until_next_update()
```

`pair.quote_score`, `pair.gap` and `pair.bias` are `None` for a cycle that had no USD score to compare against.

### `fx.markets_history()`

One market's past readings, every 3-hour cycle, oldest first. `symbol` is required. Dates, `limit` and paging work exactly as in `sentiment_history()`.

```python
h = fx.markets_history("XAU")      # the last 30 days

for r in h:
    print(r.scored_at, r.score, r.bias, r.pair_gap, r.pair_bias)

h.coverage_from                    # the first date gold was scored

for r in fx.iter_markets_history("XAU", start=h.coverage_from):
    store(r.symbol, r.scored_at, r.score)
```

### `fx.market_session_bias()`

The newest session call for one market's pair, for example XAU/USD. Calls are made on weekdays for the same three sessions as the currency pairs.

```python
sb = fx.market_session_bias("XAU")

if sb.has_call:                    # False before the first call
    print(sb.pair, sb.session, sb.session_date, sb.tone, sb.strength)
```

### `fx.market_session_bias_history()`

The settled calls for one market, misses included, with the same alignment rules as `session_bias_history()`. The summary covers that market alone and is never mixed into the currency pair scorecard.

```python
h = fx.market_session_bias_history("XAU")

h.summary.aligned_pct      # gold only, over the whole range (summary is None if it could not be counted)
h.pip_convention           # how move_pips is measured

for s in h:
    print(s.session_date, s.session, s.tone, s.alignment, s.move_usd, s.move_pips)
```

Gold moves are quoted in US dollars per ounce. `move_usd` is the move in dollars, and for gold 1 pip is $0.10 per ounce, so `move_pips` is `move_usd * 10`. `iter_market_session_bias_history()` pages the same way as the others.

## Worked example: a news filter for a backtest

Record what the news backdrop was at entry, so you can check afterwards whether it mattered.

```python
from fxnewsbias import Client

fx = Client()
snapshot = fx.sentiment()

def should_enter(pair: str, signal: str) -> bool:
    """Take the trade only when the news does not argue against it."""
    view = snapshot.favours(pair, threshold=10)
    if view is None:
        return True              # news is flat, let the strategy decide
    return view == signal        # news agrees

for pair, signal in candidates:
    if should_enter(pair, signal):
        log(pair, signal, spread=snapshot.spread(pair))
```

## No required dependencies

Uses `requests` if it's already installed, otherwise the standard library. Nothing is pulled into your trading stack.

```bash
pip install fxnewsbias[requests]   # if you want connection pooling
```

Python 3.8+. Fully type-hinted, ships `py.typed`.

## Development

```bash
git clone https://github.com/EARNOVAGAMING/fxnewsbias-python
cd fxnewsbias-python
pip install -e ".[dev]"
pytest
```

Tests run against a fake transport, so they need no key and never touch the live API.

## Links

- [API documentation](https://fxnewsbias.com/developers)
- [Pricing](https://fxnewsbias.com/pricing)
- [Markets](https://fxnewsbias.com/markets): gold (XAU/USD) and the other markets on the website
- [Data quality report](https://fxnewsbias.com/data-quality): live coverage figures, updated automatically
- [Changelog](https://github.com/EARNOVAGAMING/fxnewsbias-python/blob/main/CHANGELOG.md)

## Attribution

Responses carry an `attribution` object. If you display the data publicly, credit FXNewsBias with a link. Redistributing the raw feed or sharing a key across separate users is not permitted; see the [terms](https://fxnewsbias.com/terms).

## Licence

MIT for this client library. The data it fetches is licensed separately under the terms above.
