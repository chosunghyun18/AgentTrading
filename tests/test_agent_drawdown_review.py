"""Review regressions. Fake exchange only; exercise the actual store lock and transport."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest

from src.agent import risk, drawdown_cli
from src.agent.bybit import Bybit, BybitError
from src.agent.drawdown import DrawdownError
from src.agent.live.router import Entry, OrderRouter
from src.agent.live.state_cache import StateCache
from src.agent.service import TradingService, RiskRejected, OrderUnknown
from tests.agent_fakes import FakeClient
from tests.test_agent_live import _snap
from tests.test_agent_service import REQ


@pytest.fixture
def svc(tmp_path):
    s = TradingService(FakeClient(), 'demo', tmp_path/'agent', tmp_path/'risk.yaml')
    s.save_limits(risk.RiskLimits(4, 1500, 50, 100))
    s.initialize_drawdown(confirm=True)
    return s


def router_for(s):
    cache = StateCache('BTCUSDT')
    cache.set_connected(True)
    cache.load_snapshot(_snap(pos=None, closed=0.))
    r = OrderRouter(s.client, cache, mode='demo', state_dir=s.state_dir,
                    limits_path=s.limits_path, symbol=s.symbol, live_unlocked=lambda: False)
    r.start()
    return r


@pytest.mark.parametrize('route', ['service', 'router'])
def test_two_successful_preflights_only_one_sends_960_against_1500(svc, route, monkeypatch):
    barrier = Barrier(2)
    if route == 'service':
        original = svc.review
        def review(req):
            result = original(req)
            assert result[0] == []
            barrier.wait(timeout=3)
            return result
        monkeypatch.setattr(svc, 'review', review)
        send = lambda i: svc.place_order(REQ)
    else:
        r = router_for(svc)
        original = r.cache.context
        def context(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(timeout=3)
            return result
        monkeypatch.setattr(r.cache, 'context', context)
        send = lambda i: r.submit(Entry('Buy', .012, 'market', 79600), str(i), 80000)
    def attempt(i):
        try:
            send(i)
            return 'sent'
        except RiskRejected:
            return 'blocked'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ['blocked', 'sent']
    assert float(svc.client.pos['size']) * 80000 == 960  # stale checks previously allowed 1920


@pytest.mark.parametrize('route,change', [('service', 'daily'), ('service', 'halt'),
                                            ('router', 'daily'), ('router', 'halt'), ('router', 'cache')])
def test_final_risk_refresh_after_preflight(svc, route, change, monkeypatch):
    r = router_for(svc) if route == 'router' else None
    original = svc._drawdown.entry_guard if r is None else r._service._drawdown.entry_guard
    from contextlib import contextmanager
    @contextmanager
    def guard():
        with original() as state:
            if change == 'daily':
                svc.client.closed = [{'closedPnl': '-100'}]
            elif change == 'halt':
                risk.set_halt(svc.state_dir, 'test')
            else:
                r.cache.set_connected(False)
            yield state
    monkeypatch.setattr(svc._drawdown if r is None else r._service._drawdown, 'entry_guard', guard)
    with pytest.raises(RiskRejected):
        svc.place_order(REQ) if r is None else r.submit(Entry('Buy', .012, 'market', 79600), 'risk', 80000)
    assert not any(n == 'create_order' for n, _ in svc.client.calls)


@pytest.mark.parametrize('route', ['service', 'router'])
@pytest.mark.parametrize('code', [-2, -3, 10000, 10006, 10016, 110072, 999999])
def test_ambiguous_bybit_preserves_pending_restart_and_scrubs_audit(svc, route, code):
    svc.client.fail['create_order'] = BybitError(code, 'SECRET raw server text', '/v5/order/create')
    try:
        if route == 'service':
            svc.place_order(REQ)
        else:
            router_for(svc).submit(Entry('Buy', .012, 'market', 79600), 'unknown', 80000)
    except (BybitError, OrderUnknown):
        pass
    data = json.loads(svc._drawdown.path.read_text())
    state = next(iter(data['accounts'].values()))
    assert len(state['pending']) == 1
    restarted = TradingService(svc.client, 'demo', svc.state_dir, svc.limits_path)
    assert restarted.drawdown_status()['blocked']
    assert restarted.poll_drawdown()['blocked']
    with pytest.raises(DrawdownError):
        restarted.resume_drawdown(confirm=True)
    assert 'SECRET' not in risk.audit_path(svc.state_dir).read_text()


@pytest.mark.parametrize('route', ['service', 'router'])
def test_positive_validation_rejection_clears_pending_safely(svc, route):
    svc.client.fail['create_order'] = BybitError(10001, 'SECRET raw validation', '/v5/order/create')
    with pytest.raises(BybitError):
        svc.place_order(REQ) if route == 'service' else router_for(svc).submit(Entry('Buy', .012, 'market', 79600), 'bad', 80000)
    assert not next(iter(json.loads(svc._drawdown.path.read_text())['accounts'].values()))['pending']
    assert 'SECRET' not in risk.audit_path(svc.state_dir).read_text()


def unknown(svc):
    svc.client.fail['create_order'] = TimeoutError('SECRET transport')
    with pytest.raises(OrderUnknown) as exc:
        svc.place_order(REQ)
    svc.client.fail.clear()
    return exc.value.link_id


@pytest.mark.parametrize('evidence', ['empty', 'wrong-symbol', 'wrong-link', 'server-error'])
def test_reconcile_requires_positive_scoped_evidence(svc, evidence, monkeypatch):
    link = unknown(svc)
    rows = [] if evidence == 'empty' else [{'symbol': 'ETHUSDT' if evidence == 'wrong-symbol' else 'BTCUSDT',
        'orderLinkId': 'wrong' if evidence == 'wrong-link' else link, 'orderStatus': 'Filled'}]
    if evidence == 'server-error':
        def query(*args):
            raise BybitError(10016, 'SECRET', '/v5/order/history')
        monkeypatch.setattr(svc.client, 'order_by_link', query)
    else:
        monkeypatch.setattr(svc.client, 'order_by_link', lambda *a: rows)
    with pytest.raises(DrawdownError):
        svc.reconcile_drawdown_order(link, confirm=True)
    assert svc.poll_drawdown()['blocked']
    with pytest.raises(DrawdownError):
        svc.resume_drawdown(confirm=True)
    assert 'SECRET' not in risk.audit_path(svc.state_dir).read_text()


def test_confirmed_terminal_reconciliation_then_separate_resume_preserves_epoch(svc, monkeypatch):
    link = unknown(svc)
    before = svc.drawdown_status()
    monkeypatch.setattr(svc.client, 'order_by_link', lambda symbol, recorded_link: [
        {'symbol': symbol, 'orderLinkId': recorded_link, 'orderStatus': 'Cancelled'}])
    preview = svc.reconcile_drawdown_order(link)
    assert preview['reconciliation_known'] and not preview['reconciled']
    assert svc.drawdown_status()['blocked']
    result = svc.reconcile_drawdown_order(link, confirm=True)
    assert result['reconciled'] and result['latched'] and result['blocked']
    assert result['epoch'] == before['epoch'] and result['history'] == before['history']
    assert not result['pending_orders']
    assert not svc.resume_drawdown(confirm=True)['blocked']


def test_policy_save_latches_all_affected_accounts_without_cancel(svc):
    svc.save_drawdown_limit(.8)
    svc.client.equity = 500
    svc.poll_drawdown()
    svc.client.uid = 'second'
    svc.initialize_drawdown(confirm=True)
    svc.client.equity = 250
    svc.poll_drawdown()
    calls = list(svc.client.calls)
    svc.save_drawdown_limit(.3)
    states = json.loads(svc._drawdown.path.read_text())['accounts'].values()
    assert all(s['latched'] for s in states)
    assert svc.drawdown_status()['blocked']
    assert svc.client.calls == calls
    svc.save_drawdown_limit(.9)
    assert svc.drawdown_status()['latched']


def test_history_fallback_and_malformed_http_preserve_uncertainty():
    class Response:
        status_code = 200
        def __init__(self, data): self.data = data
        def json(self): return self.data
    class Session:
        def __init__(self, responses): self.responses = iter(responses)
        def request(self, *a, **kw): return next(self.responses)
    record = {'symbol': 'BTCUSDT', 'orderLinkId': 'link', 'orderStatus': 'Filled'}
    client = Bybit('https://fake', 'key', 'secret', session=Session([
        Response({'retCode': 0, 'result': {'list': []}}),
        Response({'retCode': 0, 'result': {'list': [record]}})]))
    assert client.order_by_link('BTCUSDT', 'link') == [record]
    for data in ([], None, {'retCode': 'nonsense'}, {'retCode': 0, 'result': ['invalid']}):
        client._session = Session([Response(data)])
        with pytest.raises(BybitError) as exc:
            client.create_order(symbol='BTCUSDT')
        assert exc.value.code == -3
    response = Response({'retCode': 10001, 'retMsg': 'validation'})
    response.status_code = 502
    client._session = Session([response])
    with pytest.raises(BybitError) as exc:
        client.create_order(symbol='BTCUSDT')
    assert exc.value.code == -2


def test_cli_reconcile_and_bounded_watch_failure(svc, monkeypatch, capsys):
    link = unknown(svc)
    monkeypatch.setattr(drawdown_cli, 'make_service', lambda mode: svc)
    assert drawdown_cli.main(['reconcile', '--order-link-id', link, '--confirm']) == 1
    svc.client.fail['wallet'] = TimeoutError('SECRET')
    assert drawdown_cli.main(['watch', '--iterations', '2', '--interval', '.001']) == 1
    assert 'SECRET' not in str(capsys.readouterr())


@pytest.mark.parametrize('route', ['service', 'router'])
@pytest.mark.parametrize('ack', [{}, {'orderId': ''}, {'orderId': 7}, {'orderId': 'id', 'orderLinkId': 'wrong'}, None])
def test_malformed_positive_ack_stays_uncertain(svc, route, ack, monkeypatch):
    monkeypatch.setattr(svc.client, 'create_order', lambda **params: ack)
    with pytest.raises((BybitError, OrderUnknown)):
        svc.place_order(REQ) if route == 'service' else router_for(svc).submit(Entry('Buy', .012, 'market', 79600), 'ack', 80000)
    restarted = TradingService(svc.client, 'demo', svc.state_dir, svc.limits_path)
    assert restarted.drawdown_status()['pending_orders']
    assert restarted.poll_drawdown()['blocked']


@pytest.mark.parametrize('route', ['service', 'router'])
def test_daily_budget_changes_at_pending_save_rechecked_before_send(svc, route, monkeypatch):
    r = router_for(svc) if route == 'router' else None
    guard = svc._drawdown if r is None else r._service._drawdown
    original = guard.pending
    def pending(data, state, symbol, link, active=True):
        original(data, state, symbol, link, active)
        if active:
            svc.client.closed = [{'closedPnl': '-100'}]
    monkeypatch.setattr(guard, 'pending', pending)
    with pytest.raises(RiskRejected):
        svc.place_order(REQ) if r is None else r.submit(Entry('Buy', .012, 'market', 79600), 'late-pnl', 80000)
    assert not any(n == 'create_order' for n, _ in svc.client.calls)


@pytest.mark.parametrize('route', ['service', 'router'])
def test_final_wallet_after_risk_context_is_persisted_before_transport(svc, route, monkeypatch):
    r = router_for(svc) if route == 'router' else None
    service = svc if r is None else r._service
    original = service.context
    calls = 0
    def context(req):
        nonlocal calls
        result = original(req)
        calls += 1
        if r is not None or calls == 2:
            svc.client.equity = 100
        return result
    monkeypatch.setattr(service, 'context', context)
    with pytest.raises(RiskRejected):
        svc.place_order(REQ) if r is None else r.submit(Entry('Buy', .012, 'market', 79600), 'late-wallet', 80000)
    assert svc.drawdown_status()['latched']
    assert not any(n == 'create_order' for n, _ in svc.client.calls)


def test_reconcile_uses_recorded_symbol_clears_only_target_and_requires_account(svc, monkeypatch):
    link = unknown(svc)
    guard = svc._drawdown
    with guard.locked():
        data = guard.load()
        state = guard.current(data)
        guard.pending(data, state, 'ETHUSDT', 'second-link')
    seen = []
    def query(symbol, recorded_link):
        seen.append((symbol, recorded_link))
        return [{'symbol': symbol, 'orderLinkId': recorded_link, 'orderStatus': 'Cancelled'}]
    monkeypatch.setattr(svc.client, 'order_by_link', query)
    # The service's configured market must not replace the symbol persisted with a pending item.
    svc.symbol = 'SOLUSDT'
    result = svc.reconcile_drawdown_order('second-link', confirm=True)
    assert seen == [('ETHUSDT', 'second-link')]
    assert result['pending_orders'] == [{'symbol': 'BTCUSDT', 'orderLinkId': link}]
    svc.client.uid = 'other-account'
    with pytest.raises(DrawdownError):
        svc.reconcile_drawdown_order(link, confirm=True)
    svc.client.uid = 'fake-uid'
    assert svc.drawdown_status()['blocked']


def test_positive_history_evidence_reconciles_and_empty_history_never_does(svc, monkeypatch):
    from tests.test_agent_bybit import SeqSession
    link = unknown(svc)
    session = SeqSession([{'retCode': 0, 'result': {'list': []}},
        {'retCode': 0, 'result': {'list': [{'symbol': 'BTCUSDT', 'orderLinkId': link, 'orderStatus': 'Filled'}]}}])
    client = Bybit('https://fake', 'key', 'secret', session=session)
    monkeypatch.setattr(svc.client, 'order_by_link', client.order_by_link)
    assert svc.reconcile_drawdown_order(link, confirm=True)['reconciled']
    assert [r['url'].split('?')[0].rsplit('/', 1)[-1] for r in session.sent] == ['realtime', 'history']
    assert svc.drawdown_status()['blocked']


@pytest.mark.parametrize('route', ['service', 'router'])
def test_store_lock_stays_held_during_final_wallet_pending_save_and_transport(svc, route, monkeypatch):
    import fcntl
    r = router_for(svc) if route == 'router' else None
    service = svc if r is None else r._service
    original_context = service.context
    context_calls = 0
    original_wallet = svc.client.wallet
    original_create = svc.client.create_order
    def assert_locked():
        with (svc.state_dir/'drawdown.lock').open('a') as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def wallet():
        assert_locked()
        return original_wallet()
    def context(req):
        nonlocal context_calls
        context_calls += 1
        # Service preflight is outside the guard; router REST context exists only inside it.
        if r is not None or context_calls > 1:
            monkeypatch.setattr(svc.client, 'wallet', wallet)
        return original_context(req)
    def create(**params):
        assert_locked()
        persisted = json.loads(service._drawdown.path.read_text())
        pending = next(iter(persisted['accounts'].values()))['pending']
        assert {'symbol': params['symbol'], 'orderLinkId': params['orderLinkId']} in pending
        return original_create(**params)
    monkeypatch.setattr(service, 'context', context)
    monkeypatch.setattr(svc.client, 'create_order', create)
    svc.place_order(REQ) if r is None else r.submit(Entry('Buy', .012, 'market', 79600), 'lock', 80000)


def test_known_terminal_flat_can_confirm_rebase_without_erasing_ending_epoch(svc, monkeypatch):
    link = unknown(svc)
    original_epoch = svc.drawdown_status()['epoch']
    monkeypatch.setattr(svc.client, 'order_by_link', lambda symbol, recorded_link: [
        {'symbol': symbol, 'orderLinkId': recorded_link, 'orderStatus': 'Filled'}])
    result = svc.reset_drawdown_baseline(confirm=True, reason='confirmed cash flow')
    assert result['latched'] and result['epoch'] != original_epoch
    assert result['history'][-1]['epoch'] == original_epoch
    assert result['history'][-1]['pending'] == [{'symbol': 'BTCUSDT', 'orderLinkId': link}]
    assert result['pending_orders'] == []


def test_empty_realtime_and_history_remain_unknown(svc, monkeypatch):
    from tests.test_agent_bybit import SeqSession
    link = unknown(svc)
    session = SeqSession([{'retCode': 0, 'result': {'list': []}}, {'retCode': 0, 'result': {'list': []}}])
    monkeypatch.setattr(svc.client, 'order_by_link', Bybit('https://fake', 'key', 'secret', session=session).order_by_link)
    with pytest.raises(DrawdownError):
        svc.reconcile_drawdown_order(link, confirm=True)
    assert len(session.sent) == 2
    assert svc.drawdown_status()['pending_orders']
    assert svc.drawdown_status()['blocked']
