#!/usr/bin/env python3
"""Backtester for the ICAIF 2026 Trading Agent Competition.

Replays the 2021-2025 hourly data under the competition rules (USD 1m start,
long-only, 30% per-stock cap, 0.1% transaction cost) and scores each strategy
with the four official metrics: cumulative return (M1), Sharpe ratio (M2),
maximum drawdown (M3) and turnover (M4).

Timing proxy
------------
The data's bars start at 09:30, 10:00, 11:00 ... 15:00 ET, but the competition
executes at 09:30, 10:30 ... 15:30 ET. Each round here executes at the open of
the bar starting at or just before its official time (09:30 exact, the rest
30 minutes early). Decisions use only bars that have already closed, so there
is no look-ahead. The 16:00 close is recorded as an extra valuation point for
drawdown, as in the official rules.

Usage (from the icaif-agent folder):
    python backtest/backtest.py
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

INITIAL_NAV = 1_000_000.0
COST = 0.001              # 0.1% of traded value
CAP = 0.30                # max weight per stock
SLOTS = ["09:30", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"]
ANNUALISE = np.sqrt(252 * 7)
WARMUP_DAYS = 60          # history needed before the first trade (for volatility)
WINDOW_DAYS = 15          # length of the official competition


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_prices(path):
    """Return wide tables of bar opens and closes: one row per bar, one column per stock."""
    df = pd.read_parquet(path)
    df = df[df["timestamp_et"].dt.strftime("%H:%M").isin(SLOTS)]
    opens = df.pivot_table(index="timestamp_et", columns="ticker", values="open", aggfunc="last").sort_index()
    closes = df.pivot_table(index="timestamp_et", columns="ticker", values="close", aggfunc="last").sort_index()
    # A missing bar means no trade happened: carry the last known price forward.
    closes = closes.ffill().bfill()
    opens = opens.fillna(closes.shift(1)).ffill().bfill()
    return opens, closes


# ---------------------------------------------------------------------------
# Strategies: each takes a context and returns 30 target weights, or None to skip the round
# ---------------------------------------------------------------------------
def momentum_baseline(ctx):
    """The starter kit's example: top five stocks by positive 5-bar momentum, 20% each, every round."""
    closes = ctx.past_closes
    if len(closes) < 6:
        return None
    score = closes[-1] / closes[-6] - 1
    ranked = sorted(range(len(score)), key=lambda i: (-score[i], ctx.symbols[i]))
    picks = [i for i in ranked if score[i] > 0][:5]
    weights = np.zeros(len(score))
    weights[picks] = 0.20
    return weights


class TargetStrategy:
    """Hold a fixed-mix target; trade only when some weight drifts more than `delta` from it.

    weighting: "equal" (c/30 each) or "invvol" (in proportion to 1/volatility)
    invested:  c, the fraction of the portfolio in stocks (the rest is cash)
    delta:     drift threshold in weight units (0.01 = 1 percentage point);
               0 = rebalance every round, inf = buy and hold
    """

    def __init__(self, weighting, invested, delta, vol_bars=WARMUP_DAYS * 7):
        self.weighting, self.invested, self.delta, self.vol_bars = weighting, invested, delta, vol_bars
        self.target = None

    def compute_target(self, ctx):
        n = len(ctx.symbols)
        if self.weighting == "equal":
            raw = np.ones(n)
        else:
            window = ctx.past_closes[-(self.vol_bars + 1):]
            sigma = np.diff(np.log(window), axis=0).std(axis=0, ddof=1)
            raw = 1 / sigma
        weights = self.invested * raw / raw.sum()
        return np.minimum(weights, CAP)

    def __call__(self, ctx):
        if ctx.first_round_of_day or self.target is None:
            self.target = self.compute_target(ctx)   # refresh the target once a day
        if not ctx.invested:
            return self.target                       # first purchase
        drift = np.max(np.abs(ctx.weights - self.target))
        return self.target if drift > self.delta else None


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def check_weights(w):
    if np.any(w < 0) or np.any(w > CAP + 1e-12) or w.sum() > 1 + 1e-10:
        raise ValueError("Strategy returned weights that break the competition rules.")


