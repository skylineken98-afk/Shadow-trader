import json
import streamlit as st
import pandas as pd
import os
import time
import subprocess
import sys
from supabase import create_client
REFRESH_SECONDS = 60
HISTORY_FILE = "signal_history.json"
with open(HISTORY_FILE, "r") as f:
    signal_history = json.load(f)
    known_contracts = {s.get("contract") for s in signal_history}
st.set_page_config(
    page_title="KMN Shadow Signal",
    page_icon="📈",
    layout="wide"
)
supabase = create_client(
    st.secrets["NEXT_PUBLIC_SUPABASE_URL"],
    st.secrets["NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"]
)

st.title("KMN Shadow Signal")
st.caption("Scan • Score • Signal")
if st.button("🔄 Run Live Scanner"):
    with st.spinner("Scanning options market..."):
        result = subprocess.run(
            [sys.executable, "option_scanner.py"],
            input="\n\n",
            text=True,
            capture_output=True,
            timeout=180
        )

        if result.returncode == 0:
            st.success("Scan complete.")
            st.rerun()
        else:
            st.error("Scanner failed.")
            st.code(result.stderr)

st.info("Options scanner dashboard is online.")

try:
    with open("option_scan_results.json", "r") as f:
        signals = json.load(f)

    if signals:
        df = pd.DataFrame(signals)
        latest_prices = {
    row["contract"]: row["ask"]
    for _, row in df.iterrows()
    }
        for saved_signal in signal_history:
            saved_signal["current_price"] = latest_prices.get(saved_signal["contract"], saved_signal.get("current_price", 0))
            if saved_signal["current_price"] > saved_signal.get("high_price", 0):
                saved_signal["high_price"] = saved_signal["current_price"]
            saved_signal["gain_pct"] = ((saved_signal["current_price"] - saved_signal.get("entry_price", saved_signal["current_price"])) / saved_signal.get("entry_price", saved_signal["current_price"])) * 100
            with open(HISTORY_FILE, "w") as f:
                json.dump(signal_history, f, indent=2)
        top_signals = df.sort_values("score", ascending=False).head(10)
        for _, signal in top_signals.iterrows():
            if signal["contract"] not in known_contracts:
                signal["entry_price"] = signal["ask"]
                signal["current_price"] = signal["ask"]
                signal["gain_pct"] = 0.0
                signal["high_price"] = signal["ask"]
                signal_history.append(signal.to_dict())
                known_contracts.add(signal["contract"])
        with open(HISTORY_FILE, "w") as f:
            json.dump(signal_history, f, indent=2)
        st.metric("Signals Found", len(df))
        st.subheader("top 10 option Signals")
        tab_request, tab_0dte, tab_swing, tab_leaps, tab_winners = st.tabs(["🎯 Request Signal", "⚡ 0DTE", "📈 Swings", "🚀 LEAPS", "🏆 Winners"])

        with tab_0dte:
            today = pd.Timestamp.now().date()
            expirations = pd.to_datetime(df["expiration"]).dt.date

        odte_signals = df[expirations == today].sort_values(
            "score",
            ascending=False
        ).head(10)

    if odte_signals.empty:
        st.info("No 0DTE signals available right now.")
    else:
        st.dataframe(odte_signals, width="stretch")
    with tab_swing:
                swing_signals = df[
        (pd.to_datetime(df["expiration"]) > pd.Timestamp.now() + pd.Timedelta(days=1)) &
        (pd.to_datetime(df["expiration"]) <= pd.Timestamp.now() + pd.Timedelta(days=90))
        ].sort_values("score", ascending=False).head(10)

        st.dataframe(swing_signals, width="stretch")
    with tab_leaps:
        leaps_signals = df[
            pd.to_datetime(df["expiration"]) > pd.Timestamp.now() + pd.Timedelta(days=90)
        ].sort_values("score", ascending=False).head(10)

        st.dataframe(leaps_signals, width="stretch")

    with tab_winners:
        winners = [s for s in signal_history if s.get("high_gain_pct", s.get("gain_pct", 0)) >= 50]
        if winners:
                st.dataframe(pd.DataFrame(winners), width="stretch")
        else:
            st.write("no +50 winners recorded yet.")    
except FileNotFoundError:
    st.error("No scan results found yet. Run the option scanner first.")
st.divider()
with tab_request:
    st.header("🎯 Request a Signal")
    
    signal_type = st.selectbox(
        "Trade style",
            ["0DTE", "Swing", "LEAPS"]
    )
    
    max_budget = st.number_input(
        "Maximum contract cost ($)",
        min_value=1,
        max_value=10000,
        value=300,
        step=25
    )
    
    more_time = st.checkbox("Prefer more time before expiration")
    request_signal = st.button("Request a Signal")
if request_signal:
        if "df" not in locals() or df.empty:
            st.warning("No scan results available yet. Run the option scanner first.")
            st.stop()
        matches = df[df["cost"] <= max_budget].copy()
        expiration_dates = pd.to_datetime(matches["expiration"]).dt.date

        today = pd.Timestamp.now().date()
        days_left = expiration_dates.apply(lambda x: (x - today).days)

        if signal_type == "0DTE":
            matches = matches[days_left == 0]
        elif signal_type == "Swing":
            matches = matches[(days_left >= 1) & (days_left <= 90)]
        elif signal_type == "LEAPS":
            matches = matches[days_left > 90]

        if more_time and not matches.empty:
            matches = matches.assign(days_left=days_left.loc[matches.index])
            matches = matches.sort_values(
                ["days_left", "score"],
                ascending=[False, False]
            )
        else:
            matches = matches.sort_values("score", ascending=False)

        matches = matches.head(3)

        if matches.empty:
            st.warning("No matching signals found right now.")
        else:
            st.subheader("🔥 Your Top Signals")
            for _, signal in matches.iterrows():
                entry = float(signal["ask"])
                stop = entry * 0.80
                target1 = entry * 1.25
                target2 = entry * 1.50
                direction = "BULLISH" if signal["option_type"] == "CALL" else "BEARISH"

                with st.container(border=True):
                    st.subheader(f'{signal["underlying"]} • {signal["option_type"]} — {direction}')
                    st.write(f'**${signal["strike"]} {signal["option_type"]} • {signal["expiration"]}**')

                    col1, col2 = st.columns(2)
                    col1.metric("Entry", f'${entry:.2f}')
                    col2.metric("Stop Loss", f'${stop:.2f}')

                    col3, col4 = st.columns(2)
                    col3.metric("Target 1", f'${target1:.2f}', "+25%")
                    col4.metric("Target 2", f'${target2:.2f}', "+50%")

                    st.write(
                        f'**Contract Cost:** ${entry * 100:.0f}  |  '
                        f'**Shadow Score:** {signal["score"]:.1f}  |  '
                        f'**Delta:** {signal["delta"]:.2f}'
                    )

                    with st.expander("More Details"):
                        st.write(f'Bid / Ask: ${signal["bid"]:.2f} / ${signal["ask"]:.2f}')
                        st.write(f'IV: {signal["iv"]}')
                        st.write(f'Theta: {signal["theta"]}')
                        st.write(f'Gamma: {signal["gamma"]}')
                        st.write(f'Spread: ${signal["spread"]:.2f}')
