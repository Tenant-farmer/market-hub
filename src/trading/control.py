"""실전 게이트 조작 CLI — 상태 확인 / 모드 전환 / 무장·해제.

  python -m src.trading.control status          # 현재 상태 + 한도 + 오늘 주문수
  python -m src.trading.control mode paper       # log | paper | live
  python -m src.trading.control arm              # live 실주문 허용 (경고 표시)
  python -m src.trading.control disarm           # 무장 해제 (안전)

live 실전은 mode=live 와 arm 이 모두 필요. 어느 하나라도 아니면 로그/페이퍼로만 처리된다.
"""
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from src import db
from src.trading import ensure_tables, risk, state


def _status(con):
    st = state.get_state(con)
    today = date.today().isoformat()
    n = con.execute(
        "SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (today,)
    ).fetchone()["c"]
    live_hot = st["mode"] == "live" and st["armed"]
    print("=== 실전 게이트 상태 ===")
    print(f"  모드(mode)   : {st['mode']}")
    print(f"  무장(armed)  : {'예 ⚠' if st['armed'] else '아니오'}")
    print(f"  → 실주문 여부: {'★ 실전 활성 (real orders) ★' if live_hot else '로그/페이퍼만 (안전)'}")
    print(f"  킬스위치     : {'활성(전면중단)' if os.getenv('KILL_SWITCH') == '1' else '해제'}")
    print("--- 리스크 한도 ---")
    print(f"  주문상한 US  : {risk._f('MAX_ORDER_USD', risk.MAX_ORDER_USD):,.0f} USD")
    print(f"  주문상한 KR  : {risk._f('MAX_ORDER_KRW', risk.MAX_ORDER_KRW):,.0f} KRW")
    print(f"  일일 주문상한: {int(risk._f('MAX_DAILY_ORDERS', risk.MAX_DAILY_ORDERS))}건 (오늘 {n}건)")
    print("--- 청산 레이어 ---")
    ex_on = os.getenv("EXIT_ENABLED") == "1"
    ma_on = os.getenv("EXIT_MA_ENABLED") == "1"
    print(f"  자동청산     : {'ON' if ex_on else 'OFF (EXIT_ENABLED=1 로 켬)'}")
    print(f"  규칙         : 손절 {risk._f('EXIT_STOP_PCT', -8.0):+.0f}% · "
          f"주도이탈 RS<{risk._f('EXIT_RS', 0.0):+.0f}"
          + (f" · 추세이탈 {int(risk._f('EXIT_MA', 20))}MA" if ma_on
             else " · 추세이탈 off(백테스트상 해로움)"))
    print("  미리보기: python -m src.trading.exits --dry")
    print("--- 신호진입 (green→지수) ---")
    en_on = os.getenv("SIGNAL_ENTRY_ENABLED") == "1"
    print(f"  자동진입     : {'ON' if en_on else 'OFF (SIGNAL_ENTRY_ENABLED=1 로 켬)'}")
    print(f"  대상         : {os.getenv('SIGNAL_ENTRY_SYMBOL', 'SPY')} "
          f"x {os.getenv('SIGNAL_ENTRY_QTY', '1')}")
    print("  미리보기: python -m src.trading.signal_entry")
    print("--- 주도주 로테이션 (126일) ---")
    ro_on = os.getenv("ROTATION_ENABLED") == "1"
    print(f"  자동로테이션 : {'ON' if ro_on else 'OFF (ROTATION_ENABLED=1 로 켬)'}")
    print(f"  규칙         : top10 진입 / top30 이탈 · 주 1회 · "
          f"슬롯 10개 x ${float(os.getenv('ROTATION_SLOT_USD', '1000')):,.0f}")
    print("  미리보기: python -m src.trading.leader_rotation --dry")


def main(argv):
    con = db.connect()
    ensure_tables(con)
    cmd = argv[0] if argv else "status"

    if cmd == "status":
        _status(con)
    elif cmd == "mode":
        if len(argv) < 2 or argv[1] not in state.MODES:
            print(f"사용: mode {'|'.join(state.MODES)}")
            return 1
        state.set_mode(con, argv[1])
        print(f"모드 → {argv[1]}")
        if argv[1] == "live":
            print("주의: live 모드입니다. 실주문은 'arm' 까지 해야 활성화됩니다.")
        _status(con)
    elif cmd == "arm":
        st = state.get_state(con)
        state.set_armed(con, True)
        print("⚠ 무장(armed=1) 설정됨.")
        if st["mode"] != "live":
            print(f"  다만 현재 모드가 '{st['mode']}'라 실주문은 아직 안 나갑니다 (mode live 필요).")
        else:
            print("  ★ mode=live + armed → 실주문이 실제로 나갑니다. 신중히. ★")
        _status(con)
    elif cmd == "disarm":
        state.set_armed(con, False)
        print("무장 해제(armed=0) — 안전 상태.")
        _status(con)
    else:
        print(__doc__)
        return 1
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
