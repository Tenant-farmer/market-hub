"""전략 논거 추적 — '이 전략을 굴리는 전제가 아직 유효한가'.

배경(Vibe-Trading thesis-tracker): 대부분의 투자 과정은 '리서치 → 매수 → 기도'다.
매수 후 추적이 없으면 ①팔아야 할 때 안 팔고 ②안 팔아도 될 때 패닉 매도하고 ③왜 샀는지 잊는다.

우리는 규칙 기반이라 '왜 샀나'는 규칙에 있다. 문제는 **규칙의 전제가 깨졌는지**를 아무도
감시하지 않는다는 것. 예: '모멘텀은 월1회 재평가가 최적'이라는 전제는 백테스트 기반인데,
실제로 그렇게 굴러가고 있는지·가정이 여전한지 확인하는 층이 없다.

이 모듈은 전략별 **반증 가능한 가정**을 코드로 정의하고, 실데이터로 위반 여부를 판정한다.
백테스트 근거는 docs/HISTORY.md에 있고, 여기선 '지금도 유효한가'만 본다.
"""
from datetime import date, timedelta


def _pct(a, b):
    return (a / b - 1) * 100 if b else None


def _contrarian_safe(code) -> bool:
    """신호진입(역발상) 포지션인가 — 손절 대상이 아니므로 감시에서도 빼야 한다."""
    try:
        from src.trading.exits import _contrarian

        return _contrarian(code)
    except Exception:
        return False


def _failed(out, strategy, assumption, exc):
    """점검이 실패하면 **항목을 빼지 말고 warn으로 남긴다**.

    2026-07-27 발견: 세 점검이 `except: pass`라 예외 시 항목이 목록에서 사라졌다.
    상태 리포트는 '전제 n/n 유효'를 len(결과)로 계산하므로, 손절 점검이 죽으면
    '4/4 유효 ✅'가 뜬다 — 감시 장치가 조용히 꺼진 채 초록불이 되는 최악의 형태.
    """
    from src.errlog import swallow

    swallow(f"thesis.{strategy}", exc)
    out.append({"strategy": strategy, "assumption": assumption, "status": "warn",
                "detail": f"점검 실패({type(exc).__name__}: {str(exc)[:40]}) — 판정 불가"})


