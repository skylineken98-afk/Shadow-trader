from dotenv import load_dotenv
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

# ============================================================
# KENNY SHADOW TRADER
# DATA / RESEARCH ONLY — NO ORDER CODE
# ============================================================

load_dotenv(override=True)

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")

client = StockHistoricalDataClient(API_KEY, API_SECRET)

# Core market + Magnificent Seven
SYMBOLS = [
    "IWM", "SPY", "QQQ",
    "AAPL", "MSFT", "AMZN",
    "GOOG", "META", "NVDA", "TSLA"
]

end = datetime.now(timezone.utc)
start = end - timedelta(days=30)


def get_bars(timeframe):
    request = StockBarsRequest(
        symbol_or_symbols=SYMBOLS,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=DataFeed.IEX
    )

    return client.get_stock_bars(request).df


print("\n======================================")
print("       KENNY SHADOW TRADER")
print("       SHADOW MODE — NO ORDERS")
print("======================================\n")

print("Downloading 4-hour candles...")
bars_4h = get_bars(TimeFrame(4, TimeFrameUnit.Hour))

print("Downloading 1-hour candles...")
bars_1h = get_bars(TimeFrame.Hour)

print("Downloading 5-minute candles...")
bars_5m = get_bars(TimeFrame(5, TimeFrameUnit.Minute))

print("\nDATA CONNECTION SUCCESSFUL\n")

for symbol in SYMBOLS:
    print(f"----- {symbol} -----")

    for label, data in [
        ("4H", bars_4h),
        ("1H", bars_1h),
        ("5M", bars_5m),
    ]:
        try:
            s = data.xs(symbol)

            latest = s.iloc[-1]

            print(
                f"{label}: "
                f"Close=${latest['close']:.2f} | "
                f"Volume={int(latest['volume']):,}"
            )

        except (KeyError, IndexError):
            print(f"{label}: No data")

    print()

print("SCAN COMPLETE")
print("NO REAL ORDERS WERE PLACED.")

# Load latest option scanner results
import json

try:
    with open("option_scan_results.json", "r") as f:
        option_results = json.load(f)

    print("\nLATEST OPTION CONTRACTS")
    print("=" * 68)

    option_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    for contract in option_results:
        delta = contract.get("delta")
        volume = contract.get("volume")

        if delta is not None and abs(delta) < 0.10:
            continue

        if volume is not None and volume == 0:
            continue

        print(
    f"{contract.get('core')} | "
    f"Bid ${contract.get('bid')} | "
    f"Ask ${contract.get('ask')} | "
    f"Cost ${contract.get('cost'):.2f} | "
    f"Score {contract.get('score'):.1f}"
)   

except Exception as e:
    print("Could not load option scan results:", e)