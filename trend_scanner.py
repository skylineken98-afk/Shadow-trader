import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.trading.client import TradingClient
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")

client = StockHistoricalDataClient(API_KEY, API_SECRET)
trading_client = TradingClient(API_KEY, API_SECRET, paper=True)

SYMBOLS = [
    "SPY", "QQQ", "IWM",
    "AAPL", "MSFT", "AMZN", "GOOG",
    "META", "NVDA", "TSLA"
]
SECTOR_ETFS = {
    "TECH": "XLK",
    "SEMICONDUCTORS": "SMH",
    "COMMUNICATION": "XLC",
    "CONSUMER": "XLY",
    "FINANCIALS": "XLF",
    "INDUSTRIALS": "XLI",
    "MATERIALS": "XLB",
    "ENERGY": "XLE",
    "STAPLES": "XLP",
    "HEALTHCARE": "XLV",
    "UTILITIES": "XLU",
    "REAL_ESTATE": "XLRE",
}


def get_market_universe():
    assets = trading_client.get_all_assets()
    symbols = []

    for asset in assets:
        symbol = getattr(asset, "symbol", None)
        tradable = getattr(asset, "tradable", False)
        status = str(getattr(asset, "status", "")).lower()
        asset_class = str(getattr(asset, "asset_class", "")).lower()

        if (
            symbol
            and tradable
            and "active" in status
            and ("equity" in asset_class or "us_equity" in asset_class)
        ):
            symbols.append(symbol)

    return symbols

def fast_market_filter(symbols, max_symbols=300, batch_size=200):
    candidates = []

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]

        try:
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=datetime.now(timezone.utc) - timedelta(days=7)
            )

            df = client.get_stock_bars(req).df

            if df.empty:
                continue

            for symbol in batch:
                try:
                    if not isinstance(df.index, pd.MultiIndex):
                        continue

                    if symbol not in df.index.get_level_values(0):
                        continue

                    symbol_df = df.xs(symbol)

                    if len(symbol_df) < 2:
                        continue

                    close = float(symbol_df["close"].iloc[-1])
                    prev_close = float(symbol_df["close"].iloc[-2])
                    volume = float(symbol_df["volume"].iloc[-1])

                    if prev_close <= 0:
                        continue

                    move = ((close / prev_close) - 1) * 100

                    if close < 0.25:
                        continue

                    if volume < 50000:
                        continue

                    if abs(move) < 1.0:
                        continue

                    candidates.append({
                        "symbol": symbol,
                        "price": close,
                        "move": move,
                        "volume": volume
                    })

                except Exception:
                    continue

        except Exception:
            continue

    candidates.sort(
        key=lambda x: abs(x["move"]),
        reverse=True
    )

    return candidates[:max_symbols]

def get_bars(symbol, timeframe, days):
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=datetime.now(timezone.utc) - timedelta(days=days)
    )

    df = client.get_stock_bars(req).df

    if df.empty:
        return pd.DataFrame()

    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)

    return df.sort_index()

def premarket_gap(symbol):
    df = get_bars(symbol, TimeFrame.Minute, 5)

    if df.empty:
        return None

    data = df.copy()

    # Alpaca timestamps are UTC. Convert them to New York market time.
    data.index = pd.to_datetime(data.index)

    if data.index.tz is None:
        data.index = data.index.tz_localize("UTC")

    data.index = data.index.tz_convert("America/New_York")

    trading_dates = sorted(set(data.index.date))

    if len(trading_dates) < 2:
        return None

    # Look for the newest day that has premarket data
    for i in range(len(trading_dates) - 1, 0, -1):
        current_day = trading_dates[i]
        previous_day = trading_dates[i - 1]

        previous = data[data.index.date == previous_day]
        current = data[data.index.date == current_day]

        previous_regular = previous.between_time("09:30", "16:00")
        premarket = current.between_time("04:00", "09:29")

        if previous_regular.empty or premarket.empty:
            continue

        previous_close = float(previous_regular["close"].iloc[-1])
        premarket_price = float(premarket["close"].iloc[-1])

        gap_pct = ((premarket_price - previous_close) / previous_close) * 100

        if gap_pct >= 5:
            gap_status = "STRONG GAP UP"
        elif gap_pct >= 2:
            gap_status = "GAP UP"
        elif gap_pct <= -5:
            gap_status = "STRONG GAP DOWN"
        elif gap_pct <= -2:
            gap_status = "GAP DOWN"
        else:
            gap_status = "FLAT"

        return {
            "previous_close": previous_close,
            "premarket_price": premarket_price,
            "gap_pct": gap_pct,
            "gap_status": gap_status
        }

    return None

