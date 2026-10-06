import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")

client = OptionHistoricalDataClient(
    API_KEY,
    API_SECRET
)

# Core universe
SYMBOLS = [
    "SPY", "QQQ", "IWM", "SPX",
    "AAPL", "MSFT", "AMZN", "GOOG",
    "META", "NVDA", "TSLA",
    "AMD", "INTC", "MU", "AVGO",
    "COIN", "PLTR", "SOFI",

    # Lower-priced / budget-friendly underlyings
    "F", "BAC", "T", "SNAP",
    "HOOD", "RIVN", "NIO",
    "MARA", "CLF", "AAL",
    "PLUG", "LCID", "RIOT",
    "JOBY", "UPST", "AFRM", "KGC",
    "WFC", "KEY", "PFE", "CCL",
    "NCLH", "UAL", "DAL", "VZ",
    "PINS", "LYFT", "SLV", "GDX",
    "XLF", "HYG", "SOXL",
]

manual_symbol = input("Manual scan ticker (press Enter for normal scan): ").strip().upper()

if manual_symbol:
    SYMBOLS = [manual_symbol]

budget_input = input("Maximum contract cost in dollars (press Enter for no limit): ").strip()
MAX_CONTRACT_COST = float(budget_input) if budget_input else None

# Kenny Shadow Trader rules
MAX_PREMIUM = (MAX_CONTRACT_COST / 100) if MAX_CONTRACT_COST is not None else float("inf")
MAX_SPREAD_PERCENT = 15.0   # reject extremely wide spreads
MAX_RESULTS = 5

today = datetime.now(timezone.utc).date()
max_expiration = today + timedelta(days=45)


def safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except:
        return None


def analyze_contract(symbol, snapshot):

    quote = getattr(snapshot, "latest_quote", None)
    trade = getattr(snapshot, "latest_trade", None)
    greeks = getattr(snapshot, "greeks", None)

    if quote is None:
        return None

    bid = safe_float(getattr(quote, "bid_price", None))
    ask = safe_float(getattr(quote, "ask_price", None))

    if bid is None or ask is None:
        return None

    if bid <= 0 or ask <= 0:
        return None

    midpoint = (bid + ask) / 2

    # Use ask as conservative executable entry estimate.
    executable_price = ask

    if executable_price > MAX_PREMIUM:
        return None

    spread = ask - bid
    spread_pct = (
        (spread / midpoint) * 100
        if midpoint > 0 else 999
    )

    if spread_pct > MAX_SPREAD_PERCENT:
        return None

    last = None

    if trade is not None:
        last = safe_float(
            getattr(trade, "price", None)
        )

    delta = gamma = theta = vega = rho = None

    if greeks is not None:
        delta = safe_float(
            getattr(greeks, "delta", None)
        )
        gamma = safe_float(
            getattr(greeks, "gamma", None)
        )
        theta = safe_float(
            getattr(greeks, "theta", None)
        )
        vega = safe_float(
            getattr(greeks, "vega", None)
        )
        rho = safe_float(
            getattr(greeks, "rho", None)
        )

    iv = safe_float(
        getattr(snapshot, "implied_volatility", None)
    )

    expiry_raw = symbol[-15:-9]
    option_letter = symbol[-9]
    strike = int(symbol[-8:]) / 1000

    expiry = datetime.strptime(expiry_raw, "%y%m%d").strftime("%m/%d/%y")
    option_type = "CALL" if option_letter == "C" else "PUT"
    underlying = symbol[:-15]

    core = f"{underlying} {expiry} ${strike:g} {option_type}"    

    return {
        "contract": symbol,
        "scanned_at": datetime.now().astimezone().isoformat(),
        "underlying": underlying,
        "expiration": expiry,
        "strike": strike,
        "option_type": option_type,
        "core": core,
        "bid": bid,
        "ask": ask,
        "mid": midpoint,
        "last": last,
        "spread": spread,
        "spread_pct": spread_pct,
        "cost": executable_price * 100,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
        "iv": iv
    }


def contract_score(c):
    score = 100.0

    # Penalize wide spreads
    score -= c["spread_pct"] * 2

    # Avoid ultra-cheap lottery-style contracts
    if c["ask"] < 0.20:
        score -= 20

    # Reward affordable contracts
    if c["ask"] <= MAX_PREMIUM:
        score += 5

    # Delta quality scoring
    if c["delta"] is not None:
        abs_delta = abs(c["delta"])

        if 0.30 <= abs_delta <= 0.70:
            score += 15
        elif 0.20 <= abs_delta < 0.30:
            score += 7
        elif abs_delta < 0.15:
            score -= 30

    return score


print()
print("=" * 68)
print("KENNY SHADOW TRADER - OPTION CONTRACT ENGINE")
print("READ-ONLY / SHADOW MODE")
print(f"MAX PREMIUM: ${MAX_PREMIUM:.2f}/share")
print(f"MAX CONTRACT COST: ${MAX_PREMIUM * 100:.0f}")
print("=" * 68)

all_results = []

for underlying in SYMBOLS:

    print(f"\n----- {underlying} -----")

    try:
        request = OptionChainRequest(
            underlying_symbol=underlying
        )

        chain = client.get_option_chain(request)

        candidates = []

        for contract_symbol, snapshot in chain.items():

            result = analyze_contract(
                contract_symbol,
                snapshot
            )

            if result is None:
                continue

            result["score"] = contract_score(result)

            candidates.append(result)
            all_results.append(result)

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        if not candidates:
            print(
                "No qualifying liquid contracts "
                "under premium/spread limits."
            )
            continue

        for number, c in enumerate(
            candidates[:MAX_RESULTS],
            start=1
        ):

            print()
            print(f"#{number} {c['core']}")
            print(
                f"Bid / Ask : "
                f"${c['bid']:.2f} / ${c['ask']:.2f}"
            )

            print(
                f"Mid       : ${c['mid']:.2f}"
            )

            print(
                f"Spread    : "
                f"{c['spread_pct']:.1f}%"
            )

            print(
                f"Est Cost  : ${c['cost']:.2f}"
            )

            if c["iv"] is not None:
                print(
                    f"IV        : "
                    f"{c['iv'] * 100:.1f}%"
                )

            if c["delta"] is not None:
                print(
                    f"Delta     : {c['delta']:.3f}"
                )

            if c["gamma"] is not None:
                print(
                    f"Gamma     : {c['gamma']:.4f}"
                )

            if c["theta"] is not None:
                print(
                    f"Theta     : {c['theta']:.4f}"
                )

            if c["vega"] is not None:
                print(
                    f"Vega      : {c['vega']:.4f}"
                )

            print(
                f"Quality   : {c['score']:.1f}"
            )

    except Exception as e:

        print(
            "OPTION DATA ERROR:",
            str(e)
        )


print()
print("=" * 68)
print("OPTION SCAN COMPLETE")
print("NO REAL ORDERS WERE PLACED.")
print("=" * 68)

import json

with open("option_scan_results.json", "w") as f:
    json.dump(all_results, f, indent=2)