def simulate(strategy, opens, closes, start, end):
    """Run one strategy over bars [start, end). Returns per-round records and valuation points."""
    O, C = opens.values, closes.values
    times, symbols = opens.index, list(opens.columns)
    day = times.normalize()
    shares, cash = np.zeros(len(symbols)), INITIAL_NAV
    rounds, valuations = [], []

    for t in range(start, end):
        prices = O[t]
        nav = cash + shares @ prices                       # value just before this round's trade
        valuations.append((times[t], nav))
        if rounds:
            rounds[-1]["nav_end"] = nav                    # ...which ends the previous round

        ctx = SimpleNamespace(
            symbols=symbols,
            past_closes=C[:t],                             # only bars that have already closed
            weights=shares * prices / nav,
            invested=bool(shares.any()),
            first_round_of_day=(t == start or day[t] != day[t - 1]),
        )
        target = strategy(ctx)

        traded = 0.0
        if target is not None:
            target = np.asarray(target, dtype=float)
            check_weights(target)
            current = shares * prices
            nav_after = nav
            for _ in range(5):                             # costs reduce the NAV the weights apply to
                cost = COST * np.abs(target * nav_after - current).sum()
                nav_after = nav - cost
            new_values = target * nav_after
            traded = np.abs(new_values - current).sum()
            cash = nav - new_values.sum() - COST * traded
            shares = new_values / prices

        rounds.append({"time": times[t], "nav_before": nav, "traded": traded, "nav_end": np.nan})

        if t == end - 1 or day[t + 1] != day[t]:           # 16:00 close valuation
            close_value = cash + shares @ C[t]
            valuations.append((day[t] + pd.Timedelta(hours=16), close_value))
            if t == end - 1:
                rounds[-1]["nav_end"] = close_value        # final round ends at the last close

    rounds = pd.DataFrame(rounds)
    valuations = pd.Series(dict(valuations)).sort_index()
    return rounds, valuations


# ---------------------------------------------------------------------------
# Official metrics
# ---------------------------------------------------------------------------
def metrics(rounds, valuations):
    r = rounds["nav_end"] / rounds["nav_before"] - 1
    s = r.std(ddof=1)
    v = valuations.values
    peak = np.maximum.accumulate(v)
    return {
        "M1_return": rounds["nav_end"].iloc[-1] / rounds["nav_before"].iloc[0] - 1,
        "M2_sharpe": ANNUALISE * r.mean() / s if s > 0 else 0.0,
        "M3_max_drawdown": ((peak - v) / peak).max(),
        "M4_turnover": (rounds["traded"] / rounds["nav_before"]).mean(),
        "trades": int((rounds["traded"] > 0).sum()),
    }


def window_metrics(make_strategy, opens, closes, start, days):
    """Rerun each consecutive block of `days` trading days as a fresh competition, starting in cash."""
    day = opens.index.normalize()
    first_bars = [t for t in range(start, len(opens)) if t == start or day[t] != day[t - 1]]
    results = []
    for i in range(0, len(first_bars) - days + 1, days):
        end = first_bars[i + days] if i + days < len(first_bars) else len(opens)
        results.append(metrics(*simulate(make_strategy(), opens, closes, first_bars[i], end)))
    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser(description="Backtest strategies under the ICAIF 2026 rules.")
    parser.add_argument("--data", default="data/hourly_market_data_2021_2026.parquet")
    parser.add_argument("--out", default="logs/backtest_results.csv")
    args = parser.parse_args()

    opens, closes = load_prices(args.data)
    days = opens.index.normalize().unique()
    start = int(np.argmax(opens.index.normalize() == days[WARMUP_DAYS]))
    end = len(opens)
    print(f"Backtest: {opens.index[start]:%Y-%m-%d} to {opens.index[-1]:%Y-%m-%d}, "
          f"{len(days) - WARMUP_DAYS} trading days, {end - start} rounds, {opens.shape[1]} stocks\n")

    variants = {"baseline_momentum": momentum_baseline}
    for weighting in ["equal", "invvol"]:
        for invested in [1.0, 0.9]:
            for delta in [0, 0.0025, 0.005, 0.01, 0.02, np.inf]:
                label = "every_round" if delta == 0 else "buy_hold" if np.isinf(delta) else f"d{delta * 100:g}pp"
                variants[f"{weighting}_c{invested:g}_{label}"] = (weighting, invested, delta)

    full, windows, yearly = {}, {}, {}
    for name, spec in variants.items():
        make_strategy = (lambda spec=spec: spec) if callable(spec) else (lambda spec=spec: TargetStrategy(*spec))
        rounds, valuations = simulate(make_strategy(), opens, closes, start, end)
        full[name] = metrics(rounds, valuations)
        windows[name] = window_metrics(make_strategy, opens, closes, start, WINDOW_DAYS).median()
        r = rounds["nav_end"] / rounds["nav_before"] - 1
        yearly[name] = r.groupby(rounds["time"].dt.year).apply(lambda x: ANNUALISE * x.mean() / x.std(ddof=1))

    full = pd.DataFrame(full).T
    windows = pd.DataFrame(windows).T
    yearly = pd.DataFrame(yearly).T

    pd.set_option("display.width", 200)
    fmt = {"M1_return": "{:.2%}", "M2_sharpe": "{:.2f}", "M3_max_drawdown": "{:.2%}", "M4_turnover": "{:.3%}", "trades": "{:.0f}"}
    show = lambda df: df.apply(lambda col: col.map(fmt[col.name].format) if col.name in fmt else col)
    print("Full period")
    print(show(full).to_string(), "\n")
    print(f"Median over {WINDOW_DAYS}-day windows (each scored like a fresh competition)")
    print(show(windows).to_string(), "\n")
    print("Sharpe ratio by year")
    print(yearly.round(2).to_string())

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pd.concat({"full": full, f"median_{WINDOW_DAYS}d": windows}, axis=1).to_csv(args.out)
    print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