def direction(df, lookback=4):
    if len(df) < lookback + 1:
        return "UNKNOWN"

    start = df["close"].iloc[-lookback - 1]
    end = df["close"].iloc[-1]

    change = (end / start) - 1

    if change > 0.002:
        return "BULLISH"
    if change < -0.002:
        return "BEARISH"

    return "FLAT"


def ema_state(df):
    if len(df) < 20:
        return "UNKNOWN"

    ema9 = df["close"].ewm(span=9, adjust=False).mean()
    ema20 = df["close"].ewm(span=20, adjust=False).mean()

    if ema9.iloc[-1] > ema20.iloc[-1]:
        return "BULLISH"

    if ema9.iloc[-1] < ema20.iloc[-1]:
        return "BEARISH"

    return "FLAT"


def calculate_vwap(df):
    if df.empty:
        return None

    # Use current UTC trading date only.
    today = df.index[-1].date()
    session = df[df.index.date == today].copy()

    if session.empty:
        return None

    typical = (
        session["high"] +
        session["low"] +
        session["close"]
    ) / 3

    volume = session["volume"]

    if volume.sum() == 0:
        return None

    vwap = (typical * volume).cumsum() / volume.cumsum()

    return float(vwap.iloc[-1])


def relative_volume(df, period=20):
    if len(df) < period + 1:
        return None

    current = df["volume"].iloc[-1]
    baseline = df["volume"].iloc[-period - 1:-1].mean()

    if baseline <= 0:
        return None

    return float(current / baseline)


def roc(df, periods):
    if len(df) <= periods:
        return None

    old = df["close"].iloc[-periods - 1]
    new = df["close"].iloc[-1]

    return float(((new / old) - 1) * 100)


def momentum_acceleration(df):
    if len(df) < 7:
        return "UNKNOWN", 0.0, 0.0

    recent = roc(df, 3)
    previous_start = df["close"].iloc[-7]
    previous_end = df["close"].iloc[-4]

    previous = ((previous_end / previous_start) - 1) * 100

    if recent is None:
        return "UNKNOWN", 0.0, previous

    if recent > previous + 0.05:
        state = "ACCELERATING UP"

    elif recent < previous - 0.05:
        state = "ACCELERATING DOWN"

    else:
        state = "STABLE"

    return state, recent, previous


def support_resistance(df, lookback=20):
    if len(df) < lookback:
        return None, None

    prior = df.iloc[-lookback - 1:-1]

    if prior.empty:
        return None, None

    resistance = float(prior["high"].max())
    support = float(prior["low"].min())

    return support, resistance

def key_levels(symbol):
    df = get_bars(symbol, TimeFrame.Minute, 5)

    if df.empty:
        return None

    data = df.copy()

    data.index = pd.to_datetime(data.index)

    if data.index.tz is None:
        data.index = data.index.tz_localize("UTC")

    data.index = data.index.tz_convert("America/New_York")

    trading_dates = sorted(set(data.index.date))

    if len(trading_dates) < 2:
        return None

    for i in range(len(trading_dates) - 1, 0, -1):
        current_day = trading_dates[i]
        previous_day = trading_dates[i - 1]

        previous = data[data.index.date == previous_day]
        current = data[data.index.date == current_day]

        previous_regular = previous.between_time("09:30", "16:00")
        premarket = current.between_time("04:00", "09:29")

        if previous_regular.empty:
            continue

        previous_high = float(previous_regular["high"].max())
        previous_low = float(previous_regular["low"].min())

        premarket_high = None
        premarket_low = None

        if not premarket.empty:
            premarket_high = float(premarket["high"].max())
            premarket_low = float(premarket["low"].min())

        return {
            "previous_high": previous_high,
            "previous_low": previous_low,
            "premarket_high": premarket_high,
            "premarket_low": premarket_low
        }

    return None

