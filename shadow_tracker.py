# KENNY SHADOW TRACKER v0.1
# Tracks hypothetical positions only.
# NO REAL ORDER EXECUTION.

from pathlib import Path
from datetime import datetime, timezone
import os

import pandas as pd
from dotenv import load_dotenv
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest


SHADOW_MODE = True

if SHADOW_MODE is not True:
    raise RuntimeError(
        "SAFETY LOCK: Shadow Tracker must remain in SHADOW MODE."
    )


load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")

if not API_KEY or not API_SECRET:
    raise RuntimeError(
        "Alpaca market-data credentials were not loaded."
    )


SHADOW_POSITIONS_FILE = Path("shadow_positions.csv")

option_client = OptionHistoricalDataClient(
    API_KEY,
    API_SECRET
)


def safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_option_mark(contract_symbol):
    """
    Gets a realistic shadow mark from the latest option quote.
    Uses the midpoint when both bid and ask are valid.
    Falls back to bid or ask if necessary.
    """

    request = OptionLatestQuoteRequest(
        symbol_or_symbols=[contract_symbol]
    )

    quotes = option_client.get_option_latest_quote(request)

    quote = quotes.get(contract_symbol)

    if quote is None:
        return None

    bid = safe_float(quote.bid_price)
    ask = safe_float(quote.ask_price)

    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2

    if bid is not None and bid > 0:
        return bid

    if ask is not None and ask > 0:
        return ask

    return None

def evaluate_shadow_exit(entry, mark, mfe_pct, mae_pct):
    """
    Research-only exit engine.
    Returns an exit reason when a hypothetical exit condition is met.
    Returns None when the shadow position should remain OPEN.

    NO REAL ORDERS.
    """

    if entry is None or mark is None or entry <= 0:
        return None

    return_pct = ((mark - entry) / entry) * 100

    # Hard research stop.
    if return_pct <= -30:
        return "SHADOW STOP -30%"

    # Initial profit target.
    if return_pct >= 50:
        return "SHADOW PROFIT TARGET +50%"

    # Protect a trade that previously reached +30%
    # but has given back a significant portion of the move.
    if mfe_pct is not None and mfe_pct >= 30:
        if return_pct <= 15:
            return "SHADOW PROFIT PROTECTION"

    return None

def update_shadow_positions():
    if not SHADOW_POSITIONS_FILE.exists():
        print("No shadow positions file exists yet.")
        print("Waiting for the first qualifying shadow setup.")
        return

    positions = pd.read_csv(SHADOW_POSITIONS_FILE)

    if positions.empty:
        print("No shadow positions recorded yet.")
        return

    required_columns = {
        "contract",
        "option_entry",
        "status"
    }

    missing = required_columns - set(positions.columns)

    if missing:
        print(
            "Missing required columns:",
            ", ".join(sorted(missing))
        )
        return

    open_mask = (
        positions["status"]
        .astype(str)
        .str.upper()
        .eq("OPEN")
    )

    if not open_mask.any():
        print("No OPEN shadow positions.")
        return

    for index in positions.index[open_mask]:
        contract_symbol = str(
            positions.at[index, "contract"]
        )

        entry = safe_float(
            positions.at[index, "option_entry"]
        )

        if not contract_symbol or entry is None or entry <= 0:
            print(
                f"Skipping invalid shadow position at row {index}."
            )
            continue

        try:
            mark = get_option_mark(contract_symbol)

            if mark is None:
                print(
                    f"No usable quote for {contract_symbol}."
                )
                continue

            return_pct = (
                (mark - entry) / entry
            ) * 100

            old_mfe = safe_float(
                positions.at[index, "mfe_pct"]
            )

            old_mae = safe_float(
                positions.at[index, "mae_pct"]
            )

            if old_mfe is None:
                old_mfe = return_pct

            if old_mae is None:
                old_mae = return_pct

            new_mfe = max(old_mfe, return_pct)
            new_mae = min(old_mae, return_pct)

            exit_reason = evaluate_shadow_exit(
                entry,
                mark,
                new_mfe,
                new_mae
            )

            if exit_reason is not None:
                positions.at[index, "status"] = "CLOSED"
                positions.at[index, "option_exit"] = round(mark, 4)
                positions.at[index, "return_pct"] = round(return_pct, 2)
                positions.at[index, "mfe_pct"] = round(new_mfe, 2)
                positions.at[index, "mae_pct"] = round(new_mae, 2)
                positions.at[index, "exit_reason"] = exit_reason

                print()
                print(f"SHADOW EXIT : {contract_symbol}")
                print(f"EXIT PRICE  : ${mark:.2f}")
                print(f"RETURN      : {return_pct:.2f}%")
                print(f"REASON      : {exit_reason}")

            positions.at[index, "return_pct"] = round(
                return_pct,
                2
            )

            positions.at[index, "mfe_pct"] = round(
                new_mfe,
                2
            )

            positions.at[index, "mae_pct"] = round(
                new_mae,
                2
            )

            print()
            print(f"CONTRACT : {contract_symbol}")
            print(f"ENTRY    : ${entry:.2f}")
            print(f"MARK     : ${mark:.2f}")
            print(f"RETURN   : {return_pct:.2f}%")
            print(f"MFE      : {new_mfe:.2f}%")
            print(f"MAE      : {new_mae:.2f}%")

        except Exception as error:
            print(
                f"Tracker error for {contract_symbol}: {error}"
            )

    positions.to_csv(
        SHADOW_POSITIONS_FILE,
        index=False
    )

    print()
    print(
        "Shadow positions updated:",
        datetime.now(timezone.utc).isoformat()
    )


if __name__ == "__main__":
    print("=" * 60)
    print("KENNY SHADOW TRACKER")
    print("HYPOTHETICAL POSITIONS ONLY")
    print("=" * 60)

    update_shadow_positions()

    print("=" * 60)
    print("NO REAL ORDERS WERE PLACED.")