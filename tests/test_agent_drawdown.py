"""Safety contracts: all exchange operations use explicit in-memory fakes."""
import json
import pytest
from src.agent import risk
from src.agent.service import TradingService, RiskRejected
from tests.agent_fakes import FakeClient
from tests.test_agent_service import REQ, LIM

@pytest.fixture
def svc(tmp_path):
    s = TradingService(FakeClient(), 'demo', tmp_path/'agent', tmp_path/'risk.yaml')
    s.save_limits(LIM)
    return s

class TestPolicyContract:
    def test_default_and_persisted_fraction(self, svc):
        assert svc.drawdown_status()['limit_fraction'] == .30
        svc.save_drawdown_limit(.8)
        assert svc.drawdown_status()['limit_fraction'] == .8
        for value in (0, 1, -1, float('nan'), float('inf'), '80', True):
            with pytest.raises((ValueError, TypeError)):
                svc.save_drawdown_limit(value)


    def test_restart_risklimits_compatibility_and_corrupt_policy(self, svc):
        original = svc.limits_path.read_bytes()
        svc.save_drawdown_limit(.8)
        fresh = TradingService(svc.client, 'demo', svc.state_dir, svc.limits_path)
        assert fresh.drawdown_status()['limit_fraction'] == .8
        assert svc.limits_path.read_bytes() == original
        data = json.loads(svc._drawdown.path.read_text())
        data['limit_fraction'] = 'not a fraction'
        svc._drawdown.path.write_text(json.dumps(data))
        assert fresh.drawdown_status()['blocked']
        assert fresh.drawdown_status()['limit_fraction'] is None
        with pytest.raises(RuntimeError):
            fresh.save_drawdown_limit(.5)
        with pytest.raises(RiskRejected):
            fresh.place_order(REQ)

