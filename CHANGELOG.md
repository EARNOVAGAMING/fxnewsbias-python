# Changelog

All notable changes to the `fxnewsbias` Python client. Versions follow
[semantic versioning](https://semver.org): a minor release adds methods, and
nothing an existing integration relies on changes shape or meaning.

## 1.2.1 (10 Oct 2026)

Documentation only, no code change. Every existing call keeps its shape.

- Eleven more markets are served by the existing methods: silver (XAG), WTI
  crude (WTI), Brent crude (XBR), Bitcoin (BTC), Ethereum (ETH), Solana
  (SOL), XRP (XRP), BNB (BNB) and the US 500 (SPX), US Tech 100 (NDX) and
  Dow 30 (DJI), the last three read through their ETF proxies (SPY, QQQ,
  DIA). `fx.markets()` lists them as each gets its first reading;
  `markets_history()`, `market_session_bias()` and
  `market_session_bias_history()` take the new symbols. The README documents
  the symbols and notes that the US indices are addressed by symbol.
- The README links the gold page at its new address,
  https://fxnewsbias.com/markets/xau-usd (the old /pairs/xau-usd address
  redirects there), and the Markets page, https://fxnewsbias.com/markets.

## 1.2.0 (3 Oct 2026)

Gold (XAU/USD) and the markets endpoints. Additive only: every existing
method, model and response is unchanged, and `sentiment()` still returns
exactly the 8 currencies.

### Added

- `fx.markets()`: the latest reading for every market, starting with gold.
  Each `Market` has `symbol`, `name`, `score` (the same 0 to 100 scale and
  labels as the currencies), `bias`, `drivers`, `updated_at`, and a `pair`
  block (`MarketPair`: `name`, `quote`, `quote_score`, `gap`, `bias`) that
  reads the market against its quote currency, for example XAU/USD. Index the
  response by symbol or pair, `m["XAU"]` or `m["XAU/USD"]`; `m.symbols()`
  lists what is available. The list grows as instruments are added, so do
  not rely on its length or order.
- `fx.markets_history(symbol, start=, end=, limit=, offset=)` and
  `fx.iter_markets_history(symbol, ...)`: one market's past readings, every
  3-hour cycle, with the same paging and date rules as `sentiment_history()`.
  Returns `MarketHistory` of `MarketReading` rows.
- `fx.market_session_bias(symbol)`: the newest session call for a market's
  pair, as `MarketSessionBias`. `has_call` is False before the first call.
- `fx.market_session_bias_history(symbol, ...)` and
  `fx.iter_market_session_bias_history(symbol, ...)`: settled session calls,
  misses included, with a summary for that market alone. Rows are
  `MarketSettledSession`, a `SettledSession` with an extra `move_usd`. For
  gold, 1 pip is $0.10 per ounce, so `move_pips` is `move_usd * 10`; the
  response's `pip_convention` says the same.
- Every new model keeps the untouched response in `.raw`, like the existing
  ones.
- `examples/news_filter.py` accepts `XAUUSD` (gold reads come from
  `fx.markets()`), and `examples/gold.py` shows the gold reading and the
  latest session call.

All markets methods are Pro only, on the same key and the same daily
allowance. A free key raises `PlanError` with the server's message.

- `RequestError` for an HTTP 400, such as an unknown market symbol, so a
  wrong request can be told apart from an outage. It subclasses
  `ServerError` and is never retried, so code written for 1.1.0 that caught
  a 400 as `ServerError` still catches it. Catch `RequestError` first to
  tell the two apart. Markets methods also accept the pair name as the
  symbol (`"XAU/USD"`, `"XAUUSD"` or `"xau-usd"` mean `"XAU"`).

### Changed

- `Sentiment["XAU"]` (KeyError) and `Sentiment.spread("XAU/USD")`
  (ValueError) still raise as before, and their messages now point to
  `fx.markets()`.
- Package description and keywords mention gold.
- A 400 now raises `RequestError` (a `ServerError` subclass, so existing
  handlers still catch it) and its message is the server's reason instead of
  `"HTTP 400 from <path>: <body>"`. The full reply is still in `.body`.
- An error reply whose body is valid JSON but not an object (for example
  `null`) now raises the usual error class instead of `AttributeError`.

## 1.1.0 (23 Sep 2026)

### Added

- `sentiment_history()` and `iter_sentiment_history()` for
  `/api/v1/sentiment/history`.
- `session_bias_history()` and `iter_session_bias_history()` for the settled
  session scorecard, with a hit-rate summary over the whole requested range.
- Dates, `limit` and `offset` are checked before a request is spent.

### Fixed

- History timestamps with 1 to 6 fractional digits now parse on Python 3.8
  to 3.10 instead of coming back as None.
- `AuthError` no longer blames an ended subscription: an ended Pro
  subscription moves the key to the free tier in place.

## 1.0.2 (17 Sep 2026)

### Changed

- A 401 now carries the server's reason (missing header, malformed key, or a
  key that is no longer active, for example after regenerating it).
- A 402 raises `PlanError` instead of `ServerError`.

## 1.0.1 (28 Aug 2026)

- README documents the free tier. No code changes.

## 1.0.0 (23 Aug 2026)

- First release: `sentiment()`, `session_bias()`, `follow()`, `spread()` and
  `favours()`, typed errors, and no required dependencies.
