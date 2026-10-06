import os
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionChainRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit


# ============================================================
# KENNY SHADOW TRADER v0.1
# DATA -> STATE -> SETUP -> CONTRACT -> SHADOW JOURNAL
# NO ORDER EXECUTION CODE EXISTS IN THIS PROGRAM
# ============================================================

MARKET_TZ = ZoneInfo("America/New_York")

def regular_market_hours():
    now_et = datetime.now(MARKET_TZ)

    if now_et.weekday() >= 5:
        return False

    market_open = now_et.replace(
        hour=9, minute=30, second=0, microsecond=0
    )
    market_close = now_et.replace(
        hour=16, minute=0, second=0, microsecond=0
    )

    return market_open <= now_et < market_close

SHADOW_MODE = True

if SHADOW_MODE is not True:
    raise RuntimeError("SAFETY LOCK: KST must remain in SHADOW MODE.")

load_dotenv()

SHADOW_POSITIONS_FILE = Path("shadow_positions.csv")

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")

if not API_KEY or not API_SECRET:
    raise RuntimeError("Alpaca credentials were not loaded from .env")

stock_client = StockHistoricalDataClient(API_KEY, API_SECRET)
option_client = OptionHistoricalDataClient(API_KEY, API_SECRET)


# ---------------- CONFIGURATION ----------------

SYMBOLS = [
    "SPY", "QQQ", "IWM",
    "AAPL", "MSFT", "AMZN", "GOOG",
    "META", "NVDA", "TSLA"
]

MAX_OPTION_PREMIUM = 6.00
MAX_CONTRACT_COST = 600.00
MAX_SPREAD_PCT = 15.0

# Experimental research threshold, NOT a probability.
MIN_SHADOW_CONFIDENCE = 65

JOURNAL_FILE = Path("shadow_journal.csv")
SHADOW_POSITIONS_FILE = Path("shadow_positions.csv")


# ---------------- HELPERS ----------------

def safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_bars(symbol, timeframe, days):
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=datetime.now(timezone.utc) - timedelta(days=days)
    )

    df = stock_client.get_stock_bars(request).df

    if df.empty:
        return pd.DataFrame()

    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)

    return df.sort_index()


# ---------------- TREND ENGINE ----------------

def direction(df, lookback=4):
    if len(df) < lookback + 1:
        return "UNKNOWN"

    start = float(df["close"].iloc[-lookback - 1])
    end = float(df["close"].iloc[-1])

    change = (end / start) - 1

    if change > 0.002:
        return "BULLISH"

    if change < -0.002:
        return "BEARISH"

    return "FLAT"


def ema_state(df):
    if len(df) < 20:
        return "UNKNOWN"

    ema9 = df["close"].ewm(
        span=9,
        adjust=False
    ).mean().iloc[-1]

    ema20 = df["close"].ewm(
        span=20,
        adjust=False
    ).mean().iloc[-1]

    if ema9 > ema20:
        return "BULLISH"

    if ema9 < ema20:
        return "BEARISH"

    return "FLAT"


def roc(df, periods=3):
    if len(df) <= periods:
        return 0.0

    old = float(df["close"].iloc[-periods - 1])
    new = float(df["close"].iloc[-1])

    return ((new / old) - 1) * 100


def acceleration_state(df):
    if len(df) < 7:
        return "UNKNOWN"

    recent = roc(df, 3)

    previous_start = float(df["close"].iloc[-7])
    previous_end = float(df["close"].iloc[-4])

    previous = (
        (previous_end / previous_start) - 1
    ) * 100

    if recent > previous + 0.05:
        return "ACCELERATING UP"

    if recent < previous - 0.05:
        return "ACCELERATING DOWN"

    return "STABLE"


# ---------------- VWAP ----------------

def session_vwap(df):
    if df.empty:
        return None

    latest_date = df.index[-1].date()

    session = df[
        df.index.date == latest_date
    ].copy()

    if session.empty:
        return None

    volume = session["volume"]

    if volume.sum() <= 0:
        return None

    typical = (
        session["high"]
        + session["low"]
        + session["close"]
    ) / 3

    vwap = (
        (typical * volume).cumsum()
        / volume.cumsum()
    )

    return float(vwap.iloc[-1])


# ---------------- RELATIVE VOLUME ----------------

