"""키움 REST 어댑터 — 모의(mockapi.kiwoom.com)/실전(api.kiwoom.com) 국내주식 현금 주문.

인증: appkey/secretkey → /oauth2/token 접근토큰(프로세스 캐시, 만료까지 재사용).
주문: POST /api/dostk/ordr, 헤더 api-id kt10000(매수)/kt10001(매도)/kt10003(취소).
멱등: 키움은 client_order_id 개념이 없어 orders 테이블에 이미 있으면 재제출 안 함(앱측 멱등).

레이트리밋(에러 1700) 주의 — 실측으로 확인된 함정:
- 초당 요청 한도 초과 시 '거부' 응답이 와도 **그 요청이 서버에 접수돼 있을 수 있음** (NAVER 이중매수 사건)
- 대응: ① 주문 간 최소 1초 사전 스로틀(_throttle) ② 그래도 1700이면 kt00007로 방금 접수된
  동일 주문이 있는지 **확인 후** 없을 때만 1회 재시도 (블라인드 재시도 금지)
"""
import os
import time
from datetime import datetime, time as dtime

import requests

from src.trading.brokers.base import BrokerAdapter, OrderRequest

_TOKEN = {"val": None, "exp": None}
_ORD_TS = {"t": 0.0}                     # 마지막 주문요청 시각 (사전 스로틀용)


def _throttle():
    """주문류 요청 간 최소 1초 간격 보장 — 1700(초당 한도) 예방."""
    wait = 1.05 - (time.monotonic() - _ORD_TS["t"])
    if wait > 0:
        time.sleep(wait)
    _ORD_TS["t"] = time.monotonic()


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
            _throttle()
            for _ in range(2):        # 1700이면 '유령접수 확인 후' 1회만 재시도 (블라인드 재시도 금지)
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
                if ok or "1700" not in msg:
                    break
                # 레이트리밋 거부 응답이어도 접수됐을 수 있음(실측: NAVER 이중매수) → 확인 후 재시도
                time.sleep(1.2)
                ghost = self._find_recent_order(req)
                if ghost:
                    ok, status = True, "submitted"
                    msg = f"{ghost['ord_no']} 레이트리밋 지연접수 확인"
                    break
                _throttle()
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

    def order_history(self, ord_dt: str | None = None) -> list[dict]:
        """kt00007 계좌별주문체결내역 — 당일(기본) 주문의 체결/미체결 상태. 실패 시 []."""
        try:
            r = requests.post(
                f"{_base()}/api/dostk/acnt",
                headers={"Content-Type": "application/json;charset=UTF-8",
                         "authorization": f"Bearer {_token()}", "api-id": "kt00007"},
                json={"ord_dt": ord_dt or datetime.now().strftime("%Y%m%d"), "qry_tp": "1",
                      "stk_bond_tp": "1", "sell_tp": "0", "stk_cd": "", "fr_ord_no": "",
                      "dmst_stex_tp": "%"}, timeout=15,
            )
            d = r.json() if r.content else {}
            if d.get("return_code") != 0:
                return []
            return [{
                "ord_no": o.get("ord_no", ""), "code": (o.get("stk_cd") or "").lstrip("A"),
                "name": o.get("stk_nm", ""),
                "side": "sell" if "매도" in (o.get("io_tp_nm") or "") else "buy",
                "qty": _num(o.get("ord_qty")), "filled": _num(o.get("cntr_qty")),
                "remain": _num(o.get("ord_remnq")), "price": _num(o.get("cntr_uv")),
                "tm": o.get("ord_tm", ""), "mdfy": o.get("mdfy_cncl", ""),
            } for o in d.get("acnt_ord_cntr_prps_dtl", [])]
        except Exception:
            return []

    def _find_recent_order(self, req: OrderRequest) -> dict | None:
        """직전 ~25초 내 접수된 동일 종목·방향 주문 — 1700 거부 후 유령접수 확인용."""
        side = "sell" if req.action == "sell" else "buy"
        now = datetime.now()
        for o in self.order_history():
            if o["code"] != str(req.ticker) or o["side"] != side:
                continue
            try:
                t = datetime.combine(now.date(),
                                     datetime.strptime(o["tm"], "%H:%M:%S").time())
            except ValueError:
                continue
            if 0 <= (now - t).total_seconds() <= 25:
                return o
        return None

    def cancel_order(self, orig_ord_no: str, stk_cd: str, qty: int = 0) -> dict:
        """kt10003 취소주문 — qty 0이면 전량취소. 반환 {ok, msg}."""
        try:
            _throttle()
            r = requests.post(
                f"{_base()}/api/dostk/ordr",
                headers={"Content-Type": "application/json;charset=UTF-8",
                         "authorization": f"Bearer {_token()}", "api-id": "kt10003"},
                json={"dmst_stex_tp": "KRX", "orig_ord_no": str(orig_ord_no),
                      "stk_cd": str(stk_cd), "cncl_qty": str(int(qty))}, timeout=15,
            )
            d = r.json() if r.content else {}
            return {"ok": d.get("return_code") == 0,
                    "msg": ((d.get("ord_no") or "") + " "
                            + (d.get("return_msg") or "")).strip()[:120]}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:120]}

    def account_balance(self) -> dict | None:
        """kt00018 계좌평가잔고내역 → {예탁·총손익 + 보유종목별 평가손익}. 조회 실패 시 None.

        여러 프로세스(대시보드·텔레그램·reconcile)가 동시에 키움을 때리면 레이트리밋(1700)에
        걸려 간헐 실패 → 사전 스로틀 + 1700/순간오류 시 짧게 대기 후 최대 2회 재시도.
        """
        d = {}
        for attempt in range(3):
            try:
                _throttle()                            # 1초 최소 간격 (submit_order와 공유)
                r = requests.post(
                    f"{_base()}/api/dostk/acnt",
                    headers={"Content-Type": "application/json;charset=UTF-8",
                             "authorization": f"Bearer {_token()}", "api-id": "kt00018"},
                    json={"qry_tp": "1", "dmst_stex_tp": "KRX"}, timeout=15,
                )
                d = r.json() if r.content else {}
                if d.get("return_code") == 0:
                    break
                msg = str(d.get("return_msg", ""))
                if "1700" in msg or d.get("return_code") in (3, 8):   # 레이트리밋/일시 → 재시도
                    time.sleep(1.2)
                    continue
                return None                            # 그 외 오류(인증 등)는 재시도 무의미
            except Exception:
                time.sleep(1.0)
        if d.get("return_code") != 0:
            return None
        try:
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
