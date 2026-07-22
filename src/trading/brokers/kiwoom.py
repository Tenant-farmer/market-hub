"""키움 REST 어댑터 — 모의(mockapi.kiwoom.com)/실전(api.kiwoom.com) 국내주식 현금 주문.

인증: appkey/secretkey → /oauth2/token 접근토큰(프로세스 캐시, 만료까지 재사용).
주문: POST /api/dostk/ordr, 헤더 api-id kt10000(매수)/kt10001(매도).
멱등: 키움은 client_order_id 개념이 없어 orders 테이블에 이미 있으면 재제출 안 함(앱측 멱등).
"""
import os
from datetime import datetime, time as dtime

import requests

from src.trading.brokers.base import BrokerAdapter, OrderRequest

_TOKEN = {"val": None, "exp": None}


def _num(s) -> float:
    """키움 응답의 제로패딩 숫자문자열 → float (부호·소수점 처리)."""
    s = (s or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("-")
    s = s.lstrip("-").lstrip("0") or "0"
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def is_mock() -> bool:
    return os.getenv("KIWOOM_MOCK", "1") == "1"


def configured() -> bool:
    return bool(os.getenv("KIWOOM_APP_KEY")) and bool(os.getenv("KIWOOM_APP_SECRET"))


def _base() -> str:
    return "https://mockapi.kiwoom.com" if is_mock() else "https://api.kiwoom.com"


def _token() -> str | None:
    now = datetime.now()
    if _TOKEN["val"] and _TOKEN["exp"] and now < _TOKEN["exp"]:
        return _TOKEN["val"]
    r = requests.post(
        f"{_base()}/oauth2/token",
        json={"grant_type": "client_credentials",
              "appkey": os.getenv("KIWOOM_APP_KEY"), "secretkey": os.getenv("KIWOOM_APP_SECRET")},
        headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=15,
    )
    d = r.json() if r.content else {}
    tok = d.get("token") or d.get("access_token")
    _TOKEN["val"] = tok
    try:
        _TOKEN["exp"] = datetime.strptime(d.get("expires_dt", ""), "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        _TOKEN["exp"] = None
    return tok


class KiwoomBroker(BrokerAdapter):
    name = "kiwoom"

    def submit_order(self, con, req: OrderRequest, client_order_id: str,
                     signal_id: int | None = None) -> dict:
        # 앱측 멱등: 같은 client_order_id가 이미 있으면 재제출 안 함 (중복 주문 방지)
        exist = con.execute(
            "SELECT status FROM orders WHERE client_order_id=?", (client_order_id,)
        ).fetchone()
        if exist:
            return {"ok": True, "dup": True, "status": exist["status"]}

        api_id = "kt10000" if req.action == "buy" else "kt10001"
        market = not req.price or req.price <= 0
        body = {
            "dmst_stex_tp": "KRX",
            "stk_cd": str(req.ticker),
            "ord_qty": str(int(req.qty or 1)),
            "ord_uv": "" if market else str(int(req.price)),
            "trde_tp": "3" if market else "0",   # 3: 시장가, 0: 보통(지정가)
        }
        ok, status, msg = False, "error", ""
        try:
            r = requests.post(
                f"{_base()}/api/dostk/ordr",
                headers={"Content-Type": "application/json;charset=UTF-8",
                         "authorization": f"Bearer {_token()}", "api-id": api_id},
                json=body, timeout=15,
            )
            d = r.json() if r.content else {}
            rc = d.get("return_code")
            ok = rc == 0
            status = "submitted" if ok else f"rejected(rc={rc})"
            msg = ((d.get("ord_no") or "") + " " + (d.get("return_msg") or "")).strip()[:150]
        except Exception as e:
            msg = str(e)[:150]

        con.execute(
            "INSERT OR IGNORE INTO orders "
            "(signal_id, client_order_id, broker, ticker, action, qty, price, status, created_at, message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, client_order_id, f"{self.name}{'-mock' if is_mock() else ''}",
             req.ticker, req.action, req.qty, req.price, status,
             datetime.now().isoformat(timespec="seconds"), msg),
        )
        con.commit()
        return {"ok": ok, "dup": False, "status": status}

    def account_balance(self) -> dict | None:
        """kt00018 계좌평가잔고내역 → {예탁·총손익 + 보유종목별 평가손익}. 조회 실패 시 None."""
        try:
            r = requests.post(
                f"{_base()}/api/dostk/acnt",
                headers={"Content-Type": "application/json;charset=UTF-8",
                         "authorization": f"Bearer {_token()}", "api-id": "kt00018"},
                json={"qry_tp": "1", "dmst_stex_tp": "KRX"}, timeout=15,
            )
            d = r.json() if r.content else {}
            if d.get("return_code") != 0:
                return None
            holdings = [
                {
                    "code": h["stk_cd"].lstrip("A"), "name": h.get("stk_nm", ""),
                    "qty": _num(h.get("rmnd_qty")), "avg": _num(h.get("pur_pric")),
                    "cur": _num(h.get("cur_prc")), "value": _num(h.get("evlt_amt")),
                    "pl": _num(h.get("evltv_prft")), "plpc": _num(h.get("prft_rt")),
                }
                for h in d.get("acnt_evlt_remn_indv_tot", [])
            ]
            return {
                "cash": _num(d.get("prsm_dpst_aset_amt")),
                "pur": _num(d.get("tot_pur_amt")), "value": _num(d.get("tot_evlt_amt")),
                "pl": _num(d.get("tot_evlt_pl")), "plpc": _num(d.get("tot_prft_rt")),
                "holdings": holdings,
            }
        except Exception:
            return None

    def is_market_open(self, ticker: str) -> bool:
        now = datetime.now()   # 서버 로컬(KST) 기준
        if now.weekday() >= 5:
            return False
        return dtime(9, 0) <= now.time() <= dtime(15, 30)
