# ICAIF 2026 Trading Agent — Reproduction package

Team member: Cian Delahunty (University College London)

A rule-based, deterministic agent. The target portfolio is equal weight across the 30 competition stocks with a 10% cash buffer (target weight 0.03 per stock, 0.90 invested). Each run reads the live competition schedule and the team's portfolio from the competition platform. If no stock's current weight differs from its target by more than 1 percentage point, the agent uploads nothing for that round (no trade, no fee). Otherwise it uploads the target weights. The first round (`official-2026-10-12-r1`) was the initial purchase from cash; the log records every later decision. No LLM, no randomness and no training are involved.

## Files in this folder

| File | Contents |
| --- | --- |
| `video.mov` | Demonstration video (under 5 minutes) |
| `source.zip` | Complete source: `agent/` (live agent and tools), `backtest/` (backtester), `starter-kit/` (organisers' kit, unmodified) |
| `requirements.txt` | Exact package versions of the environment that ran the agent |
| `decisions.jsonl` | The agent's log of every decision check during the Official Competition (no credentials) |
| `disclosures.md` | LLM, external-data, software and resource disclosures |
| `README.md` | This file |

## System

- Operating system: Ubuntu Linux (Hetzner Cloud server, Helsinki, 4 GB RAM); development on macOS
- Python: 3.11
- Hardware: any machine that runs Python 3.11; no GPU
- Setup time: about 5 minutes. One decision takes a few seconds.

## Setup

```console
unzip source.zip -d icaif-agent && cd icaif-agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp starter-kit/.env.example starter-kit/.env && chmod 600 starter-kit/.env
# Edit starter-kit/.env: CODABENCH_TOKEN=<your token>, ICAIF_PROFILE=profiles/profile99-production.json
# Save team credentials (from the registration receipt) into starter-kit/.icaif/credentials.json:
cd starter-kit && python tools/auto_submit.py fetch --submission-id <registration submission id> && cd ..
```

For the backtest only, place the organisers' file `hourly_market_data_2021_2026.parquet` in `data/`.

## Run

Make one decision for the currently open round, without uploading:

```console
python agent/run_round.py --dry-run --now
```

Live operation, exactly as run during the competition (cron, every 5 minutes on weekdays, 12:00–20:59 UTC). The agent only acts in the last 15 minutes before a round deadline and never uploads twice for one round:

```console
*/5 12-20 * * 1-5 cd /root/icaif-agent && .venv/bin/python agent/run_round.py >> logs/cron.log 2>&1
```

- Inputs: the platform's live schedule and portfolio (`/api/v1/me/portfolio`: per-stock shares, reference price, market value, weight). Information cutoff: the time of the run, always before the round deadline.
- Settings (top of `agent/run_round.py`): `INVESTED = 0.90`, `DRIFT_BAND = 0.01`, `ACT_WINDOW_MINUTES = 15`.
- Output: when a trade is needed, one `decision.json` per round in `starter-kit/private/rounds/<round_id>/`, validated with the kit's own checks and uploaded with the kit's client. Every run appends one line to `logs/decisions.jsonl`.

## Verify the recorded decisions

The agent is deterministic. Put the submitted `decisions.jsonl` in `logs/` and replay every logged input through the same decision function:

```console
python agent/report.py replay      # expects: every decision identical
python agent/report.py summary     # rounds, uploads, largest drift, data sources
python agent/report.py show official-2026-10-12-r1
```

## Backtest used to choose the design

```console
python backtest/backtest.py
```

This replays the 2021–25 hourly data under the competition rules (USD 1m, long-only, 30% cap, 0.1% cost, the official four metrics). It compares the starter-kit momentum baseline with equal-weight and inverse-volatility portfolios, at 100% and 90% invested, under six rebalancing rules. Rounds 2–7 execute at the data's bar opens (10:00 … 15:00 ET), 30 minutes before the official execution times, because the data has no 10:30 … 15:30 prices. Results are written to `logs/backtest_results.csv`.

## Resource status

- Credentials: not included. A reproduction needs the reviewer's own Codabench token and team credentials.
- Market data: the live agent uses only the competition platform. The backtest uses the organisers' development dataset (not redistributed here).
- External services: none apart from the competition platform. See `disclosures.md`.