class TestEnrollmentContract:
    def test_explicit_enrollment_and_local_status(self, svc):
        with pytest.raises(RiskRejected):
            svc.place_order(REQ)
        with pytest.raises(RuntimeError):
            svc.initialize_drawdown()
        assert svc.initialize_drawdown(confirm=True)['enrolled']
        svc.client.fail['wallet'] = AssertionError('local status called wallet')
        assert not svc.drawdown_status()['blocked']
        json.dumps(svc.drawdown_status(), allow_nan=False)

    def test_missing_uid_and_account_wide_flat(self, svc):
        svc.client.uid = None
        with pytest.raises(RuntimeError):
            svc.initialize_drawdown(confirm=True)
        svc.client.uid = 'fake-uid'
        svc.client.pos['size'] = '1'
        with pytest.raises(RuntimeError):
            svc.initialize_drawdown(confirm=True)


    def test_identity_scope_restart_rotation_and_symbol(self, svc):
        svc.initialize_drawdown(confirm=True)
        svc.client.equity = 2000
        svc.poll_drawdown()
        other = TradingService(svc.client, 'demo', svc.state_dir, svc.limits_path, 'ETHUSDT')
        assert other.poll_drawdown()['peak_equity'] == 2000
        svc.client._key = 'rotated-secret-key'
        assert svc.poll_drawdown()['peak_equity'] == 2000
        svc.client.uid = 'other-user'
        assert svc.poll_drawdown()['blocked']
        assert svc.initialize_drawdown(confirm=True)['peak_equity'] == 2000
        svc.client.uid = 'fake-uid'
        assert svc.poll_drawdown()['peak_equity'] == 2000
        live = TradingService(svc.client, 'live', svc.state_dir, svc.limits_path)
        assert live.poll_drawdown()['blocked']
        assert 'rotated-secret-key' not in svc._drawdown.path.read_text()


    def test_two_processes_never_overwrite_peak_with_later_lower_sample(self, svc):
        import subprocess
        import sys
        svc.initialize_drawdown(confirm=True)
        script = '''
import sys, time
from pathlib import Path
from src.agent.service import TradingService
from tests.agent_fakes import FakeClient
client = FakeClient()
client.equity = float(sys.argv[2])
s = TradingService(client, 'demo', Path(sys.argv[1]), Path(sys.argv[1])/'risk.yaml')
time.sleep(float(sys.argv[3]))
assert s.poll_drawdown()['enrolled']
'''
        commands = [(3000, 0), (2000, .02)]
        processes = [subprocess.Popen([sys.executable, '-c', script, str(svc.state_dir), str(eq), str(delay)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for eq, delay in commands]
        for process in processes:
            out, err = process.communicate(timeout=10)
            assert process.returncode == 0, (out, err)
        assert svc.drawdown_status()['peak_equity'] == 3000


    def test_bybit_account_queries_use_all_categories_and_paginate(self):
        from src.agent.bybit import Bybit
        client = Bybit('https://fake')
        calls = []
        def request(method, path, params, auth):
            calls.append((path, dict(params)))
            page = [{'size': '0', 'symbol': 'OTHER'}]
            return {'list': page, 'nextPageCursor': '' if params.get('cursor') else 'page2'}
        client._request = request
        assert len(client.all_positions()) == 8
        assert len(client.all_open_orders()) == 10
        assert all(p.get('settleCoin') in ('USDT', 'USDC') for _, p in calls if p['category'] == 'linear')
        assert {p['category'] for path, p in calls if path == '/v5/order/realtime'} == {'linear', 'inverse', 'option', 'spot'}
        assert not any('symbol' in params for _, params in calls)


    def test_initial_zero_negative_and_pending_account_checks(self, svc):
        for equity in (0, -1):
            svc.client.equity = equity
            with pytest.raises(RuntimeError):
                svc.initialize_drawdown(confirm=True)
        svc.client.equity = 100
        svc.client.orders = [dict(symbol='OTHER', orderId='human', orderLinkId='manual')]
        with pytest.raises(RuntimeError):
            svc.initialize_drawdown(confirm=True)
        svc.client.orders = []
        svc.initialize_drawdown(confirm=True)
        with pytest.raises(RuntimeError):
            svc.resume_drawdown()
        svc.client.equity = -10
        assert svc.poll_drawdown()['latched']


    def test_legacy_unknown_prevents_first_enrollment(self, svc):
        risk.audit(svc.state_dir, 'order_unknown', mode='demo', params={'symbol': 'BTCUSDT', 'orderLinkId': 'atc-old'})
        with pytest.raises(RuntimeError):
            svc.initialize_drawdown(confirm=True)


class TestEnforcementContract:
    def test_boundary_latch_safe_cancel_and_rebase_history(self, svc):
        svc.client.equity = 100
        svc.save_drawdown_limit(.8)
        svc.initialize_drawdown(confirm=True)
        svc.client.orders = [dict(orderId=str(i), symbol='ETHUSDT', orderLinkId=link, **flags)
            for i, (link, flags) in enumerate([('atc-entry', {}), ('atg-protect', {'reduceOnly': True}),
                ('atc-trigger', {'closeOnTrigger': True}), ('human', {})])]
        svc.client.equity = 20
        out = svc.poll_drawdown()
        assert out['latched'] and out['drawdown'] == pytest.approx(.8)
        assert [x['orderId'] for x in svc.client.orders] == ['1', '2', '3']
        assert not svc.halted()
        with pytest.raises(RiskRejected):
            svc.place_order(REQ)
        svc.save_drawdown_limit(.9)
        assert svc.drawdown_status()['latched']
        svc.client.orders = []
        with pytest.raises(RuntimeError):
            svc.reset_drawdown_baseline(confirm=True)
        old = out['epoch']
        out = svc.reset_drawdown_baseline(confirm=True, reason='cash flow reconciled')
        assert out['latched'] and out['epoch'] != old
        assert out['history'][0]['max_drawdown'] == pytest.approx(.8)
        risk.set_halt(svc.state_dir, 'manual')
        assert svc.resume_drawdown(confirm=True)['manual_halt']
        assert svc.halted()

    def test_zero_latches_and_existing_resume_cannot_bypass(self, svc):
        svc.initialize_drawdown(confirm=True)
        svc.client.equity = 0
        assert svc.poll_drawdown()['latched']
        with pytest.raises(RuntimeError):
            svc.resume()
        assert svc.drawdown_status()['latched']


    @pytest.mark.parametrize('equity', ['NaN', 'Infinity', None, '', 'broken'])
    def test_bad_wallet_blocks_transport(self, svc, equity):
        svc.initialize_drawdown(confirm=True)
        svc.client.equity = equity
        with pytest.raises((RiskRejected, ValueError)):
            svc.place_order(REQ)
        assert not any(name == 'create_order' for name, _ in svc.client.calls)
        assert svc.poll_drawdown()['blocked']


    def test_stale_clock_and_corrupt_state(self, svc, monkeypatch):
        from src.agent import drawdown
        svc.initialize_drawdown(confirm=True)
        now = drawdown.time.time()
        monkeypatch.setattr(drawdown.time, 'time', lambda: now + 31)
        assert svc.drawdown_status()['stale']
        monkeypatch.setattr(drawdown.time, 'time', lambda: now - 1)
        assert svc.drawdown_status()['blocked']
        with pytest.raises(RiskRejected):
            svc.place_order(REQ)
        svc._drawdown.path.write_text('{"limit_fraction": .8}')
        assert svc.drawdown_status()['blocked']
        with pytest.raises(RuntimeError):
            svc.initialize_drawdown(confirm=True)


    def test_failed_write_or_lock_blocks_both_entry_paths(self, svc, monkeypatch):
        from src.agent.live.router import OrderRouter, Entry
        from src.agent.live.state_cache import StateCache
        from tests.test_agent_live import _snap
        svc.initialize_drawdown(confirm=True)
        cache = StateCache('BTCUSDT')
        cache.set_connected(True)
        cache.load_snapshot(_snap(pos=None, closed=0.))
        router = OrderRouter(svc.client, cache, mode='demo', state_dir=svc.state_dir,
                             limits_path=svc.limits_path, symbol='BTCUSDT', live_unlocked=lambda: False, leverage=2)
        router.start()
        def fail(*args):
            raise OSError('disk failure')
        monkeypatch.setattr(type(svc._drawdown), 'save', fail)
        with pytest.raises(RiskRejected):
            svc.place_order(REQ)
        with pytest.raises(RiskRejected):
            router.submit(Entry('Buy', .01, 'market', 79600), 'write-failed', 80000)
        assert not any(n == 'create_order' for n, _ in svc.client.calls)


    def test_cancel_failure_keeps_latch_and_protection(self, svc):
        svc.initialize_drawdown(confirm=True)
        svc.client.orders = [dict(symbol='BTCUSDT', orderId='entry', orderLinkId='atg-new')]
        svc.client.fail['cancel_order'] = TimeoutError()
        svc.client.equity = 100
        status = svc.poll_drawdown()
        assert status['latched'] and status['cancellation_errors']
        svc.client.equity = 1000
        assert svc.poll_drawdown()['latched']
        assert not svc.halted()
        with pytest.raises(RuntimeError):
            svc.resume_drawdown(confirm=True)
        assert not any(n in ('create_order', 'cancel_all') for n, _ in svc.client.calls)


    def test_unknown_submission_survives_restart_blocks_rebase(self, svc):
        from src.agent.service import OrderUnknown
        svc.initialize_drawdown(confirm=True)
        svc.client.fail['create_order'] = TimeoutError('timeout')
        with pytest.raises(OrderUnknown):
            svc.place_order(REQ)
        assert svc.drawdown_status()['blocked']
        restarted = TradingService(svc.client, 'demo', svc.state_dir, svc.limits_path)
        with pytest.raises(RuntimeError):
            restarted.reset_drawdown_baseline(confirm=True, reason='must refuse unknown')
        with pytest.raises(RiskRejected):
            restarted.place_order(REQ)


    def test_actual_lock_timeout_and_direct_router_unenrolled(self, svc, monkeypatch):
        import fcntl
        from src.agent import drawdown
        from src.agent.live.router import OrderRouter, Entry
        from src.agent.live.state_cache import StateCache
        from tests.test_agent_live import _snap
        cache = StateCache('BTCUSDT')
        cache.set_connected(True)
        cache.load_snapshot(_snap(pos=None, closed=0.))
        router = OrderRouter(svc.client, cache, mode='demo', state_dir=svc.state_dir,
                             limits_path=svc.limits_path, symbol='BTCUSDT', live_unlocked=lambda: False, leverage=2)
        router.start()
        with pytest.raises(RiskRejected):
            router.submit(Entry('Buy', .01, 'market', 79600), 'unenrolled', 80000)
        svc.initialize_drawdown(confirm=True)
        monkeypatch.setattr(drawdown, 'LOCK_SECONDS', .03)
        with (svc.state_dir/'drawdown.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(RiskRejected, match='timeout'):
                router.submit(Entry('Buy', .01, 'market', 79600), 'locked', 80000)
        assert not any(n == 'create_order' for n, _ in svc.client.calls)


    def test_request_latency_and_latch_before_cancel(self, svc, monkeypatch):
        from src.agent import drawdown
        svc.initialize_drawdown(confirm=True)
        original = svc.client.wallet
        mono = drawdown.time.monotonic
        offset = [0.]
        def slow():
            result = original()
            offset[0] += 11
            return result
        monkeypatch.setattr(drawdown.time, 'monotonic', lambda: mono()+offset[0])
        monkeypatch.setattr(svc.client, 'wallet', slow)
        assert svc.poll_drawdown()['blocked']
        monkeypatch.setattr(svc.client, 'wallet', original)
        svc.client.equity = 0
        svc.client.orders = [dict(orderId='x', symbol='BTCUSDT', orderLinkId='atc-x')]
        cancel = svc.client.cancel_account_order
        def inspect(order):
            assert json.loads(svc._drawdown.path.read_text())['accounts'][svc._drawdown.key(svc._drawdown.identity)]['latched']
            return cancel(order)
        monkeypatch.setattr(svc.client, 'cancel_account_order', inspect)
        assert svc.poll_drawdown()['latched']


    def test_transport_holds_same_interprocess_lock(self, svc, monkeypatch):
        import fcntl
        svc.initialize_drawdown(confirm=True)
        send = svc.client.create_order
        def inspect(**params):
            with (svc.state_dir/'drawdown.lock').open('a') as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return send(**params)
        monkeypatch.setattr(svc.client, 'create_order', inspect)
        svc.place_order(REQ)


    def test_final_wallet_after_leverage_blocks_service_and_router_boundary(self, svc, monkeypatch):
        from src.agent.live.router import OrderRouter, Entry
        from src.agent.live.state_cache import StateCache
        from tests.test_agent_live import _snap
        svc.client.equity = 100
        svc.save_drawdown_limit(.8)
        svc.initialize_drawdown(confirm=True)
        original = svc.client.set_leverage
        def loss(symbol, leverage):
            original(symbol, leverage)
            svc.client.equity = 20
        monkeypatch.setattr(svc.client, 'set_leverage', loss)
        with pytest.raises(RiskRejected):
            svc.place_order(REQ)
        assert svc.drawdown_status()['latched']
        cache = StateCache('BTCUSDT')
        cache.set_connected(True)
        cache.load_snapshot(_snap(pos=None, closed=0.))
        router = OrderRouter(svc.client, cache, mode='demo', state_dir=svc.state_dir, limits_path=svc.limits_path,
                             symbol='BTCUSDT', live_unlocked=lambda: False, leverage=2)
        router.start()
        with pytest.raises(RiskRejected, match='latched'):
            router.submit(Entry('Buy', .01, 'market', 79600), 'boundary', 80000)
        assert not any(n == 'create_order' for n, _ in svc.client.calls)


    def test_poll_fault_is_visible_to_new_local_service(self, svc):
        svc.initialize_drawdown(confirm=True)
        svc.client.fail['wallet'] = TimeoutError('do not expose secret-key')
        assert svc.poll_drawdown()['blocked']
        fresh = TradingService(svc.client, 'demo', svc.state_dir, svc.limits_path)
        assert fresh.drawdown_status()['blocked']
        assert 'secret-key' not in json.dumps(fresh.drawdown_status())
        del svc.client.fail['wallet']
        assert not svc.poll_drawdown()['blocked']


    def test_slow_save_cannot_submit_using_old_wallet(self, svc, monkeypatch):
        from src.agent import drawdown
        svc.initialize_drawdown(confirm=True)
        original = svc._drawdown.save
        wall = drawdown.time.time
        offset = [0.]
        def delayed(data):
            original(data)
            if any(state['pending'] for state in data['accounts'].values()):
                offset[0] += 11
        monkeypatch.setattr(svc._drawdown, 'save', delayed)
        monkeypatch.setattr(drawdown.time, 'time', lambda: wall()+offset[0])
        with pytest.raises(RiskRejected, match='fresh'):
            svc.place_order(REQ)
        assert not any(n == 'create_order' for n, _ in svc.client.calls)


    def test_unknown_accepted_order_reconciliation_allows_rebase_only_flat(self, svc):
        from src.agent.service import OrderUnknown
        svc.initialize_drawdown(confirm=True)
        svc.client.fail['create_order'] = TimeoutError()
        with pytest.raises(OrderUnknown):
            svc.place_order(REQ)
        pending = next(iter(json.loads(svc._drawdown.path.read_text())['accounts'].values()))['pending'][0]
        svc.client.orders = [{**pending, 'orderId': 'accepted', 'orderStatus': 'New'}]
        with pytest.raises(RuntimeError):
            svc.reset_drawdown_baseline(confirm=True, reason='still pending entry')
        observed = svc.poll_drawdown()
        assert observed['blocked'] and observed['latched'] and observed['pending_orders']
        svc.client.orders = []
        # An empty open-order query is insufficient. Require a positive terminal
        # record and explicit reconciliation, which still does not resume entry.
        svc.client.order_by_link = lambda symbol, link: [{**pending, 'orderId': 'accepted',
                                                        'orderStatus': 'Cancelled'}]
        reconciled = svc.reconcile_drawdown_order(pending['orderLinkId'], confirm=True)
        assert reconciled['reconciled'] and reconciled['latched'] and not reconciled['pending_orders']
        assert svc.reset_drawdown_baseline(confirm=True, reason='confirmed resolved flat')['latched']



    def test_rebase_records_fresh_loss_in_ending_epoch_without_prior_poll(self, svc):
        svc.client.equity = 100
        svc.save_drawdown_limit(.8)
        svc.initialize_drawdown(confirm=True)
        svc.client.equity = 20
        status = svc.reset_drawdown_baseline(confirm=True, reason='reconcile cash flow')
        assert status['latched'] and status['drawdown'] == 0
        previous = status['history'][-1]
        assert previous['equity'] == 20 and previous['max_drawdown'] == .8
        assert previous['breaches'] and previous['latched']
