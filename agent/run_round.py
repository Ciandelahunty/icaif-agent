#!/usr/bin/env python3
"""Live agent for the ICAIF 2026 Trading Agent Competition.

Strategy: hold an equally weighted portfolio of the 30 stocks with a cash
buffer (each target weight = INVESTED / 30), and rebalance only when some
stock's actual weight drifts more than DRIFT_BAND from its target. When no
rebalance is needed, nothing is uploaded: a missing round means no trade and
no cost under the competition rules.

Each run handles at most one round and is safe to repeat: if the current
round already has an upload, it does nothing. Cron runs it every 5 minutes on
trading days; it only acts in the last ACT_WINDOW_MINUTES before a deadline.

Usage (from the icaif-agent folder):
    python agent/run_round.py            # normal run (used by cron)
    python agent/run_round.py --dry-run  # decide and log, never upload
    python agent/run_round.py --now      # act now, ignoring the act window
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / 'starter-kit'
sys.path.insert(0, str(KIT))
os.chdir(KIT)  # the client keeps .env, .icaif/ and private/ inside starter-kit

from kit.config import load_environment  # noqa: E402
from kit.contracts import dumps_json  # noqa: E402
from kit.original_client import OriginalSession, load_profile  # noqa: E402

# --- Strategy settings (chosen from the 2021-25 backtest) --------------------
INVESTED = Decimal('0.90')     # fraction of the portfolio in stocks; the rest is cash
DRIFT_BAND = 0.01              # rebalance when any weight is >1 percentage point off target
ACT_WINDOW_MINUTES = 15        # act only this close to a deadline (freshest information)
MIN_MINUTES_LEFT = 1           # never start an upload in the final minute
PHASE = 'official'
LOG_FILE = REPO / 'logs' / 'decisions.jsonl'

SHARE_KEYS = ('shares', 'quantity', 'qty', 'units', 'position')
VALUE_KEYS = ('market_value', 'value', 'notional', 'position_value')
WEIGHT_KEYS = ('weight', 'current_weight')
NAV_KEYS = ('nav', 'total_value', 'portfolio_value', 'equity', 'net_asset_value')
CASH_KEYS = ('cash', 'cash_balance')


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
def target_weights(symbols):
    """Equal weight: INVESTED / 30 per stock, rounded down so the total never exceeds INVESTED."""
    each = (INVESTED / len(symbols)).quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
    return {symbol: each for symbol in symbols}


def decide(symbols, state):
    """Return (weights or None, reason). `state` comes from read_portfolio()."""
    target = target_weights(symbols)
    if state['invested'] is False:
        return target, 'initial purchase from cash'
    if state['invested'] is None:
        return None, 'skip: portfolio not readable (no trade is the safe default)'
    if state['weights'] is None:
        return None, 'skip: invested but current weights unavailable (no trade is the safe default)'
    drift = max(abs(state['weights'].get(s, 0.0) - float(target[s])) for s in symbols)
    if drift > DRIFT_BAND:
        return target, f'rebalance: max drift {drift:.4f} > band {DRIFT_BAND}'
    return None, f'skip: max drift {drift:.4f} within band {DRIFT_BAND}'


# ---------------------------------------------------------------------------
# Reading the platform's portfolio (its exact layout is not documented)
# ---------------------------------------------------------------------------
def _number(value):
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _first(record, keys):
    for key in keys:
        if isinstance(record, dict) and key in record:
            number = _number(record[key])
            if number is not None:
                return number
    return None


def _walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _positions(portfolio, symbols):
    """Find per-stock records: a dict keyed by symbol, or a list of records with a symbol field."""
    wanted = set(symbols)
    for node in _walk(portfolio):
        if isinstance(node, dict) and set(node) & wanted and set(node) <= wanted | {'CASH', 'cash'}:
            return {s: (r if isinstance(r, dict) else {'shares': r}) for s, r in node.items() if s in wanted}
        if isinstance(node, list) and node and all(isinstance(r, dict) for r in node):
            out = {}
            for r in node:
                symbol = r.get('symbol') or r.get('ticker')
                if symbol in wanted:
                    out[symbol] = r
            if out:
                return out
    return None


def latest_prices(symbols):
    """Fallback only: last traded prices from Yahoo Finance's public chart endpoint."""
    import httpx
    prices = {}
    with httpx.Client(timeout=10, headers={'User-Agent': 'Mozilla/5.0'}) as client:
        for symbol in symbols:
            response = client.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}',
                                  params={'range': '1d', 'interval': '1m'})
            response.raise_for_status()
            prices[symbol] = float(response.json()['chart']['result'][0]['meta']['regularMarketPrice'])
    return prices