def relative_volume(df, period=20):
    if len(df) < period + 1:
        return None

    current = float(df["volume"].iloc[-1])

    previous = df["volume"].iloc[
        -period - 1:-1
    ]

    baseline = float(previous.mean())

    if baseline <= 0:
        return None

    return current / baseline


# ---------------- SUPPORT / RESISTANCE ----------------

def levels(df, lookback=20):
    if len(df) < lookback + 1:
        return None, None

    prior = df.iloc[
        -lookback - 1:-1
    ]

    support = float(
        prior["low"].min()
    )

    resistance = float(
        prior["high"].max()
    )

    return support, resistance


def breakout_state(df, support, resistance):
    if (
        len(df) < 2
        or support is None
        or resistance is None
    ):
        return "NONE"

    close = float(df["close"].iloc[-1])
    previous = float(df["close"].iloc[-2])

    high = float(df["high"].iloc[-1])
    low = float(df["low"].iloc[-1])

    tolerance = close * 0.0015

    if close > resistance and previous <= resistance:
        return "BULLISH BREAKOUT"

    if close < support and previous >= support:
        return "BEARISH BREAKDOWN"

    if (
        close >= resistance
        and abs(low - resistance) <= tolerance
    ):
        return "BULLISH RETEST"

    if (
        close <= support
        and abs(high - support) <= tolerance
    ):
        return "BEARISH RETEST"

    return "NONE"


# ---------------- STATE CLASSIFIER ----------------

def classify_state(
    d4h,
    d1h,
    d5m,
    ema5,
    above_vwap,
    acceleration,
    breakout
):
    # Established bullish continuation
    if (
        d4h == "BULLISH"
        and d1h == "BULLISH"
        and d5m == "BULLISH"
        and ema5 == "BULLISH"
        and above_vwap
    ):
        return "ESTABLISHED CONTINUATION", "CALL"

    # Established bearish continuation
    if (
        d4h == "BEARISH"
        and d1h == "BEARISH"
        and d5m == "BEARISH"
        and ema5 == "BEARISH"
        and not above_vwap
    ):
        return "ESTABLISHED CONTINUATION", "PUT"

    # Bullish transition
    if (
        d4h == "BEARISH"
        and d1h == "BULLISH"
        and d5m == "BULLISH"
        and ema5 == "BULLISH"
        and above_vwap
        and acceleration == "ACCELERATING UP"
    ):
        return "CONFIRMED TRANSITION", "CALL"

    # Bearish transition
    if (
        d4h == "BULLISH"
        and d1h == "BEARISH"
        and d5m == "BEARISH"
        and ema5 == "BEARISH"
        and not above_vwap
        and acceleration == "ACCELERATING DOWN"
    ):
        return "CONFIRMED TRANSITION", "PUT"

    # Breakout / retest
    if (
        breakout in (
            "BULLISH BREAKOUT",
            "BULLISH RETEST"
        )
        and d5m == "BULLISH"
        and above_vwap
    ):
        return "BREAKOUT / RETEST", "CALL"

    if (
        breakout in (
            "BEARISH BREAKDOWN",
            "BEARISH RETEST"
        )
        and d5m == "BEARISH"
        and not above_vwap
    ):
        return "BREAKDOWN / RETEST", "PUT"

    return "UNRESOLVED / CHOP", "NONE"


# ---------------- EXPERIMENTAL CONFIDENCE ----------------

def confidence_score(
    bias,
    d4h,
    d1h,
    d5m,
    ema5,
    above_vwap,
    rvol,
    acceleration,
    breakout
):
    if bias == "NONE":
        return 35

    score = 50

    if bias == "CALL":

        if d4h == "BULLISH":
            score += 10

        if d1h == "BULLISH":
            score += 10

        if d5m == "BULLISH":
            score += 10

        if ema5 == "BULLISH":
            score += 5

        if above_vwap:
            score += 5

        if acceleration == "ACCELERATING UP":
            score += 5

    if bias == "PUT":

        if d4h == "BEARISH":
            score += 10

        if d1h == "BEARISH":
            score += 10

        if d5m == "BEARISH":
            score += 10

        if ema5 == "BEARISH":
            score += 5

        if not above_vwap:
            score += 5

        if acceleration == "ACCELERATING DOWN":
            score += 5

    if rvol is not None:

        if rvol >= 1.5:
            score += 10

        elif rvol >= 1.0:
            score += 5

        elif rvol < 0.5:
            score -= 5

    if breakout != "NONE":
        score += 5

    return max(
        0,
        min(100, score)
    )


