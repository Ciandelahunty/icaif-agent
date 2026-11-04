# Agent and resource disclosures

## Agent summary

- Agent name and version: Drift-band equal-weight agent, v1 (`agent/run_round.py`, unchanged throughout the Official Competition)
- Repository or source revision: included as `source.zip`; the commit history in the repository shows the agent file committed on 11 October 2026
- Strategy summary: equal weight across the 30 stocks with 10% cash (0.03 per stock). The agent rebalances to target only when any stock's weight differs from target by more than 1 percentage point; otherwise it submits nothing for that round.
- Decision inputs and information cutoff: the competition platform's live schedule and the team's own portfolio snapshot (shares, reference prices, market values, weights). Inputs are read at run time, in the final 15 minutes before each round's deadline.

## LLM use

- No LLM is used by this agent.

(The code was written with the help of an AI coding assistant, Claude by Anthropic. The agent itself makes no model calls when deciding or submitting.)

## External data

No external data is used.

The code contains a fallback that would fetch last-traded prices from Yahoo Finance's public chart endpoint, used only if the platform's portfolio omitted per-stock weights and values. It was never triggered: every logged decision in `decisions.jsonl` shows its portfolio data source as the competition platform ("platform weights", or "cash equals NAV" for the initial purchase). Run `python agent/report.py summary` to confirm.

## Software

| Software | Version | Use |
| --- | --- | --- |
| Python | 3.11 | Runtime |
| httpx | 0.28.1 | Platform requests (organisers' starter-kit client) |
| pandas, numpy, pyarrow | see `requirements.txt` | Backtest only |
| matplotlib | see `requirements.txt` | Optional drift chart in `agent/report.py` |
| Organisers' starter kit | as downloaded, unmodified | Validation and submission |

## Resources

- Hardware: one Hetzner Cloud virtual server (Helsinki, 4 GB RAM, Ubuntu); no GPU
- Scheduling: cron on that server
- Paid or private data: none
- Randomness: none; identical inputs give identical decisions