def check_theses(con) -> list[dict]:
    """전략별 가정 점검. 각 항목: {strategy, assumption, status, detail}

    status: ok(유효) / warn(주의) / broken(전제 위반 — 사람이 판단 필요)
    """
    out = []
    today = date.today().isoformat()

    # ─── 전략 1: 주도주 로테이션 (US) ───────────────────────────
    # 논거: 126일 모멘텀 상위가 계속 이긴다. 전제 = ①주1회 평가가 실제로 돌아감
    #      ②슬롯이 실제 보유로 이어짐 ③손절이 작동
    try:
        slots = con.execute("SELECT COUNT(*) n FROM rotation_slots WHERE symbol NOT GLOB "
                            "'[0-9]*'").fetchone()["n"]
        last_rot = con.execute(
            "SELECT MAX(created_at) d FROM orders WHERE created_at >= ? "
            "AND ticker NOT GLOB '[0-9]*'",
            ((date.today() - timedelta(days=14)).isoformat(),)).fetchone()["d"]
        out.append({
            "strategy": "로테이션 US",
            "assumption": "126일 모멘텀 상위 10슬롯이 채워져 있고 주1회 재평가된다",
            "status": "ok" if slots >= 8 else "warn" if slots >= 5 else "broken",
            "detail": f"슬롯 {slots}/10 · 최근 2주 주문 {'있음 ' + last_rot[:10] if last_rot else '없음'}",
        })
    except Exception as e:
        out.append({"strategy": "로테이션 US", "assumption": "슬롯 유지",
                    "status": "warn", "detail": f"조회 실패: {str(e)[:40]}"})

    # ─── 전략 2: KR 신호진입 (VKOSPI) ──────────────────────────
    # 논거: VKOSPI≥30 & 낙폭-5%면 63일 승률 75%. 전제 = 그 조건이 실제로 켜져 있고 매수가 나감
    try:
        from src.dashboard.queries_macro import kr_signal

        ks = kr_signal(con)
        if not ks:                     # VKOSPI·KOSPI 미수집 → 신호 자체를 못 냄
            out.append({
                "strategy": "KR 신호진입",
                "assumption": "green(VKOSPI≥30 & 낙폭-5%)이면 KODEX200을 매일 분할 매수한다",
                "status": "warn", "detail": "VKOSPI/KOSPI 데이터 없음 — 신호 판정 불가"})
        else:
            green = ks["state"] == "buy"
            recent = con.execute(
                "SELECT COUNT(*) n FROM orders WHERE ticker='069500' AND created_at >= ?",
                ((date.today() - timedelta(days=7)).isoformat(),)).fetchone()["n"]
            # VKOSPI가 밀리면 값은 맞아도 '지금'의 신호가 아니다 (2026-07-27: KRX 게시가
            # 아침 수집보다 늦어 1거래일 묵은 값으로 buy가 계속 떴다). 4일 = 주말+공휴일 여유
            stale = ks.get("stale_days")
            outdated = stale is not None and stale > 4
            out.append({
                "strategy": "KR 신호진입",
                "assumption": "green(VKOSPI≥30 & 낙폭-5%)이면 KODEX200을 매일 분할 매수한다",
                "status": "warn" if outdated else ("ok" if (not green or recent > 0) else "broken"),
                "detail": (f"신호 {ks['state']} (VKOSPI {ks['vkospi']:.1f}, 낙폭 "
                           f"{ks['kospi_dd']:+.1f}%) · 최근 7일 매수 {recent}건"
                           + (f" — VKOSPI {stale}일 전 값(묵음)" if outdated else "")
                           + (" — green인데 매수 없음!" if green and recent == 0 else "")),
            })
    except Exception as e:
        _failed(out, "KR 신호진입", "green이면 KODEX200 분할 매수", e)

    # ─── 전략 3: 청산 규칙 ─────────────────────────────────────
    # 논거: 손절 -8%가 꼬리위험을 자른다. 전제 = -8% 넘게 물린 보유가 없어야 함
    try:
        from src.trading.brokers import kiwoom

        if kiwoom.configured():
            b = kiwoom.KiwoomBroker().account_balance()
            if not b:
                # 예외 없이 None이 오는 경로(키움 내부에서 삼킴)도 조용히 넘기면 안 된다 —
                # 점검이 사라지는 대신 '판정 불가'로 남겨야 리포트가 초록불이 되지 않는다
                out.append({
                    "strategy": "청산 규칙",
                    "assumption": "손절 -8%가 작동해 그보다 크게 물린 보유가 없다",
                    "status": "warn", "detail": "잔고 조회 실패 — 손절 감시 판정 불가"})
            else:
                # 손절폭은 시장별로 다르고 env로 바뀐다 → **실제 규칙과 같은 함수**를 쓴다.
                # 여기에 -8%를 박아두면 손절폭을 넓힌 순간 감시가 거짓 경보를 낸다
                # (2026-07-28 실측: KR -25%로 넓혔는데 감시는 -8%라 '전제 위반 🔴'이 떴다)
                from src.trading.exits import _stop_pct

                deep = [h for h in b["holdings"]
                        if h["plpc"] <= _stop_pct(h["code"]) and not _contrarian_safe(h["code"])]
                names = ", ".join(f"{h['name']}({h['plpc']:.1f}%)" for h in deep[:3])
                out.append({
                    "strategy": "청산 규칙",
                    "assumption": "손절(US -15% / KR -25%)이 작동해 그보다 크게 물린 보유가 없다",
                    "status": "ok" if not deep else "broken",
                    "detail": (f"보유 {len(b['holdings'])}종목 중 손절선 초과 {len(deep)}종목"
                               + (f": {names}" if deep else "")),
                })
    except Exception as e:
        _failed(out, "청산 규칙", "손절 -8%가 작동해 크게 물린 보유가 없다", e)

    # ─── 전략 4: 가상장부 A/B ──────────────────────────────────
    # 논거: 모멘텀이 단타보다 낫다(백테스트). 전제 = 실제로도 그 방향이어야 함(반증 가능)
    try:
        eqs = {}
        for s in ("momentum", "meanrev"):
            r = con.execute("SELECT equity FROM daytrade_equity WHERE strategy=? "
                            "ORDER BY date DESC LIMIT 1", (s,)).fetchone()
            if r:
                eqs[s] = r["equity"]
        if len(eqs) == 2:
            m, v = _pct(eqs["momentum"], 100000), _pct(eqs["meanrev"], 100000)
            n = con.execute("SELECT COUNT(DISTINCT date) n FROM daytrade_equity").fetchone()["n"]
            out.append({
                "strategy": "가상장부 A/B",
                "assumption": "장기 모멘텀이 단타보다 우수하다 (백테스트 +12153% vs +349%)",
                "status": "ok" if n < 20 else ("ok" if m >= v else "warn"),
                "detail": (f"모멘텀 {m:+.2f}% vs 단타 {v:+.2f}% ({n}일차)"
                           + (" — 표본 부족, 판정 보류" if n < 20 else
                              "" if m >= v else " — 백테스트와 반대 방향(관찰 계속)")),
            })
    except Exception as e:
        _failed(out, "가상장부 A/B", "모멘텀이 단타보다 우수하다", e)

    # ─── 전략 5: 시스템 가정 ───────────────────────────────────
    # 논거: 자동화가 조용히 실패하지 않는다. 전제 = 워커가 살아있고 알림이 나감
    try:
        last = con.execute("SELECT MAX(run_at) d FROM collector_runs "
                           "WHERE collector='engine'").fetchone()["d"]
        stale = last and last[:10] < today
        out.append({
            "strategy": "시스템",
            "assumption": "엔진 워커가 살아있고 매매·경보 알림이 실제로 발송된다",
            "status": "broken" if stale else "ok",
            "detail": f"엔진 마지막 기록 {last[:16] if last else '없음'}"
                      + (" — 오늘 기록 없음!" if stale else ""),
        })
    except Exception as e:
        _failed(out, "시스템", "엔진 워커가 살아있고 알림이 발송된다", e)
    return out


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    icon = {"ok": "✅", "warn": "⚠️", "broken": "🔴"}
    print("=== 전략 논거 점검 ===")
    for t in check_theses(c):
        print(f"{icon.get(t['status'], '?')} [{t['strategy']}] {t['assumption']}")
        print(f"    → {t['detail']}")
    c.close()
