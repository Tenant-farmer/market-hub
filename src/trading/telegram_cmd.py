"""텔레그램 양방향 명령 — 워커 데몬 스레드가 getUpdates 롱폴링.

지원: /잔고(/bal) /신호(/sig) /킬스위치(/kill) [on|off] /도움말 — TELEGRAM_CHAT_ID
발신 메시지만 처리(보안). 킬스위치는 DB(trading_state.kill) 기반이라 워커·대시보드
모든 프로세스의 리스크 게이트에 즉시 적용된다. 시작 시 밀린 업데이트는 스킵(과거 명령 재실행 방지).
"""
import os
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"

HELP = ("<b>명령</b>\n"
        "/잔고 (/bal) — 키움·알파카 계좌 현황\n"
        "/신호 (/sig) — 매수 신호등(US/KR) + 게이트 상태\n"
        "/킬스위치 (/kill) on|off — 전 주문 차단 토글 (인자 없으면 상태만)\n"
        "/help (/도움말) — 이 목록")


def register_commands() -> bool:
    """텔레그램 '/' 자동완성 메뉴 등록 (setMyCommands — 1회면 충분, 서버측 저장)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return False
    r = requests.post(API.format(token=token, method="setMyCommands"), json={"commands": [
        {"command": "bal", "description": "잔고 — 키움·알파카 계좌 현황"},
        {"command": "sig", "description": "신호 — 매수 신호등(US/KR) + 게이트"},
        {"command": "kill", "description": "킬스위치 on|off — 전 주문 차단"},
        {"command": "help", "description": "명령 목록"},
    ]}, timeout=15)
    return r.ok and r.json().get("ok", False)


def _balance(con) -> str:
    from datetime import date

    L = ["<b>💼 잔고</b>"]
    today = date.today().isoformat()

    def _prev(broker):
        try:
            r = con.execute(
                "SELECT equity FROM portfolio_snapshots WHERE broker=? AND date<? "
                "ORDER BY date DESC LIMIT 1", (broker, today)).fetchone()
            return r["equity"] if r else None
        except Exception:
            return None

    kr_eq = us_eq = None
    try:
        from src.trading.brokers import kiwoom

        if kiwoom.configured():
            b = kiwoom.KiwoomBroker().account_balance()
            if b:
                kr_eq = b["cash"]                      # 추정예탁자산 = 총자산
                prev = _prev("kiwoom")
                chg = f" (전일比 {(kr_eq / prev - 1) * 100:+.2f}%)" if prev else ""
                L += ["", f"<b>키움{'모의' if kiwoom.is_mock() else '실전'}</b>",
                      f"총자산 {kr_eq:,.0f}원{chg}",
                      f"평가 {b['value']:,.0f}원 (총자산의 {b['value'] / kr_eq * 100:.1f}% 투입)",
                      f"미실현 {b['pl']:+,.0f}원 ({b['plpc']:+.2f}%) / {len(b['holdings'])}종목"]
            else:
                L += ["", "키움: 조회 실패"]
    except Exception as e:
        L += ["", f"키움: 오류 {str(e)[:40]}"]
    try:
        from src.trading.brokers import alpaca

        if alpaca.configured():
            br = alpaca.AlpacaBroker()
            a = br.get_account()
            pos = br.get_positions()
            us_eq = float(a.get("equity") or 0)
            mv = sum(float(p.get("market_value") or 0) for p in pos)
            pl = sum(float(p.get("unrealized_pl") or 0) for p in pos)
            prev = _prev("alpaca")
            chg = f" (전일比 {(us_eq / prev - 1) * 100:+.2f}%)" if prev else ""
            L += ["", "<b>Alpaca</b>",
                  f"총자산 ${us_eq:,.2f}{chg}",
                  f"평가 ${mv:,.2f} (총자산의 {mv / us_eq * 100:.2f}% 투입)" if us_eq
                  else f"평가 ${mv:,.2f}",
                  f"미실현 ${pl:+,.2f} / {len(pos)}종목"]
    except Exception as e:
        L += ["", f"Alpaca: 오류 {str(e)[:40]}"]
    try:                                              # 원화 합산 (최근 환율)
        fx = con.execute("SELECT close FROM prices_daily WHERE symbol='KRW=X' "
                         "ORDER BY date DESC LIMIT 1").fetchone()
        if kr_eq and us_eq and fx and fx["close"]:
            L += ["", f"합산 ≈ {kr_eq + us_eq * fx['close']:,.0f}원 (@{fx['close']:,.0f})"]
    except Exception:
        pass
    if len(L) == 1:
        L.append("브로커 미설정")
    return "\n".join(L)


def _signals(con) -> str:
    from src.dashboard.queries_macro import kr_signal, vix_signal
    from src.trading import state

    L = ["<b>🚦 신호등</b>"]
    us = vix_signal(con)
    if us:
        L.append(f"US: {us['emoji']} {us['label']} (VIX {us['vix']:.1f} · VVIX {us['vvix']:.0f})")
    kr = kr_signal(con)
    if kr:
        L.append(f"KR: {kr['emoji']} {kr['label']} "
                 f"(VKOSPI {kr['vkospi']:.1f} · 고점比 {kr['kospi_dd']:+.1f}%)")
    st = state.get_state(con)
    gates = [g for g, env in (("청산", "EXIT_ENABLED"), ("신호진입", "SIGNAL_ENTRY_ENABLED"),
                              ("로테이션", "ROTATION_ENABLED")) if os.getenv(env) == "1"]
    kill = state.get_kill(con) or os.getenv("KILL_SWITCH") == "1"
    L.append(f"게이트: mode={st['mode']} armed={st['armed']} · ON: {', '.join(gates) or '없음'}"
             + (" · ⛔킬스위치" if kill else ""))
    return "\n".join(L)


def _kill(con, arg: str) -> str:
    from src.trading import state

    arg = arg.lower()
    if arg == "on":
        state.set_kill(con, True)
        return "⛔ 킬스위치 ON — 모든 신규 주문 차단 (해제: /킬스위치 off)"
    if arg == "off":
        state.set_kill(con, False)
        return "✅ 킬스위치 OFF — 주문 재개"
    on = state.get_kill(con) or os.getenv("KILL_SWITCH") == "1"
    return f"킬스위치 현재: {'⛔ ON' if on else '✅ OFF'} (변경: /킬스위치 on|off)"


def handle(text: str) -> str:
    """명령 텍스트 → 응답 텍스트 (순수 함수 — 테스트 용이)."""
    from src import db

    parts = (text or "").strip().split()
    if not parts:
        return HELP
    c0 = parts[0].lower().split("@")[0]        # 그룹챗의 /cmd@botname 형태 허용
    con = db.connect()
    try:
        if c0 in ("/잔고", "/bal"):
            return _balance(con)
        if c0 in ("/신호", "/sig"):
            return _signals(con)
        if c0 in ("/킬스위치", "/kill"):
            return _kill(con, parts[1] if len(parts) > 1 else "")
        return HELP                                    # /help·/도움말·미지원 → 목록
    finally:
        con.close()


def poll_loop() -> None:
    """getUpdates 롱폴링 데몬 — 워커에서 daemon=True 스레드로 기동."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    url = API.format(token=token, method="getUpdates")
    offset = None
    try:                                       # 시작 시 밀린 업데이트 스킵
        r = requests.get(url, params={"timeout": 0}, timeout=15)
        upds = r.json().get("result", [])
        if upds:
            offset = upds[-1]["update_id"] + 1
    except Exception:
        pass
    while True:
        try:
            r = requests.get(url, params={"timeout": 50, "offset": offset}, timeout=60)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != str(chat):
                    continue                   # 승인된 채팅만
                text = msg.get("text") or ""
                if text.startswith("/"):
                    try:
                        from src import notify

                        notify.send(handle(text))
                    except Exception:
                        pass
        except Exception:
            time.sleep(5)                      # 네트워크 일시 장애 — 잠시 후 재시도