def vwap_setup(df, vwap):
    if vwap is None or len(df) < 2:
        return "UNAVAILABLE"

    previous_close = float(df["close"].iloc[-2])
    current_close = float(df["close"].iloc[-1])
    current_low = float(df["low"].iloc[-1])
    current_high = float(df["high"].iloc[-1])

    tolerance = current_close * 0.0015

    if previous_close <= vwap and current_close > vwap:
        return "BULLISH RECLAIM"

    if previous_close >= vwap and current_close < vwap:
        return "BEARISH LOSS"

    if current_close > vwap and current_low <= vwap + tolerance:
        return "BULLISH REJECTION"

    if current_close < vwap and current_high >= vwap - tolerance:
        return "BEARISH REJECTION"

    if current_close > vwap:
        return "ABOVE VWAP"

    return "BELOW VWAP"

def opening_range_setup(df):
    if df.empty:
        return None

    data = df.copy()
    data.index = pd.to_datetime(data.index)

    if data.index.tz is None:
        data.index = data.index.tz_localize("UTC")

    data.index = data.index.tz_convert("America/New_York")

    trading_dates = sorted(set(data.index.date))

    if not trading_dates:
        return None

    latest_day = trading_dates[-1]
    today = data[data.index.date == latest_day]

    opening_range = today.between_time("09:30", "09:59")

    if len(opening_range) < 6:
        return None

    orb_high = float(opening_range["high"].max())
    orb_low = float(opening_range["low"].min())
    current_price = float(today["close"].iloc[-1])

    if current_price > orb_high:
        status = "BULLISH ORB BREAKOUT"
    elif current_price < orb_low:
        status = "BEARISH ORB BREAKDOWN"
    else:
        status = "INSIDE OPENING RANGE"

    return {
        "high": orb_high,
        "low": orb_low,
        "status": status
    }

def relative_strength_compare(stock_df, benchmark_df, lookback=12):
    if stock_df.empty or benchmark_df.empty:
        return None

    if len(stock_df) < lookback + 1 or len(benchmark_df) < lookback + 1:
        return None

    stock_start = float(stock_df["close"].iloc[-lookback - 1])
    stock_end = float(stock_df["close"].iloc[-1])

    benchmark_start = float(benchmark_df["close"].iloc[-lookback - 1])
    benchmark_end = float(benchmark_df["close"].iloc[-1])

    stock_change = ((stock_end / stock_start) - 1) * 100
    benchmark_change = ((benchmark_end / benchmark_start) - 1) * 100

    strength = stock_change - benchmark_change

    if strength >= 1.0:
        status = "STRONGLY OUTPERFORMING"
    elif strength > 0:
        status = "OUTPERFORMING"
    elif strength <= -1.0:
        status = "STRONGLY UNDERPERFORMING"
    elif strength < 0:
        status = "UNDERPERFORMING"
    else:
        status = "MATCHING MARKET"

    return {
        "stock_change": stock_change,
        "benchmark_change": benchmark_change,
        "strength": strength,
        "status": status
    }

def get_shadow_entry(symbol):
    try:
        positions = pd.read_csv("shadow_positions.csv")

        if positions.empty:
            return None

        if "symbol" not in positions.columns or "status" not in positions.columns:
            return None

        open_positions = positions[
            (positions["symbol"].astype(str).str.upper() == symbol.upper())
            & (positions["status"].astype(str).str.upper() == "OPEN")
        ]

        if open_positions.empty:
            return None

        entry = open_positions.iloc[-1]["underlying_entry"]

        if pd.isna(entry):
            return None

        return float(entry)

    except Exception:
        return None
