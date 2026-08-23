"""Gate a trade on the news backdrop.

    export FXNEWSBIAS_API_KEY=fxnb_live_...
    python examples/news_filter.py EURUSD
"""

import sys

from fxnewsbias import Client, FXNewsBiasError

PAIR = sys.argv[1] if len(sys.argv) > 1 else "AUD/USD"
THRESHOLD = 10

try:
    s = Client().sentiment()
except FXNewsBiasError as e:
    sys.exit(f"{e}")

spread = s.spread(PAIR)
view = s.favours(PAIR, threshold=THRESHOLD)

base, quote = PAIR.upper().replace("/", "")[:3], PAIR.upper().replace("/", "")[3:]
print(f"{base} {s[base].score}  vs  {quote} {s[quote].score}   spread {spread:+d}")
print(f"news view on {PAIR}: {view or 'flat, stand aside'}")
print(f"scores generated {s.generated_at}, next update in "
      f"{int((s.seconds_until_next_update() or 0) / 60)} min")
