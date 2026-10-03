"""Gate a trade on the news backdrop.

    export FXNEWSBIAS_API_KEY=fxnb_live_...
    python examples/news_filter.py EURUSD
    python examples/news_filter.py XAUUSD    # gold, Pro plans only
"""

import sys

from fxnewsbias import Client, FXNewsBiasError, PlanError

PAIR = sys.argv[1] if len(sys.argv) > 1 else "AUD/USD"
THRESHOLD = 10

compact = PAIR.upper().replace("/", "").replace("-", "")
base, quote = compact[:3], compact[3:]
try:
    fx = Client()
except FXNewsBiasError as e:
    sys.exit(f"{e}")

if base == "XAU":
    # Gold is not one of the 8 currencies, so it has its own Pro endpoint.
    # The XAU/USD gap uses the same +/-10 thresholds as every pair.
    try:
        m = fx.markets()
    except PlanError as e:
        sys.exit(f"{e} See https://fxnewsbias.com/pricing")
    except FXNewsBiasError as e:
        sys.exit(f"{e}")
    gold = m.get("XAU")
    if gold is None or gold.pair.gap is None:
        sys.exit("No XAU/USD read this cycle, stand aside")
    gap = gold.pair.gap
    view = "XAU" if gap > THRESHOLD else "USD" if gap < -THRESHOLD else None
    print(f"XAU {gold.score}  vs  USD {gold.pair.quote_score}   spread {gap:+d}")
    print(f"news view on XAU/USD: {view or 'flat, stand aside'}")
    print(f"next update in {int((m.seconds_until_next_update() or 0) / 60)} min")
    sys.exit(0)

try:
    s = fx.sentiment()
except FXNewsBiasError as e:
    sys.exit(f"{e}")

spread = s.spread(PAIR)
view = s.favours(PAIR, threshold=THRESHOLD)

print(f"{base} {s[base].score}  vs  {quote} {s[quote].score}   spread {spread:+d}")
print(f"news view on {PAIR}: {view or 'flat, stand aside'}")
print(f"scores generated {s.generated_at}, next update in "
      f"{int((s.seconds_until_next_update() or 0) / 60)} min")