def get_shadow_direction(symbol):
    try:
        positions = pd.read_csv("shadow_positions.csv")

        if positions.empty:
            return None

        if "symbol" not in positions.columns or "status" not in positions.columns:
            return None

        open_positions = positions[
            (positions["symbol"].astype(str).str.upper() == symbol.upper())
            & (positions["status"].astype(str).str.upper() == "OPEN")
        ]

        if open_positions.empty:
            return None

        direction = open_positions.iloc[-1]["direction"]

        if pd.isna(direction):
            return None

        return str(direction).upper()

    except Exception:
        return None

def runner_action(
    current_price,
    entry_price,
    direction,
    atr_target,
    above_vwap,
    ema5,
    accel,
    breakout
):
    if entry_price is None or current_price is None:
        return "NO POSITION"

    if direction == "PUT":
        profit = entry_price - current_price
    else:
        profit = current_price - entry_price

    if atr_target is not None and profit >= atr_target:
        if direction == "PUT":
            if (
                not above_vwap
                and ema5 == "BEARISH"
                and accel == "ACCELERATING DOWN"
            ):
                return "HOLD RUNNER"

            if breakout == "BEARISH BREAKDOWN":
                return "HOLD RUNNER"

        else:
            if (
                above_vwap
                and ema5 == "BULLISH"
                and accel == "ACCELERATING UP"
            ):
                return "HOLD RUNNER"

            if breakout == "BULLISH BREAKOUT":
                return "HOLD RUNNER"

        return "TRIM / TRAIL STOP"

    if direction == "PUT":
        if (
            above_vwap
            and ema5 == "BULLISH"
            and accel == "ACCELERATING UP"
        ):
            return "EXIT - STRUCTURE WEAKENED"

    else:
        if (
            not above_vwap
            and ema5 == "BEARISH"
            and accel == "ACCELERATING DOWN"
        ):
            return "EXIT - STRUCTURE WEAKENED"

    return "HOLD"

def market_breadth(candidates):
    if not candidates:
        return {
            "advancers": 0,
            "decliners": 0,
            "advance_pct": 0.0,
            "decline_pct": 0.0,
            "state": "UNAVAILABLE"
        }

    advancers = sum(1 for item in candidates if item["move"] > 0)
    decliners = sum(1 for item in candidates if item["move"] < 0)

    total = advancers + decliners

    if total == 0:
        return {
            "advancers": 0,
            "decliners": 0,
            "advance_pct": 0.0,
            "decline_pct": 0.0,
            "state": "NEUTRAL"
        }

    advance_pct = (advancers / total) * 100
    decline_pct = (decliners / total) * 100

    if advance_pct >= 65:
        state = "STRONG BULLISH BREADTH"
    elif advance_pct >= 55:
        state = "BULLISH BREADTH"
    elif decline_pct >= 65:
        state = "STRONG BEARISH BREADTH"
    elif decline_pct >= 55:
        state = "BEARISH BREADTH"
    else:
        state = "MIXED BREADTH"

    return {
        "advancers": advancers,
        "decliners": decliners,
        "advance_pct": advance_pct,
        "decline_pct": decline_pct,
        "state": state
    }

def calculate_atr(df, period=14):
    if df is None or df.empty or len(df) < period + 1:
        return None

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean().iloc[-1]

    if pd.isna(atr):
        return None

    return float(atr)

