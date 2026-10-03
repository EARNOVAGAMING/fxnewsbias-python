"""FXNewsBias: AI-scored forex news sentiment for the 8 major currencies and gold.

    pip install fxnewsbias

    from fxnewsbias import Client

    fx = Client("fxnb_live_...")
    for c in fx.sentiment():
        print(c.currency, c.score, c.bias)

    gold = fx.markets()["XAU"]          # Pro plans: gold and other markets
    print(gold.score, gold.bias, gold.pair.gap)

Get a key at https://fxnewsbias.com/developers
"""

from .client import Client
from .models import (
    Currency,
    Sentiment,
    SessionBias,
    PairBias,
    Paging,
    SentimentReading,
    SentimentHistory,
    SettledSession,
    ScorecardSummary,
    SessionBiasHistory,
    Market,
    MarketPair,
    Markets,
    MarketReading,
    MarketHistory,
    MarketSessionBias,
    MarketSettledSession,
    MarketSessionBiasHistory,
)
from .errors import (
    FXNewsBiasError,
    AuthError,
    RateLimitError,
    PlanError,
    RequestError,
    ServerError,
)

__version__ = "1.2.0"
__all__ = [
    "Client",
    "Currency",
    "Sentiment",
    "SessionBias",
    "PairBias",
    "Paging",
    "SentimentReading",
    "SentimentHistory",
    "SettledSession",
    "ScorecardSummary",
    "SessionBiasHistory",
    "Market",
    "MarketPair",
    "Markets",
    "MarketReading",
    "MarketHistory",
    "MarketSessionBias",
    "MarketSettledSession",
    "MarketSessionBiasHistory",
    "FXNewsBiasError",
    "AuthError",
    "RateLimitError",
    "PlanError",
    "RequestError",
    "ServerError",
]