def read_portfolio(portfolio, symbols):
    """Return {'invested': bool|None, 'weights': {symbol: weight}|None, 'nav': float|None, 'source': str}."""
    nav = cash = None
    for node in _walk(portfolio):
        if isinstance(node, dict):
            nav = nav if nav is not None else _first(node, NAV_KEYS)
            cash = cash if cash is not None else _first(node, CASH_KEYS)
    positions = _positions(portfolio, symbols)

    if positions:
        weights = {s: _first(r, WEIGHT_KEYS) for s, r in positions.items()}
        if all(w is not None for w in weights.values()):
            return {'invested': any(w > 0 for w in weights.values()), 'weights': weights, 'nav': nav, 'source': 'platform weights'}
        values = {s: _first(r, VALUE_KEYS) for s, r in positions.items()}
        shares = {s: _first(r, SHARE_KEYS) for s, r in positions.items()}
        if all(v is not None for v in values.values()):
            total = nav or (sum(values.values()) + (cash or 0))
            return {'invested': any(v > 0 for v in values.values()),
                    'weights': {s: v / total for s, v in values.items()}, 'nav': total, 'source': 'platform values'}
        if all(q is not None for q in shares.values()):
            invested = any(q > 0 for q in shares.values())
            if not invested:
                return {'invested': False, 'weights': None, 'nav': nav, 'source': 'platform shares'}
            try:
                prices = latest_prices([s for s, q in shares.items() if q > 0])
                held = {s: shares[s] * prices[s] for s in prices}
                total = sum(held.values()) + (cash or 0)
                return {'invested': True, 'weights': {s: v / total for s, v in held.items()}, 'nav': total,
                        'source': 'platform shares x Yahoo prices'}
            except Exception:
                return {'invested': True, 'weights': None, 'nav': nav, 'source': 'price fetch failed'}

    if cash is not None and nav is not None:
        if cash >= 0.999 * nav:
            return {'invested': False, 'weights': None, 'nav': nav, 'source': 'cash equals NAV'}
        return {'invested': True, 'weights': None, 'nav': nav, 'source': 'positions not readable'}
    return {'invested': None, 'weights': None, 'nav': nav, 'source': 'portfolio not readable'}


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------
def log(entry):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {'logged_at': datetime.now(timezone.utc).isoformat(), **entry}
    with LOG_FILE.open('a', encoding='utf-8') as stream:
        stream.write(dumps_json(entry) + '\n')
    print(dumps_json(entry))


def open_round(schedule, now):
    for row in schedule['rounds']:
        if row['phase'] == PHASE and row['status'] != 'CANCELLED':
            opens = datetime.fromisoformat(row['opens_at'])
            deadline = datetime.fromisoformat(row['deadline'])
            if opens <= now < deadline:
                return row, deadline
    return None, None


def run(args, session_factory=None):
    load_environment()
    if session_factory is None:
        def session_factory():
            return OriginalSession(profile=load_profile(os.environ['ICAIF_PROFILE']),
                                   token=os.environ['CODABENCH_TOKEN'],
                                   checkpoint='.icaif/checkpoint.json', credentials='.icaif/credentials.json')
    with session_factory() as session:
        schedule = session.schedule()
        now = session._now(schedule)
        row, deadline = open_round(schedule, now)
        if row is None:
            print('No official round is open for uploads right now.')
            return 0
        minutes_left = (deadline - now) / timedelta(minutes=1)
        if minutes_left < MIN_MINUTES_LEFT:
            print(f'{row["id"]}: under {MIN_MINUTES_LEFT} min to the deadline; not starting an upload.')
            return 0
        if minutes_left > ACT_WINDOW_MINUTES and not args.now:
            print(f'{row["id"]}: {minutes_left:.0f} min to the deadline; waiting until the last {ACT_WINDOW_MINUTES} min.')
            return 0
        key = f'decision:{PHASE}:{row["id"]}'
        if key in session.state['operations'] or session._occupied(session.round(row['id'])):
            print(f'{row["id"]}: already has an upload; nothing to do.')
            return 0

        symbols = schedule['symbols']
        state = read_portfolio(session.portfolio(PHASE), symbols)
        weights, reason = decide(symbols, state)
        entry = {'round_id': row['id'], 'deadline': row['deadline'], 'as_of': now.isoformat(),
                 'portfolio_source': state['source'], 'nav': state['nav'],
                 'current_weights': state['weights'], 'decision': reason,
                 'target_weights': weights, 'dry_run': bool(args.dry_run)}
        if weights is None:
            log({**entry, 'result': 'NO_UPLOAD'})
            return 0

        creds = session._team()
        payload = {'submission_type': 'decision', 'team_id': creds['team_id'], 'team_token': creds['team_token'],
                   'phase': PHASE, 'round_id': row['id'], 'weights': {s: weights[s] for s in symbols}}
        raw = dumps_json(payload).encode('utf-8')
        session._validated(raw, payload, 'decision.json', schedule)   # the kit's own checks, before any upload
        if args.dry_run:
            log({**entry, 'result': 'DRY_RUN_VALID'})
            return 0
        path = Path('private') / 'rounds' / row['id'] / 'decision.json'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        result = session.decision(str(path))
        log({**entry, 'result': result.get('status', 'SUBMITTED'), 'receipt': result})
        return 0


def main():
    parser = argparse.ArgumentParser(description='Run one ICAIF round for the drift-band equal-weight agent.')
    parser.add_argument('--dry-run', action='store_true', help='decide and validate, but never upload')
    parser.add_argument('--now', action='store_true', help='act immediately instead of waiting for the act window')
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as error:   # never print tokens: the client's own errors are already redacted
        log({'result': 'ERROR', 'error_type': type(error).__name__,
             'error': str(error) if type(error).__name__ in ('AutomationError', 'AmbiguousSubmission') else 'see type'})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
