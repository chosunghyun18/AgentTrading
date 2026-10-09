"""agent.bybit 서명·요청 구성·오류 처리, agent.settings 키·모드·LIVE 잠금 — 네트워크 없음."""

import hashlib
import hmac
import json

import pytest

from src.agent import settings
from src.agent.bybit import DEMO_URL, LIVE_URL, Bybit, BybitError


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        if isinstance(self._d, Exception):
            raise self._d
        return self._d


class Session:
    def __init__(self, data=None):
        self.data = data if data is not None else {"retCode": 0, "result": {"list": [{"lastPrice": "1"}]}}
        self.sent = []

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.sent.append({"method": method, "url": url, "headers": headers, "data": data})
        return Resp(self.data)


def _client(session, key="KEY", secret="SECRET"):
    return Bybit(DEMO_URL, key, secret, session=session, clock=lambda: 1_700_000_000.123)


def test_get_signature_over_query_string():
    s = Session()
    _client(s).positions("BTCUSDT")
    req = s.sent[0]
    assert req["url"] == DEMO_URL + "/v5/position/list?category=linear&symbol=BTCUSDT"
    h = req["headers"]
    assert h["X-BAPI-TIMESTAMP"] == "1700000000123" and h["X-BAPI-RECV-WINDOW"] == "5000"
    want = hmac.new(b"SECRET", b"1700000000123KEY5000category=linear&symbol=BTCUSDT", hashlib.sha256).hexdigest()
    assert h["X-BAPI-SIGN"] == want and req["data"] is None


def test_post_signature_over_json_body_and_drops_none():
    s = Session({"retCode": 0, "result": {"orderId": "1"}})
    _client(s).create_order(symbol="BTCUSDT", side="Buy", orderType="Market", qty="0.01", price=None)
    req = s.sent[0]
    body = req["data"]
    assert json.loads(body) == {"category": "linear", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Market",
                                "qty": "0.01"}
    want = hmac.new(b"SECRET", ("1700000000123KEY5000" + body).encode(), hashlib.sha256).hexdigest()
    assert req["headers"]["X-BAPI-SIGN"] == want


def test_public_call_has_no_auth_headers():
    s = Session()
    Bybit(LIVE_URL, session=s).ticker("BTCUSDT")
    assert "X-BAPI-SIGN" not in s.sent[0]["headers"]


def test_auth_without_keys_raises_before_sending():
    s = Session()
    with pytest.raises(BybitError, match="API 키"):
        Bybit(DEMO_URL, session=s).wallet()
    assert s.sent == []


def test_retcode_error_hides_secret():
    s = Session({"retCode": 10003, "retMsg": "API key is invalid."})
    with pytest.raises(BybitError) as e:
        _client(s).wallet()
    assert e.value.code == 10003 and "SECRET" not in str(e.value) and "KEY" not in str(e.value).replace("key", "")


def test_non_json_response():
    s = Session(ValueError("html"))
    with pytest.raises(BybitError, match="비JSON"):
        _client(s).ticker("BTCUSDT")


def test_set_leverage_not_modified_is_ok():
    _client(Session({"retCode": 110043, "retMsg": "leverage not modified"})).set_leverage("BTCUSDT", 3)
    with pytest.raises(BybitError):
        _client(Session({"retCode": 10001, "retMsg": "bad"})).set_leverage("BTCUSDT", 3)


# settings ------------------------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    for n in ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET", "BYBIT_LIVE_API_KEY", "BYBIT_LIVE_API_SECRET",
              settings.LIVE_FLAG):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("AT_AGENT_DIR", str(tmp_path / "agent"))
    return monkeypatch


def test_keys_masked(env):
    assert settings.keys("demo") is None
    env.setenv("BYBIT_DEMO_API_KEY", "abcdefgh1234")
    env.setenv("BYBIT_DEMO_API_SECRET", "s")
    assert settings.keys("demo").masked() == "…1234"


def test_live_lock(env):
    assert settings.get_mode() == "demo"
    with pytest.raises(PermissionError):
        settings.set_mode("live")
    env.setenv("BYBIT_LIVE_API_KEY", "k")
    env.setenv("BYBIT_LIVE_API_SECRET", "s")
    with pytest.raises(PermissionError):  # 플래그 없음
        settings.set_mode("live")
    env.setenv(settings.LIVE_FLAG, "1")
    settings.set_mode("live")
    assert settings.get_mode() == "live"
    env.delenv(settings.LIVE_FLAG)  # 잠금이 다시 걸리면 저장값이 live 여도 demo
    assert settings.get_mode() == "demo"
    with pytest.raises(ValueError):
        settings.set_mode("paper")



class SeqSession(Session):
    def __init__(self, seq):
        super().__init__()
        self.seq = list(seq)

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.sent.append({"method": method, "url": url, "headers": headers, "data": data})
        return Resp(self.seq.pop(0))


def test_timestamp_error_syncs_clock_and_retries_once():
    s = SeqSession([{"retCode": 10002, "retMsg": "timestamp"},
                    {"retCode": 0, "result": {"timeNano": str(1_700_000_005_123 * 1_000_000)}},
                    {"retCode": 0, "result": {"list": [{"totalEquity": "1"}]}}])
    c = _client(s)
    assert c.wallet() == {"totalEquity": "1"}
    assert [r["url"].split("?")[0].rsplit("/", 1)[-1] for r in s.sent] == ["wallet-balance", "time", "wallet-balance"]
    assert s.sent[2]["headers"]["X-BAPI-TIMESTAMP"] == "1700000005123"


def test_closed_pnl_paginates():
    s = SeqSession([{"retCode": 0, "result": {"list": [{"closedPnl": "-1"}], "nextPageCursor": "c2"}},
                    {"retCode": 0, "result": {"list": [{"closedPnl": "-2"}], "nextPageCursor": ""}}])
    rows = _client(s).closed_pnl("BTCUSDT", start_ms=1)
    assert [r["closedPnl"] for r in rows] == ["-1", "-2"] and "cursor=c2" in s.sent[1]["url"]


def test_keys_repr_hides_secret():
    assert "TOPSECRET" not in repr(settings.Keys("abcd1234", "TOPSECRET"))
