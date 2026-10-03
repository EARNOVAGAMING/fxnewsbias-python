"""Gold (XAU/USD) sentiment and the latest session call. Pro plans only.

    export FXNEWSBIAS_API_KEY=fxnb_live_...
    python examples/gold.py
"""

import sys

from fxnewsbias import Client, FXNewsBiasError, PlanError

try:
    fx = Client()
    m = fx.markets()
    call = fx.market_session_bias("XAU")
except PlanError as e:
    sys.exit(f"{e} See https://fxnewsbias.com/pricing")
except FXNewsBiasError as e:
    sys.exit(f"{e}")

gold = m.get("XAU")
if gold is None:
    sys.exit(f"No gold reading in this response. Available: {m.symbols()}")

print(f"gold {gold.score} {gold.bias}, updated {gold.updated_at}")
for d in gold.drivers:
    print(f"  - {d}")

p = gold.pair
if p.gap is None:
    print(f"{p.name}: no {p.quote} score this cycle, so no pair read")
else:
    print(f"{p.name}: gold {gold.score} vs {p.quote} {p.quote_score}, "
          f"gap {p.gap:+d}, {p.bias}")

if call.has_call:
    print(f"latest session call: {call.session_date} {call.session} "
          f"{call.tone} (strength {call.strength})")
else:
    print("no session call yet")

print(f"next update in {int((m.seconds_until_next_update() or 0) / 60)} min")