# ---------------- OPTION CONTRACT ENGINE ----------------

def desired_option_type(contract_symbol, bias):
    """
    OCC option symbols contain C or P before
    the strike field.

    Example:
    SPY261005C00500000
    """

    # Last 9 characters = C/P + 8 digit strike.
    if len(contract_symbol) < 9:
        return False

    option_type = contract_symbol[-9]

    if bias == "CALL":
        return option_type == "C"

    if bias == "PUT":
        return option_type == "P"

    return False


def analyze_contract(contract_symbol, snapshot, bias):

    if not desired_option_type(
        contract_symbol,
        bias
    ):
        return None

    quote = getattr(
        snapshot,
        "latest_quote",
        None
    )

    if quote is None:
        return None

    bid = safe_float(
        getattr(
            quote,
            "bid_price",
            None
        )
    )

    ask = safe_float(
        getattr(
            quote,
            "ask_price",
            None
        )
    )

    if (
        bid is None
        or ask is None
        or bid <= 0
        or ask <= 0
    ):
        return None

    # Conservative hypothetical fill:
    # use current ask, not midpoint.
    executable = ask

    if executable > MAX_OPTION_PREMIUM:
        return None

    midpoint = (bid + ask) / 2

    if midpoint <= 0:
        return None

    spread = ask - bid
    spread_pct = (
        spread / midpoint
    ) * 100

    if spread_pct > MAX_SPREAD_PCT:
        return None

    cost = executable * 100

    if cost > MAX_CONTRACT_COST:
        return None

    greeks = getattr(
        snapshot,
        "greeks",
        None
    )

    delta = None
    gamma = None
    theta = None
    vega = None

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

    iv = safe_float(
        getattr(
            snapshot,
            "implied_volatility",
            None
        )
    )

    score = 100 - (
        spread_pct * 2
    )

    if delta is not None:

        abs_delta = abs(delta)

        if 0.30 <= abs_delta <= 0.70:
            score += 15

        elif abs_delta < 0.15:
            score -= 20

    # Avoid selecting something only because
    # it is extremely cheap.
    if ask < 0.20:
        score -= 20

    return {
        "contract": contract_symbol,
        "bid": bid,
        "ask": ask,
        "mid": midpoint,
        "spread_pct": spread_pct,
        "cost": cost,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "iv": iv,
        "quality": score
    }


def best_contract(symbol, bias):

    request = OptionChainRequest(
        underlying_symbol=symbol
    )

    chain = option_client.get_option_chain(
        request
    )

    candidates = []

    for contract_symbol, snapshot in chain.items():

        candidate = analyze_contract(
            contract_symbol,
            snapshot,
            bias
        )

        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item["quality"],
        reverse=True
    )

    return candidates[0]


# ---------------- JOURNAL ----------------

JOURNAL_COLUMNS = [
    "timestamp_utc",
    "symbol",
    "underlying_price",
    "market_state",
    "bias",
    "confidence",
    "direction_4h",
    "direction_1h",
    "direction_5m",
    "ema_5m",
    "vwap",
    "rvol",
    "roc_5m",
    "acceleration",
    "support",
    "resistance",
    "breakout_state",
    "contract",
    "option_bid",
    "option_ask",
    "estimated_cost",
    "spread_pct",
    "delta",
    "gamma",
    "theta",
    "vega",
    "iv",
    "contract_quality",
    "decision"
]


def write_journal(row):

    exists = JOURNAL_FILE.exists()

    with JOURNAL_FILE.open(
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=JOURNAL_COLUMNS
        )

        if not exists:
            writer.writeheader()

        writer.writerow(row)