def market_regime(spy_df, qqq_df):
    if spy_df.empty or qqq_df.empty:
        return "UNKNOWN"

    if len(spy_df) < 50 or len(qqq_df) < 50:
        return "UNKNOWN"

    spy = spy_df.copy()
    qqq = qqq_df.copy()

    spy["ema20"] = spy["close"].ewm(span=20, adjust=False).mean()
    spy["ema50"] = spy["close"].ewm(span=50, adjust=False).mean()

    qqq["ema20"] = qqq["close"].ewm(span=20, adjust=False).mean()
    qqq["ema50"] = qqq["close"].ewm(span=50, adjust=False).mean()

    spy_close = float(spy["close"].iloc[-1])
    spy_ema20 = float(spy["ema20"].iloc[-1])
    spy_ema50 = float(spy["ema50"].iloc[-1])

    qqq_close = float(qqq["close"].iloc[-1])
    qqq_ema20 = float(qqq["ema20"].iloc[-1])
    qqq_ema50 = float(qqq["ema50"].iloc[-1])

    spy_range = ((spy["high"] - spy["low"]) / spy["close"]).tail(20).mean()
    qqq_range = ((qqq["high"] - qqq["low"]) / qqq["close"]).tail(20).mean()

    avg_range = (spy_range + qqq_range) / 2

    bullish = (
        spy_close > spy_ema20 > spy_ema50
        and qqq_close > qqq_ema20 > qqq_ema50
    )

    bearish = (
        spy_close < spy_ema20 < spy_ema50
        and qqq_close < qqq_ema20 < qqq_ema50
    )

    if bullish and avg_range >= 0.01:
        return "BULLISH / HIGH VOLATILITY"

    if bullish:
        return "BULLISH / CONTROLLED"

    if bearish and avg_range >= 0.01:
        return "BEARISH / HIGH VOLATILITY"

    if bearish:
        return "BEARISH / CONTROLLED"

    if avg_range >= 0.01:
        return "CHOPPY / HIGH VOLATILITY"

    return "CHOPPY / LOW VOLATILITY"

def sector_rotation_snapshot(spy_df, sector_dfs, lookback=12):
    results = []

    for sector_name, sector_df in sector_dfs.items():
        rs = relative_strength_compare(
            sector_df,
            spy_df,
            lookback
        )

        if rs is None:
            continue

        results.append({
            "sector": sector_name,
            "strength": rs["strength"],
            "status": rs["status"]
        })

    results.sort(
        key=lambda x: x["strength"],
        reverse=True
    )

    return results

def breakout_state(df, support, resistance):
    if len(df) < 2 or support is None or resistance is None:
        return "NONE"

    close = float(df["close"].iloc[-1])
    previous_close = float(df["close"].iloc[-2])

    if close > resistance and previous_close <= resistance:
        return "BULLISH BREAKOUT"

    if close < support and previous_close >= support:
        return "BEARISH BREAKDOWN"

    # Basic retest/acceptance check.
    tolerance = close * 0.0015

    if (
        close >= resistance
        and abs(float(df["low"].iloc[-1]) - resistance) <= tolerance
    ):
        return "BULLISH RETEST"

    if (
        close <= support
        and abs(float(df["high"].iloc[-1]) - support) <= tolerance
    ):
        return "BEARISH RETEST"

    return "NONE"


def classify_market(
    d4h,
    d1h,
    d5m,
    ema5,
    above_vwap,
    acceleration,
    breakout
):
    bullish_confirmation = (
        d5m == "BULLISH"
        and ema5 == "BULLISH"
        and above_vwap
    )

    bearish_confirmation = (
        d5m == "BEARISH"
        and ema5 == "BEARISH"
        and not above_vwap
    )

    if (
        d4h == "BULLISH"
        and d1h == "BULLISH"
        and bullish_confirmation
    ):
        return "ESTABLISHED CONTINUATION", "CALL"

    if (
        d4h == "BEARISH"
        and d1h == "BEARISH"
        and bearish_confirmation
    ):
        return "ESTABLISHED CONTINUATION", "PUT"

    if (
        d4h == "BEARISH"
        and d1h == "BULLISH"
        and bullish_confirmation
        and acceleration == "ACCELERATING UP"
    ):
        return "CONFIRMED TRANSITION", "CALL"

    if (
        d4h == "BULLISH"
        and d1h == "BEARISH"
        and bearish_confirmation
        and acceleration == "ACCELERATING DOWN"
    ):
        return "CONFIRMED TRANSITION", "PUT"

    if breakout in ("BULLISH BREAKOUT", "BULLISH RETEST"):
        if d5m == "BULLISH" and above_vwap:
            return "BREAKOUT / RETEST", "CALL"

    if breakout in ("BEARISH BREAKDOWN", "BEARISH RETEST"):
        if d5m == "BEARISH" and not above_vwap:
            return "BREAKDOWN / RETEST", "PUT"

    return "UNRESOLVED / CHOP", "NONE"


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
    score = 50

    if bias == "NONE":
        score -= 15

    bullish = bias == "CALL"
    bearish = bias == "PUT"

    if bullish:
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

    if bearish:
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

    return max(0, min(100, score))


