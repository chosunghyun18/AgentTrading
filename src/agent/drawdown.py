"""Account equity protection. One timed process lock covers persistence and entry transport.

Dedicated UNIFIED account, no cash flows. Samples cannot recover unobserved peaks.
The shared store lock deliberately serializes all accounts in this installation.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from src.agent import risk

ASSUMPTION = 'Dedicated account without deposits/withdrawals; pause and explicitly rebase after cash flow. Sampled protection requires a running monitor.'
MAX_REQUEST_SECONDS = 10
STALE_SECONDS = 30
LOCK_SECONDS = 5


class DrawdownError(RuntimeError):
    """Safe public protection error; never contains raw transport details."""


def number(value):
    if isinstance(value, bool):
        raise ValueError('invalid numeric value')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite numeric value')
    return result


def fraction(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < number(value) < 1:
        raise ValueError('limit_fraction must satisfy 0 < limit < 1')
    return float(value)


def protective(order):
    return any(order.get(k) in (True, 'true', 'True', 1) for k in ('reduceOnly', 'closeOnTrigger'))


class Drawdown:
    def __init__(self, client, mode, state_dir):
        self.client, self.mode, self.root = client, mode, Path(state_dir)
        self.path = self.root / 'drawdown.json'
        self.identity = None

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'drawdown.lock').open('a') as handle:
            deadline = time.monotonic() + LOCK_SECONDS
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise DrawdownError('drawdown lock timeout') from None
                    time.sleep(.02)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self):
        if not self.path.exists():
            return {'version': 1, 'limit_fraction': .3, 'accounts': {}, 'bindings': {}, 'faults': {}}
        try:
            data = json.loads(self.path.read_text())
            if data['version'] != 1 or not isinstance(data['accounts'], dict) or not isinstance(data['bindings'], dict):
                raise ValueError()
            fraction(data['limit_fraction'])
            if not isinstance(data.get('faults', {}), dict):
                raise ValueError()
            for key, state in data['accounts'].items():
                self.validate(state)
                if self.key(state['account_identity']) != key:
                    raise ValueError()
            return data
        except Exception:
            raise DrawdownError('invalid drawdown state/policy') from None

    @staticmethod
    def validate(s):
        identity = s['account_identity']
        if not all(identity.get(k) for k in ('exchange', 'mode', 'uid', 'account_type')):
            raise ValueError('invalid identity')
        peak, equity = number(s['peak_equity']), number(s['equity'])
        dd, maximum = number(s['drawdown']), number(s['max_drawdown'])
        expected = max(0., (peak - equity) / peak) if peak > 0 else -1
        if peak <= 0 or equity > peak or dd != expected or maximum < dd or maximum < 0:
            raise ValueError('impossible equity state')
        if number(s['observed_at']) > time.time() or number(s['observation_gap_seconds']) < 0:
            raise ValueError('clock rollback')
        if not isinstance(s['latched'], bool) or not s['epoch']:
            raise ValueError('invalid latch/epoch')
        for name in ('history', 'cancellation_errors', 'pending'):
            if not isinstance(s[name], list):
                raise ValueError('invalid state collection')
        for old in s['history']:
            Drawdown.validate(old)

    def save(self, data):
        # Temp and destination on the same filesystem. fsync data and directory.
        fd, temp = tempfile.mkstemp(prefix='.drawdown-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(data, out, allow_nan=False, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def identify(self):
        started = time.monotonic()
        info = self.client.api_key_info()
        uid = info.get('userID')
        if (time.monotonic() - started > MAX_REQUEST_SECONDS or not isinstance(uid, (int, str))
                or isinstance(uid, bool) or uid in (None, '', 0, '0')):
            raise DrawdownError('account UID unavailable or identity request timed out')
        self.identity = {'exchange': self.client.base_url.rstrip('/'), 'mode': self.mode,
                         'uid': str(uid), 'account_type': 'UNIFIED'}
        return self.key(self.identity)

    @staticmethod
    def key(identity):
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def sample(self):
        wall, mono = time.time(), time.monotonic()
        wallet = self.client.wallet()
        now = time.time()
        if time.monotonic() - mono > MAX_REQUEST_SECONDS or now < wall or now - wall > MAX_REQUEST_SECONDS:
            raise DrawdownError('wallet request exceeded 10s or clock rollback')
        if wallet.get('accountType', 'UNIFIED') != 'UNIFIED':
            raise DrawdownError('wallet account type mismatch')
        return number(wallet['totalEquity']), now

    def current(self, data, key=None):
        key = key or (self.key(self.identity) if self.identity else data['bindings'].get(self.mode))
        return data['accounts'].get(key)

    def status(self, data=None, state=None, error=''):
        try:
            data = data if data is not None else self.load()
            state = state if state is not None else self.current(data)
        except Exception as exc:
            data, state, error = {'limit_fraction': None}, None, str(exc)
        error = error or data.get('faults', {}).get(self.mode, '')
        out = {k: None for k in ('equity', 'peak_equity', 'drawdown', 'max_drawdown', 'observed_at', 'epoch', 'account_identity')}
        out.update(limit_fraction=data['limit_fraction'], enrolled=bool(state), observation_gap_seconds=None,
                   stale=True, latched=False, blocked=True, reason=error or 'enrollment required',
                   cancellation_errors=[], pending_orders=[], assumption=ASSUMPTION, history=[], manual_halt=risk.is_halted(self.root))
        if state:
            out.update({k: state[k] for k in out if k in state})
            out['pending_orders'] = [dict(p) for p in state['pending']]
            age = time.time() - state['observed_at']
            out['observation_gap_seconds'] = max(age, state['observation_gap_seconds'])
            out['stale'] = age < 0 or age > STALE_SECONDS
            out['reason'] = error or ('clock rollback' if age < 0 else 'stale observation' if out['stale'] else
                'drawdown latched' if state['latched'] else 'unresolved order' if state['pending'] else
                'manual HALT' if out['manual_halt'] else '')
            out['blocked'] = bool(out['reason'])
        return out

    def local_status(self):
        try:
            with self.locked():
                return self.status()
        except Exception as exc:
            return self.status(data={'limit_fraction': None, 'accounts': {}, 'bindings': {}}, error=str(exc))

    def set_limit(self, limit: float) -> None:
        limit = fraction(limit)
        with self.locked():
            data = self.load()
            data['limit_fraction'] = limit
            affected = []
            for key, state in data['accounts'].items():
                if (state['equity'] <= 0 or state['drawdown'] >= limit
                        or math.isclose(state['drawdown'], limit, abs_tol=1e-12, rel_tol=0)):
                    if not state['latched']:
                        state.setdefault('breaches', []).append({'observed_at': state['observed_at'],
                            'drawdown': state['drawdown'], 'limit_fraction': limit})
                        affected.append(key)
                    state['latched'] = True
            self.save(data)
            for key in affected:
                state = data['accounts'][key]
                risk.audit(self.root, 'drawdown_policy_latched', mode=state['account_identity']['mode'],
                           epoch=state['epoch'], drawdown=state['drawdown'], limit_fraction=limit)
        risk.audit(self.root, 'drawdown_limit_saved', mode=self.mode, limit_fraction=limit)

    def observe(self, data, key):
        state = self.current(data, key)
        if not state:
            raise DrawdownError('drawdown enrollment required')
        equity, now = self.sample()
        self.record_observation(data, state, equity, now)
        if state['latched']:
            self.cancel_entries(data, state)
        return state

    def record_observation(self, data, s, equity, now):
        if now < s['observed_at']:
            raise DrawdownError('observation clock rollback')
        s['observation_gap_seconds'] = now - s['observed_at']
        s.update(equity=equity, observed_at=now, peak_equity=max(s['peak_equity'], equity))
        s['drawdown'] = max(0., (s['peak_equity'] - equity) / s['peak_equity'])
        s['max_drawdown'] = max(s['max_drawdown'], s['drawdown'])
        breached = (equity <= 0 or s['drawdown'] >= data['limit_fraction']
                    or math.isclose(s['drawdown'], data['limit_fraction'], abs_tol=1e-12, rel_tol=0))
        newly_breached = breached and not s['latched']
        s['latched'] = s['latched'] or breached
        if newly_breached:
            s.setdefault('breaches', []).append({'observed_at': now, 'drawdown': s['drawdown'],
                                               'limit_fraction': data['limit_fraction']})
        data.setdefault('faults', {}).pop(self.mode, None)
        self.save(data)  # persist latch BEFORE any cancellation
        if newly_breached:
            risk.audit(self.root, 'drawdown_breached', mode=self.mode, epoch=s['epoch'], drawdown=s['drawdown'])

    def cancel_entries(self, data, s):
        errors = []
        try:
            for o in self.client.all_open_orders():
                if not protective(o) and str(o.get('orderLinkId', '')).startswith(('atc-', 'atg-')):
                    try:
                        self.client.cancel_account_order(o)
                        errors.append('cancellation pending verification: ' + str(o.get('orderId', 'unknown')))
                    except Exception:
                        errors.append('entry cancellation failed: ' + str(o.get('orderId', 'unknown')))
            remaining = {str(o.get('orderId')) for o in self.client.all_open_orders()}
            errors = [e for e in errors if not e.startswith('cancellation pending verification: ') or e.split(': ', 1)[1] in remaining]
        except Exception:
            errors.append('account order query failed')
        s['cancellation_errors'] = errors
        self.save(data)

    @staticmethod
    def error_text(exc):
        return str(exc) if isinstance(exc, DrawdownError) else type(exc).__name__

    def poll(self):
        try:
            with self.locked():
                data = self.load()
                try:
                    s = self.observe(data, self.identify())
                    return self.status(data, s)
                except Exception as exc:
                    error = 'drawdown observation failed: ' + self.error_text(exc)
                    data.setdefault('faults', {})[self.mode] = error
                    self.save(data)
                    risk.audit(self.root, 'drawdown_observation_failed', mode=self.mode, reason=error)
                    return self.status(data, error=error)
        except Exception as exc:
            return self.status(error='drawdown observation failed: ' + self.error_text(exc))

    def known_submission(self, pending: dict, *, terminal_only: bool = False) -> bool:
        try:
            started = time.monotonic()
            rows = self.client.order_by_link(pending['symbol'], pending['orderLinkId'])
            if time.monotonic() - started > MAX_REQUEST_SECONDS:
                return False
            statuses = {'New', 'PartiallyFilled', 'Filled', 'Cancelled', 'Rejected', 'Deactivated',
                        'Untriggered', 'Triggered', 'PartiallyFilledCanceled'}
            if terminal_only:
                statuses = {'Filled', 'Cancelled', 'Rejected', 'Deactivated', 'PartiallyFilledCanceled'}
            return any(isinstance(o, dict) and o.get('symbol') == pending['symbol']
                       and o.get('orderLinkId') == pending['orderLinkId']
                       and o.get('orderStatus') in statuses for o in rows)
        except Exception:
            return False

    def reconcile(self, order_link_id: str, confirm: bool = False) -> dict:
        """Positive recorded-symbol/link evidence only; reconciliation never resumes entries."""
        if not order_link_id or not isinstance(order_link_id, str):
            raise DrawdownError('recorded order link required')
        with self.locked():
            data = self.load()
            state = self.current(data, self.identify())
            if not state:
                raise DrawdownError('drawdown enrollment required')
            matches = [p for p in state['pending'] if p['orderLinkId'] == order_link_id]
            if len(matches) != 1:
                raise DrawdownError('unique pending order link required for this account')
            known = self.known_submission(matches[0])
            if confirm is True:
                if not known:
                    raise DrawdownError('positive matching symbol and order link evidence required')
                state['pending'].remove(matches[0])
                state['latched'] = True
                self.save(data)
                risk.audit(self.root, 'drawdown_order_reconciled', mode=self.mode, epoch=state['epoch'],
                           symbol=matches[0]['symbol'], orderLinkId=order_link_id)
            out = self.status(data, state)
            out.update(reconciliation_known=known, reconciled=confirm is True)
            return out

    def uncertain(self, data: dict, state: dict) -> None:
        state['latched'] = True
        self.save(data)

    def legacy_unknowns(self):
        path = risk.audit_path(self.root)
        if not path.exists():
            return []
        unresolved = {}
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get('mode', self.mode) != self.mode:
                continue
            params = record.get('params', {})
            if record.get('event') in ('order_unknown', 'live_order_unknown'):
                link = params.get('orderLinkId')
                if not link or not params.get('symbol'):
                    raise DrawdownError('unscoped legacy unknown submission requires reconciliation')
                unresolved[link] = {'orderLinkId': link, 'symbol': params['symbol']}
        return [p for p in unresolved.values() if not self.known_submission(p, terminal_only=True)]

    def flat(self, state: dict | None) -> None:
        started = time.monotonic()
        positions = self.client.all_positions()
        orders = self.client.all_open_orders()
        if any(number(p['size']) != 0 for p in positions) or any(not protective(o) for o in orders):
            raise DrawdownError('account must be flat with no pending entries')
        pending = state['pending'] if state else self.legacy_unknowns()
        if any(not self.known_submission(p, terminal_only=True) for p in pending):
            raise DrawdownError('unresolved order submissions require reconciliation')
        if time.monotonic() - started > MAX_REQUEST_SECONDS:
            raise DrawdownError('account checks timed out')

    def enroll(self, confirm: bool = False, *, rebase: bool = False, reason: str = '') -> dict:
        if confirm is not True or (rebase and not reason.strip()):
            raise DrawdownError('explicit confirmation and rebase reason required')
        with self.locked():
            data = self.load()
            key = self.identify()
            old = self.current(data, key)
            if bool(old) != rebase:
                raise DrawdownError('already enrolled' if old else 'enrollment required')
            self.flat(old)
            equity, now = self.sample()
            if old:
                # The fresh rebase sample also belongs to the ending epoch.
                self.record_observation(data, old, equity, now)
            if equity <= 0:
                raise DrawdownError('initial equity must be positive')
            s = dict(account_identity=self.identity, equity=equity, peak_equity=equity, drawdown=0., max_drawdown=0.,
                     observed_at=now, observation_gap_seconds=0., epoch=uuid.uuid4().hex, latched=rebase,
                     cancellation_errors=[], pending=[], history=[], rebase_reason=reason, breaches=[])
            if old:
                s['history'] = [*old['history'], {**old, 'history': []}]
            data.setdefault('faults', {}).pop(self.mode, None)
            data['accounts'][key] = s
            data['bindings'][self.mode] = key
            self.save(data)
            risk.audit(self.root, 'drawdown_rebased' if rebase else 'drawdown_initialized', mode=self.mode,
                       epoch=s['epoch'], reason=reason)
            return self.status(data, s)

    def resume(self, confirm: bool = False) -> dict:
        if confirm is not True:
            raise DrawdownError('explicit confirmation required')
        with self.locked():
            data = self.load()
            s = self.observe(data, self.identify())
            age = time.time() - s['observed_at']
            if (s['equity'] <= 0 or s['drawdown'] >= data['limit_fraction'] or s['pending']
                    or s['cancellation_errors'] or not 0 <= age <= MAX_REQUEST_SECONDS):
                raise DrawdownError('drawdown recovery below limit and resolved orders required')
            s['latched'] = False
            self.save(data)
            risk.audit(self.root, 'drawdown_resumed', mode=self.mode, epoch=s['epoch'])
            return self.status(data, s)

    @contextmanager
    def entry_guard(self):
        with self.locked():
            data = self.load()
            s = self.observe(data, self.identify())
            status = self.status(data, s)
            if status['blocked']:
                raise DrawdownError(status['reason'])
            yield data, s

    def require_send_ready(self, data, state, symbol, link):
        own = {"symbol": symbol, "orderLinkId": link}
        status = self.status(data, {**state, "pending": [p for p in state["pending"] if p != own]})
        age = time.time() - state['observed_at']
        if status['blocked'] or age < 0 or age > MAX_REQUEST_SECONDS:
            raise DrawdownError('final fresh drawdown observation required: ' + status['reason'])

    def pending(self, data, s, symbol, link, active=True):
        record = {'symbol': symbol, 'orderLinkId': link}
        if active:
            s['pending'].append(record)
        else:
            s['pending'] = [p for p in s['pending'] if p != record]
        self.save(data)
