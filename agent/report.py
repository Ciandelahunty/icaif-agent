#!/usr/bin/env python3
"""Read-only reports on the live agent's decision log (logs/decisions.jsonl).

Nothing here contacts the platform or uploads anything.

    python agent/report.py show official-2026-10-12-r1   # every logged check for one round
    python agent/report.py replay                        # re-run decide() on every logged input
    python agent/report.py summary [--plot drift.png]    # whole-competition summary
"""
import argparse
from collections import Counter, OrderedDict
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_round as agent  # noqa: E402  (the exact code that made the live decisions)

LOG = agent.LOG_FILE


def load():
    return [json.loads(line, parse_float=Decimal) for line in LOG.read_text(encoding='utf-8').splitlines() if line.strip()]


def live(rows):
    """Real decisions only: drop dry runs and error lines."""
    return [r for r in rows if r.get('round_id') and not r.get('dry_run')]


def by_round(rows):
    """The last logged check of each round, in order (the one that decided the round)."""
    out = OrderedDict()
    for r in live(rows):
        out[r['round_id']] = r
    return out


def drift(entry, symbols):
    weights = entry.get('current_weights')
    if not weights:
        return None
    target = agent.target_weights(symbols)
    return max(abs(float(weights.get(s, 0)) - float(target[s])) for s in symbols)


def state_from_log(entry):
    weights = entry.get('current_weights')
    if weights:
        weights = {s: float(w) for s, w in weights.items()}
        return {'invested': any(w > 0 for w in weights.values()), 'weights': weights}
    source = entry.get('portfolio_source')
    if source in ('cash equals NAV', 'platform shares') and entry.get('target_weights') is not None:
        return {'invested': False, 'weights': None}
    if source in (None, 'portfolio not readable'):
        return {'invested': None, 'weights': None}
    return {'invested': True, 'weights': None}


def cmd_show(args):
    rows = [r for r in load() if r.get('round_id') == args.round_id]
    if not rows:
        print(f'No log entries for {args.round_id}.')
        return 1
    for n, r in enumerate(rows, 1):
        print(f'--- check {n} of {len(rows)} for {args.round_id} ---')
        for key, value in r.items():
            if isinstance(value, dict) and key.endswith('weights'):
                items = [f'{s} {float(w):.3%}' for s, w in value.items()]
                print(f'{key}:')
                for i in range(0, len(items), 6):
                    print('    ' + '   '.join(items[i:i + 6]))
            elif isinstance(value, dict):
                print(f'{key}:')
                for k, v in value.items():
                    print(f'    {k}: {v}')
            else:
                print(f'{key}: {value}')
        print()
    return 0


def cmd_replay(args):
    rows = live(load())
    checked = matches = 0
    for r in rows:
        symbols = list(r.get('current_weights') or r.get('target_weights') or _symbols())
        weights, reason = agent.decide(symbols, state_from_log(r))
        logged_upload = r.get('target_weights') is not None
        same = (weights is not None) == logged_upload and reason == r.get('decision')
        if weights is not None and logged_upload:
            same = same and all(Decimal(str(weights[s])) == Decimal(str(r['target_weights'][s])) for s in symbols)
        checked += 1
        matches += same
        if not same or args.verbose:
            print(('MATCH   ' if same else 'MISMATCH'), r['round_id'], r['logged_at'][:16], '|', reason)
    print(f'\nReplayed {checked} logged decisions through decide(): {matches} identical, {checked - matches} different.')
    return 0 if matches == checked else 1


def _symbols():
    data = json.loads((agent.KIT / 'universe.json').read_text(encoding='utf-8'))
    return [s for group in data['sectors'].values() for s in group]


def cmd_summary(args):
    rows = load()
    symbols = _symbols()
    rounds = by_round(rows)
    results = Counter(r.get('result') for r in live(rows))
    uploads = [r for r in live(rows) if r.get('target_weights') is not None]
    errors = [r for r in rows if r.get('result') == 'ERROR']
    drifts = [(rid, drift(r, symbols)) for rid, r in rounds.items()]
    known = [(rid, d) for rid, d in drifts if d is not None]
    print(f'Rounds evaluated: {len(rounds)}  (checks logged: {sum(results.values())})')
    print(f'Uploads: {len(uploads)}  ' + ', '.join(f"{u['round_id']} ({u['decision']})" for u in uploads))
    print(f'Rounds with no upload: {len(rounds) - len({u["round_id"] for u in uploads})}')
    if known:
        rid, top = max(known, key=lambda x: x[1])
        print(f'Largest drift seen: {top * 100:.2f}pp in {rid} (band: {agent.DRIFT_BAND * 100:.0f}pp)')
    navs = [(rid, float(r['nav'])) for rid, r in rounds.items() if r.get('nav')]
    if navs:
        print(f'NAV at first logged check: {navs[0][1]:,.0f}   at last: {navs[-1][1]:,.0f} ({navs[-1][0]})')
    sources = Counter(r.get('portfolio_source') for r in live(rows))
    print('Portfolio data sources used: ' + ', '.join(f'{k} ({v})' for k, v in sources.items()))
    print(f'Transient errors (no round affected): {len(errors)}' + ''.join(f"\n  {e['logged_at'][:16]} {e.get('error')}" for e in errors))
    if args.plot and known:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(range(len(known)), [d * 100 for _, d in known], color='#2563eb', lw=1.8, label='Largest weight drift')
        ax.axhline(agent.DRIFT_BAND * 100, color='#dc2626', ls='--', lw=1.2, label='Rebalance band (1pp)')
        days = [rid[9:19] for rid, _ in known]
        ticks = [i for i in range(len(days)) if i == 0 or days[i] != days[i - 1]]
        ax.set_xticks(ticks, [days[i][5:] for i in ticks], rotation=0, fontsize=8)
        ax.set_ylabel('Percentage points')
        ax.set_ylim(0, agent.DRIFT_BAND * 100 * 1.25)
        ax.set_title('Largest drift from 3% target at each round (official competition)')
        ax.legend(frameon=False, loc='upper left')
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=160)
        print(f'Chart saved to {args.plot}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show', help='print every logged check for one round')
    show.add_argument('round_id')
    replay = sub.add_parser('replay', help="re-run decide() on every logged input and compare")
    replay.add_argument('--verbose', action='store_true')
    summary = sub.add_parser('summary', help='whole-competition summary')
    summary.add_argument('--plot', help='also save a drift chart to this PNG file (needs matplotlib)')
    args = parser.parse_args()
    return {'show': cmd_show, 'replay': cmd_replay, 'summary': cmd_summary}[args.command](args)


if __name__ == '__main__':
    raise SystemExit(main())