print("\n" + "=" * 62)
print("KENNY SHADOW TRADER - MARKET STATE ENGINE v2")
print("4H -> 1H -> 5M")
print("SHADOW MODE ONLY")
print("=" * 62)

spy_rotation_df = get_bars(
    "SPY",
    TimeFrame(5, TimeFrameUnit.Minute),
    7
)

sector_rotation_data = {}

for sector_name, sector_ticker in SECTOR_ETFS.items():
    sector_rotation_data[sector_name] = get_bars(
        sector_ticker,
        TimeFrame(5, TimeFrameUnit.Minute),
        7
    )

sector_rotation = sector_rotation_snapshot(
    spy_rotation_df,
    sector_rotation_data
)

print("\nSECTOR ROTATION LEADERS")

if sector_rotation:
    for item in sector_rotation[:5]:
        print(
            f"{item['sector']:15} "
            f"{item['strength']:+.2f}% "
            f"{item['status']}"
        )
else:
    print("SECTOR ROTATION: UNAVAILABLE")

print("-" * 62)

market_universe = get_market_universe()
market_candidates = fast_market_filter(market_universe)
breadth = market_breadth(market_candidates)

print(f"MARKET UNIVERSE FOUND: {len(market_universe)} symbols")
print(f"FAST FILTER: {len(market_candidates)} candidates from {len(market_universe)} symbols")
print(
    f"MARKET BREADTH: "
    f"{breadth['advancers']} ADV / "
    f"{breadth['decliners']} DEC | "
    f"{breadth['advance_pct']:.1f}% UP | "
    f"{breadth['state']}"
)
print("-" * 62)

deep_scan_symbols = list(dict.fromkeys(
    SYMBOLS + [item["symbol"] for item in market_candidates[:50]]
))

print(f"DEEP SCAN SYMBOLS: {len(deep_scan_symbols)}")