def save_shadow_position(symbol, bias, contract, price, confidence, state):
    """
    Records a hypothetical shadow position only.
    This function does NOT place any real orders.
    """

    if not contract:
        return

    contract_symbol = contract.get("contract", "")
    option_entry = contract.get("ask", "")
    estimated_cost = contract.get("cost", "")

    # Prevent duplicate open shadow positions.
    if SHADOW_POSITIONS_FILE.exists():
        try:
            existing = pd.read_csv(SHADOW_POSITIONS_FILE)

            if not existing.empty and "status" in existing.columns:
                open_positions = existing[
                    existing["status"].astype(str).str.upper() == "OPEN"
                ]

                if "contract" in open_positions.columns:
                    duplicate = open_positions[
                        open_positions["contract"].astype(str)
                        == str(contract_symbol)
                    ]

                    if not duplicate.empty:
                        print(
                            f"SHADOW POSITION ALREADY OPEN: {contract_symbol}"
                        )
                        return

        except Exception as error:
            print(
                f"Shadow position duplicate check warning: {error}"
            )

    trade_id = (
        f"{symbol}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )

    row = {
        "trade_id": trade_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "direction": bias,
        "market_state": state,
        "setup_type": "",
        "confidence": confidence,
        "underlying_entry": price,
        "contract": contract_symbol,
        "option_entry": option_entry,
        "estimated_cost": estimated_cost,
        "invalidation": "",
        "status": "OPEN",
        "underlying_exit": "",
        "option_exit": "",
        "return_pct": "",
        "mfe_pct": "",
        "mae_pct": "",
        "exit_reason": ""
    }

    file_exists = SHADOW_POSITIONS_FILE.exists()

    pd.DataFrame([row]).to_csv(
        SHADOW_POSITIONS_FILE,
        mode="a",
        header=not file_exists,
        index=False
    )

    print(
        f"SHADOW POSITION RECORDED: {contract_symbol}"
    )


# ---------------- MAIN SCANNER ----------------

print()
print("=" * 72)
print("KENNY SHADOW TRADER v0.1")
print("DATA -> MARKET STATE -> SETUP -> CONTRACT -> JOURNAL")
print("NO REAL-MONEY TRADING")
print("=" * 72)

if not regular_market_hours():
    print("Outside regular market hours — scan skipped.")
    print("NO REAL ORDERS WERE PLACED.")
    raise SystemExit(0)

for symbol in SYMBOLS:

    print(f"\n----- {symbol} -----")

    try:

        bars4h = get_bars(
            symbol,
            TimeFrame(
                4,
                TimeFrameUnit.Hour
            ),
            60
        )

        bars1h = get_bars(
            symbol,
            TimeFrame.Hour,
            30
        )

        bars5m = get_bars(
            symbol,
            TimeFrame(
                5,
                TimeFrameUnit.Minute
            ),
            7
        )

        if (
            bars4h.empty
            or bars1h.empty
            or bars5m.empty
        ):
            print("DATA UNAVAILABLE")
            continue

        price = float(
            bars5m["close"].iloc[-1]
        )

        d4h = direction(
            bars4h,
            3
        )

        d1h = direction(
            bars1h,
            4
        )

        d5m = direction(
            bars5m,
            4
        )

        ema5 = ema_state(
            bars5m
        )

        vwap = session_vwap(
            bars5m
        )

        above_vwap = (
            vwap is not None
            and price > vwap
        )

        rvol = relative_volume(
            bars5m
        )

        roc5 = roc(
            bars5m,
            3
        )

        acceleration = (
            acceleration_state(
                bars5m
            )
        )

        support, resistance = levels(
            bars5m,
            20
        )

        breakout = breakout_state(
            bars5m,
            support,
            resistance
        )

        state, bias = classify_state(
            d4h,
            d1h,
            d5m,
            ema5,
            above_vwap,
            acceleration,
            breakout
        )

        confidence = confidence_score(
            bias,
            d4h,
            d1h,
            d5m,
            ema5,
            above_vwap,
            rvol,
            acceleration,
            breakout
        )

        print(f"Price        : ${price:.2f}")
        print(f"4H           : {d4h}")
        print(f"1H           : {d1h}")
        print(f"5M           : {d5m}")
        print(f"EMA          : {ema5}")

        if vwap is not None:
            relation = (
                "ABOVE"
                if above_vwap
                else "BELOW"
            )

            print(
                f"VWAP         : "
                f"${vwap:.2f} "
                f"({relation})"
            )

        else:
            print("VWAP         : UNAVAILABLE")

        if rvol is not None:
            print(
                f"RVOL         : "
                f"{rvol:.2f}x"
            )

        else:
            print(
                "RVOL         : UNAVAILABLE"
            )

        print(
            f"5M ROC       : "
            f"{roc5:.2f}%"
        )

        print(
            f"Acceleration : "
            f"{acceleration}"
        )

        print(
            f"Break state  : "
            f"{breakout}"
        )

        print(
            f"Market state : "
            f"{state}"
        )

        print(
            f"Bias         : "
            f"{bias}"
        )

        print(
            f"Confidence   : "
            f"{confidence}/100"
        )

        # -----------------------------------------
        # NO-TRADE / REJECTED SETUP
        # -----------------------------------------

        if (
            bias == "NONE"
            or confidence < MIN_SHADOW_CONFIDENCE
        ):

            decision = (
                "REJECTED - "
                "UNRESOLVED/LOW CONFIDENCE"
            )

            print(
                "DECISION     : NO SHADOW TRADE"
            )

            write_journal({
                "timestamp_utc":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),

                "symbol": symbol,
                "underlying_price": price,
                "market_state": state,
                "bias": bias,
                "confidence": confidence,
                "direction_4h": d4h,
                "direction_1h": d1h,
                "direction_5m": d5m,
                "ema_5m": ema5,
                "vwap": vwap,
                "rvol": rvol,
                "roc_5m": roc5,
                "acceleration": acceleration,
                "support": support,
                "resistance": resistance,
                "breakout_state": breakout,
                "contract": "",
                "option_bid": "",
                "option_ask": "",
                "estimated_cost": "",
                "spread_pct": "",
                "delta": "",
                "gamma": "",
                "theta": "",
                "vega": "",
                "iv": "",
                "contract_quality": "",
                "decision": decision
            })

            continue

        # -----------------------------------------
        # OPTION SELECTION
        # -----------------------------------------

        contract = best_contract(
            symbol,
            bias
        )

        if contract is None:

            print(
                "DECISION     : "
                "NO QUALIFYING OPTION"
            )

            decision = (
                "REJECTED - "
                "NO QUALIFYING CONTRACT"
            )

        else:

            print()
            print(
                "*** HYPOTHETICAL SHADOW CANDIDATE ***"
            )

            print(
                f"Direction    : {bias}"
            )

            print(
                f"Contract     : "
                f"{contract['contract']}"
            )

            print(
                f"Bid / Ask    : "
                f"${contract['bid']:.2f} / "
                f"${contract['ask']:.2f}"
            )

            print(
                f"Spread       : "
                f"{contract['spread_pct']:.1f}%"
            )

            print(
                f"Est. Cost    : "
                f"${contract['cost']:.2f}"
            )

            if contract["delta"] is not None:
                print(
                    f"Delta        : "
                    f"{contract['delta']:.3f}"
                )

            if contract["iv"] is not None:
                print(
                    f"IV           : "
                    f"{contract['iv'] * 100:.1f}%"
                )

            print(
                f"Quality      : "
                f"{contract['quality']:.1f}"
            )

            print(
                "ACTION       : "
                "RECORD ONLY"
            )

            decision = (
                "HYPOTHETICAL SHADOW ENTRY"
            )

        write_journal({
            "timestamp_utc":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "symbol": symbol,
            "underlying_price": price,
            "market_state": state,
            "bias": bias,
            "confidence": confidence,
            "direction_4h": d4h,
            "direction_1h": d1h,
            "direction_5m": d5m,
            "ema_5m": ema5,
            "vwap": vwap,
            "rvol": rvol,
            "roc_5m": roc5,
            "acceleration": acceleration,
            "support": support,
            "resistance": resistance,
            "breakout_state": breakout,

            "contract":
                contract["contract"]
                if contract else "",

            "option_bid":
                contract["bid"]
                if contract else "",

            "option_ask":
                contract["ask"]
                if contract else "",

            "estimated_cost":
                contract["cost"]
                if contract else "",

            "spread_pct":
                contract["spread_pct"]
                if contract else "",

            "delta":
                contract["delta"]
                if contract else "",

            "gamma":
                contract["gamma"]
                if contract else "",

            "theta":
                contract["theta"]
                if contract else "",

            "vega":
                contract["vega"]
                if contract else "",

            "iv":
                contract["iv"]
                if contract else "",

            "contract_quality":
                contract["quality"]
                if contract else "",

            "decision": decision
        })
	
        save_shadow_position(symbol, bias, contract, price, confidence, state)
	
    except Exception as error:

        print(
            f"ERROR: {error}"
        )


print()
print("=" * 72)
print("SCAN COMPLETE")
print(
    f"Journal saved to: "
    f"{JOURNAL_FILE.resolve()}"
)
print("NO REAL ORDERS WERE PLACED.")
print("=" * 72)