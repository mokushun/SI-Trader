#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trailing_dca 再現シムの TRAIL_START 感度: 50(現行) → 100 / 200 / なし(決済なし)。

- 再現モデル・データ・換算は trailing_dca_repro.py と同一 (sim_dca を共用し、
  trail_start 引数だけを変える)。TRAIL_STEP は 10pip に固定。
- TS=50 の集計は前回出力 (findings/2026-11-13_trailing-dca-repro_out.txt) の値を
  引用する (同一コードで再現確認済み)。
- 「なし」は含み益での決済を一切せず、押し目買いだけを上限まで積んで年末評価する
  (出口を外したらどうなるかの対照)。
- 対象は完全年 2015–2025 の 4 ペア (USD_CHF の 2019 は前回同様に除外)。
"""
import sys
import numpy as np
from trailing_dca_repro import load_year, fx_series, dates_of, sim_dca, PAIRS

VARIANTS = [(100, "TS100"), (200, "TS200"), (float("inf"), "NONE")]


def tot(s, d):
    return s["price"] + s["swapbase"] * d / 365.0


def main():
    results = []  # (year, pair, name, res)
    for year in range(2015, 2026):
        chf = load_year(year, "CHF_JPY")
        for pair, pip in PAIRS.items():
            bars = load_year(year, pair)
            if len(bars) < 300000:
                continue
            fx = fx_series(bars, None if pair == "USD_JPY" else chf)
            dates = dates_of(bars)
            for ts, name in VARIANTS:
                r = sim_dca(bars, pip, fx, dates, trail_start=ts)
                results.append((year, pair, name, r))
        print(f"... {year} done", file=sys.stderr)

    print("trailing_dca TRAIL_START 感度 (TRAIL_STEP=10 固定, 完全年 2015-2025, 円換算)")
    print()
    for year, pair, name, r in results:
        print(f"{year} {pair} {name}: 価格 {r['price']:+10,.0f} 合計d2 {tot(r, 0.02):+10,.0f} 最悪 {r['worst']:+10,.0f} 決済{r['exits']:4} 末{r['end_open']:2}本")
    print()
    names = [n for _, n in VARIANTS]
    print("== バスケット (43 ペア年) ==")
    for d in (0.0, 0.02):
        line = " | ".join(f"{n} {sum(tot(r, d) for _, _, nm, r in results if nm == n):+,.0f}" for n in names)
        print(f"d={d*100:.0f}%: {line}   (参考 TS50: {'-1,275,652' if d == 0 else '-346,247'})")
    print()
    for pair in PAIRS:
        for n in names:
            sub = [r for _, p, nm, r in results if p == pair and nm == n]
            print(f"{pair} {n}: d2合計 {sum(tot(r, 0.02) for r in sub):+,.0f} | 価格合計 {sum(r['price'] for r in sub):+,.0f}"
                  f" | 決済/年 {np.mean([r['exits'] for r in sub]):.0f} | 最悪 年平均 {np.mean([r['worst'] for r in sub]):,.0f} / 最深 {min(r['worst'] for r in sub):,.0f}")
    print()
    # USD_JPY の年別 (トレンド年の回復と下落年の代償を見る)
    for n in names:
        sub = {y: r for y, p, nm, r in results if p == "USD_JPY" and nm == n}
        cells = " ".join(f"{y}:{tot(r, 0.02):+,.0f}" for y, r in sorted(sub.items()))
        print(f"USD_JPY {n} 年別d2: {cells}")


if __name__ == "__main__":
    main()