for symbol in deep_scan_symbols:

    try:
        bars4h = get_bars(
            symbol,
            TimeFrame(4, TimeFrameUnit.Hour),
            60
        )

        bars1h = get_bars(
            symbol,
            TimeFrame.Hour,
            30
        )
        gap = premarket_gap(symbol)
        levels = key_levels(symbol)
        bars5m = get_bars(
            symbol,
            TimeFrame(5, TimeFrameUnit.Minute),
            7
        )

        spy5m = get_bars(
            "SPY",
            TimeFrame(5, TimeFrameUnit.Minute),
            7
        )

        atr = calculate_atr(bars5m)

        qqq5m = get_bars(
            "QQQ",
            TimeFrame(5, TimeFrameUnit.Minute),
            7
        )

        rs_spy = relative_strength_compare(bars5m, spy5m)
        rs_qqq = relative_strength_compare(bars5m, qqq5m)
        regime = market_regime(spy5m, qqq5m)

        if bars4h.empty or bars1h.empty or bars5m.empty:
            print(f"\n{symbol}: DATA UNAVAILABLE")
            continue

        d4h = direction(bars4h, 3)
        d1h = direction(bars1h, 4)
        d5m = direction(bars5m, 4)

        ema5 = ema_state(bars5m)

        vwap = calculate_vwap(bars5m)
        vwap_signal = vwap_setup(bars5m, vwap)
        orb = opening_range_setup(bars5m)
        current_price = float(bars5m["close"].iloc[-1])
        entry_price = get_shadow_entry(symbol)
        shadow_direction = get_shadow_direction(symbol)

        above_vwap = (
            vwap is not None
            and current_price > vwap
        )

        rvol = relative_volume(bars5m)

        accel, recent_roc, previous_roc = (
            momentum_acceleration(bars5m)
        )

        support, resistance = support_resistance(
            bars5m,
            20
        )

        breakout = breakout_state(
            bars5m,
            support,
            resistance
        )
        if atr is not None:
            atr_stop = atr * 1.5
            atr_target = atr * 2.5
        else:
            atr_stop = None
            atr_target = None

        runner_status = runner_action(
            current_price,
            entry_price,
            shadow_direction,
            atr_target,
            above_vwap,
            ema5,
            accel,
            breakout
        )

        state, bias = classify_market(
            d4h,
            d1h,
            d5m,
            ema5,
            above_vwap,
            accel,
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
            accel,
            breakout
        )

        print(f"\n----- {symbol} -----")

        print(f"PRICE        : ${current_price:.2f}")

        if gap is not None:
            print(f"PREMARKET GAP : {gap['gap_pct']:+.2f}%")
            print(f"GAP STATUS    : {gap['gap_status']}")
        else:
            print("PREMARKET GAP : UNAVAILABLE")

        if levels is not None:
            print(f"PREV DAY HIGH : ${levels['previous_high']:.2f}")
            print(f"PREV DAY LOW  : ${levels['previous_low']:.2f}")

            if levels["premarket_high"] is not None:
                print(f"PREMARKET HIGH: ${levels['premarket_high']:.2f}")
                print(f"PREMARKET LOW : ${levels['premarket_low']:.2f}")
            else:
                print("PREMARKET HIGH: UNAVAILABLE")
                print("PREMARKET LOW : UNAVAILABLE")
        else:
            print("KEY LEVELS    : UNAVAILABLE")

        print(f"4H DIRECTION : {d4h}")
        print(f"1H DIRECTION : {d1h}")
        print(f"5M DIRECTION : {d5m}")

        print(f"5M EMA       : {ema5}")

        if vwap is not None:
            relation = "ABOVE" if above_vwap else "BELOW"
            print(
                f"VWAP         : ${vwap:.2f} "
                f"({relation})"
            )
        else:
            print("VWAP         : UNAVAILABLE")

        print(f"VWAP SETUP    : {vwap_signal}")
        if orb is not None:
            print(f"ORB HIGH      : ${orb['high']:.2f}")
            print(f"ORB LOW       : ${orb['low']:.2f}")
            print(f"ORB STATUS    : {orb['status']}")
        else:
            print("ORB STATUS    : UNAVAILABLE")

        if rvol is not None:
            print(f"REL VOLUME   : {rvol:.2f}x")
        else:
            print("REL VOLUME   : UNAVAILABLE")

        print(f"VS SPY        : {rs_spy['strength']:+.2f}% ({rs_spy['status']})" if rs_spy is not None else "VS SPY        : UNAVAILABLE")
        print(f"VS QQQ        : {rs_qqq['strength']:+.2f}% ({rs_qqq['status']})" if rs_qqq is not None else "VS QQQ        : UNAVAILABLE")
        print(f"MARKET REGIME : {regime}")

        print(
            f"ACCELERATION : {accel}"
        )

        if support is not None:
            print(f"SUPPORT      : ${support:.2f}")
            print(f"RESISTANCE   : ${resistance:.2f}")

        print(f"BREAK STATUS : {breakout}")
        print(f"ATR          : {atr:.2f}" if atr is not None else "ATR          : UNAVAILABLE")
        print(f"ATR STOP     : {atr_stop:.2f}" if atr_stop is not None else "ATR STOP     : UNAVAILABLE")
        print(f"ATR TARGET   : {atr_target:.2f}" if atr_target is not None else "ATR TARGET   : UNAVAILABLE")
        print(f"RUNNER ACTION: {runner_status}")

        print("-" * 35)

        print(f"MARKET STATE : {state}")
        print(f"BIAS         : {bias}")
        print(f"CONFIDENCE   : {confidence}/100")

    except Exception as e:
        print(f"\n{symbol}: ERROR - {e}")


print("\n" + "=" * 62)
print("SCAN COMPLETE")
print("NO REAL ORDERS WERE PLACED.")