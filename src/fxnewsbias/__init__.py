"""FXNewsBias: AI-scored forex news sentiment for the 8 major currencies.

    pip install fxnewsbias

    from fxnewsbias import Client

    fx = Client("fxnb_live_...")
    for c in fx.sentiment():
        print(c.currency, c.score, c.bias)

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
)
from .errors import (
    FXNewsBiasError,
    AuthError,
    RateLimitError,
    PlanError,
    ServerError,
)

__version__ = "1.1.0"
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
    "FXNewsBiasError",
    "AuthError",
    "RateLimitError",
    "PlanError",
    "ServerError",
]
