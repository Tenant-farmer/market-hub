"""매수 신호등(green) 이후 저점 타이밍 통계 + 일괄매수 vs 분할매수(DCA).

signal_backtest.load()의 SPY·green 시계열을 재사용. '신호 켜지고 며칠 뒤가 저점인가',
'추가하락은 얼마인가', '신호일 일괄매수 vs 20일 5분할 중 무엇이 나은가'를 19년(2007~)으로 검증.
실행: python scripts/signal_timing.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_backtest import load

dates, green, A = load()
spy, n = A["spy"], len(A["spy"])
WIN, GAP = 126, 15                       # 저점 탐색창 6개월, 직전 15일 잠잠하면 새 에피소드

starts = [i for i in range(n) if green[i] and not any(green[max(0, i - GAP):i])]
d2b, dd, lump, dca = [], [], [], []
for i in starts:
    seg = spy[i:min(i + WIN, n)]
    k = int(np.argmin(seg))
    d2b.append(k)
    dd.append(spy[i + k] / spy[i] - 1)
    if i + 63 < n:
        lump.append(spy[i + 63] / spy[i] - 1)
        tr = [spy[min(i + 5 * t, n - 1)] for t in range(5)]       # 0·5·10·15·20일 5분할
        dca.append(spy[i + 63] / np.mean(tr) - 1)

print(f"에피소드 {len(starts)}개 (green 최초, 직전 {GAP}일 잠잠) · 저점탐색 {WIN}일 창\n")
print(f"신호->저점 소요일:  중앙값 {np.median(d2b):.0f}일 · 평균 {np.mean(d2b):.0f}일 · "
      f"25~75%구간 {np.percentile(d2b,25):.0f}~{np.percentile(d2b,75):.0f}일")
print(f"신호->저점 추가하락: 중앙값 {np.median(dd):+.1%} · 평균 {np.mean(dd):+.1%} · 최악 {np.min(dd):+.1%}")
print("\n저점이 신호 후 N일 내에 온 비율:")
for X in [0, 5, 10, 21, 42, 63]:
    print(f"  {X:2}일 내: {np.mean([k <= X for k in d2b]):.0%}")
print(f"\n63일 뒤 수익 - 일괄매수(신호일) 중앙값 {np.median(lump):+.1%} · 평균 {np.mean(lump):+.1%}")
print(f"63일 뒤 수익 - 5분할(0~20일)  중앙값 {np.median(dca):+.1%} · 평균 {np.mean(dca):+.1%}")
print(f"분할이 일괄보다 나은 에피소드 비율: {np.mean(np.array(dca) > np.array(lump)):.0%}")
