#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USD_JPY: A (年初一括買い・保有) vs B (1円グリッドDCA) の年次比較。

定義 (固定):
  A: 年初に 100万円分を買い (数量 = floor(1,000,000 / 年初 ask))、年末まで保有。
  B: 年初に 10万円分を買う。その後、
     - 全決済: bid が 基準+1.00円 に触れたら全決済し、直ちに 10万円分を買い直す
     - 追加: bid が 平均約定単価-1.00円 に触れたら 10万円分を追加で買う
       (保有コスト合計が 100万円を超える追加はしない = 最大10トランシェ)
     を繰り返す。数量は各トランシェ floor(100,000 / 約定 ask)。
  全決済の基準は2変種:
     B-avg : 保有建玉の平均約定単価 (当初仕様)
     B-last: 直近トランシェの建値 (追加のたびに基準が下がるため決済が早い)
  追加の基準は両変種とも平均約定単価。
  両戦略とも毎年フラットから開始し、年末は最終バーの bid で時価評価する
  (実現+含み = 価格損益)。年は JST の暦年。

約定の前提 (m1.db の bid/ask バー):
  - トリガ判定は bid の高値/安値。買い約定価格は「トリガの bid 価格 + そのバーの
    スプレッド (ac-bc)」、全決済の約定価格はトリガ価格 (open が飛び越えた場合は open)。
  - 同一バーで上下両トリガに触れた場合は全決済を優先し、追加買いは翌バー以降。
  - 追加買いは同一バー内で平均単価を更新しながら複数回あり得る (上限まで)。

スワップの扱い:
  - 実測の履歴スワップ単価は無いため、価格損益とは分離して
    swap_base = Σ (保有数量 × 日次 mid 終値 × 経過日数) を集計し、
    スワップ損益 = swap_base × d / 365 として金利差 d のシナリオで表示する。
    d=2% は OANDA 実測 (2026-11: 買い +89円/万通貨/日 @158円 ⇒ 年 2.07%) に相当。
    d はブローカー受取水準の想定であり、年によって実際は上下する。
  - 保有数量は前バー終了時点のものを使い、週末は経過日数分を一括計上する近似。

M1 内の値動き順序は近似のため絶対値は推定値。
"""
import sqlite3
import sys
import numpy as np
from datetime import datetime, timezone, timedelta

DB = "/home/hatch/workspace/oanda_json/m1.db"
INST = "USD_JPY"
JST = timezone(timedelta(hours=9))
CAP = 1_000_000
TRANCHE = 100_000
STEP = 1.00


def load_year(year):
    t0 = int(datetime(year, 1, 1, tzinfo=JST).timestamp())
    t1 = int(datetime(year + 1, 1, 1, tzinfo=JST).timestamp())
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT t,bo,bh,bl,bc,ao,ac FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t",
        (INST, t0, t1)).fetchall()
    con.close()
    return np.array(rows, dtype=np.float64)


def sim_a(bars, dates):
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ac = bars[:, 5], bars[:, 6]
    ua = int(CAP // ao[0])
    basis = ua * ao[0]
    min_mtm = 0.0
    swap_base = 0.0
    prev_date, prev_mid = dates[0], (bc[0] + ac[0]) / 2.0
    for i in range(len(bars)):
        m = ua * bc[i] - basis
        if m < min_mtm:
            min_mtm = m
        if dates[i] != prev_date:
            gap = (dates[i] - prev_date).days
            if gap > 0:
                swap_base += ua * prev_mid * gap
            prev_date = dates[i]
        prev_mid = (bc[i] + ac[i]) / 2.0
    return {"units": ua, "price": ua * bc[-1] - basis, "min": min_mtm, "swapbase": swap_base}


def sim_b(bars, dates, mode):
    """mode: 'avg' (決済基準=平均約定単価) or 'last' (決済基準=直近建値)。追加は両方とも平均基準。"""
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ac = bars[:, 5], bars[:, 6]
    spread = ac - bc
    ub = int(TRANCHE // ao[0])
    basis = ub * ao[0]
    last_entry = ao[0]
    tr = 1
    realized = 0.0
    resets = adds = 0
    min_mtm = 0.0
    swap_base = 0.0
    units_time = 0.0
    cap_time = 0
    prev_date, prev_mid, prev_u = dates[0], (bc[0] + ac[0]) / 2.0, ub
    for i in range(len(bars)):
        if ub > 0:
            avg = basis / ub
            ref = avg if mode == "avg" else last_entry
            hit_exit = bh[i] >= ref + STEP
            hit_add = bl[i] <= avg - STEP
            if hit_exit:  # 両触れの場合は決済優先
                fill = max(ref + STEP, bo[i])
                realized += ub * fill - basis
                resets += 1
                ask_fill = fill + spread[i]
                ub = int(TRANCHE // ask_fill)
                basis = ub * ask_fill
                last_entry = ask_fill
                tr = 1
            elif hit_add:
                while tr < 10 and bl[i] <= basis / ub - STEP:
                    trig = basis / ub - STEP
                    ask_fill = trig + spread[i]
                    u = int(TRANCHE // ask_fill)
                    ub += u
                    basis += u * ask_fill
                    last_entry = ask_fill
                    tr += 1
                    adds += 1
        m = realized + ub * bc[i] - basis
        if m < min_mtm:
            min_mtm = m
        units_time += ub
        if tr >= 10:
            cap_time += 1
        if dates[i] != prev_date:
            gap = (dates[i] - prev_date).days
            if gap > 0:
                swap_base += prev_u * prev_mid * gap
            prev_date = dates[i]
        prev_mid = (bc[i] + ac[i]) / 2.0
        prev_u = ub
    n = len(bars)
    return {"price": realized + ub * bc[-1] - basis, "realized": realized, "min": min_mtm,
            "swapbase": swap_base, "resets": resets, "adds": adds, "end_tr": tr,
            "end_units": ub, "avg_units": units_time / n, "cap_share": cap_time / n}


def main():
    years = []
    for y in range(2015, 2027):
        bars = load_year(y)
        if len(bars) < 50000:
            continue
        dates = np.array([datetime.fromtimestamp(x, JST).date() for x in bars[:, 0]])
        r = {"year": y, "n": len(bars),
             "start_mid": (bars[0, 1] + bars[0, 5]) / 2.0,
             "end_mid": (bars[-1, 4] + bars[-1, 6]) / 2.0,
             "partial": (y == 2026) or len(bars) < 300000,
             "a": sim_a(bars, dates),
             "b_avg": sim_b(bars, dates, "avg"),
             "b_last": sim_b(bars, dates, "last")}
        years.append(r)
        print(f"... {y} done (bars {len(bars):,})", file=sys.stderr)

    def tot(s, d):
        return s["price"] + s["swapbase"] * d / 365.0

    print("USD_JPY A(年初100万円一括・保有) vs B(10万円開始・+1円で全決済再購入/-1円で追加, 上限100万円)")
    print("B-avg=決済基準は平均約定単価 / B-last=決済基準は直近建値。追加は両方とも平均-1円。合計は d=2% スワップ込み。")
    print()
    for r in years:
        tag = " (途中)" if r["partial"] else ""
        print(f"{r['year']}{tag} {r['start_mid']:.2f}→{r['end_mid']:.2f}")
        for name, s in (("A     ", r["a"]), ("B-avg ", r["b_avg"]), ("B-last", r["b_last"])):
            extra = ""
            if "resets" in s:
                extra = f" 決済{s['resets']}/追加{s['adds']} 末{s['end_tr']}本 平均数量{s['avg_units']:,.0f} 上限張付{s['cap_share']*100:.0f}%"
            print(f"   {name}: 価格 {s['price']:+10,.0f}  合計d2 {tot(s, 0.02):+10,.0f}  最大含損 {s['min']:+10,.0f}{extra}")
    print()
    full = [r for r in years if not r["partial"]]
    for d in (0.0, 0.01, 0.02, 0.03):
        ta = sum(tot(r["a"], d) for r in full)
        tb = sum(tot(r["b_avg"], d) for r in full)
        tl = sum(tot(r["b_last"], d) for r in full)
        wa = sum(1 for r in full if tot(r["b_avg"], d) > tot(r["a"], d))
        wl = sum(1 for r in full if tot(r["b_last"], d) > tot(r["a"], d))
        print(f"d={d*100:.0f}% (完全年 {len(full)} 年合計): A {ta:+,.0f} | B-avg {tb:+,.0f} (Aに勝ち {wa}/{len(full)}) | B-last {tl:+,.0f} (Aに勝ち {wl}/{len(full)}) | B-last - B-avg {tl-tb:+,.0f}")
    print(f"最大含み損 (完全年・年平均/最深): A {np.mean([r['a']['min'] for r in full]):,.0f} / {min(r['a']['min'] for r in full):,.0f}"
          f" | B-avg {np.mean([r['b_avg']['min'] for r in full]):,.0f} / {min(r['b_avg']['min'] for r in full):,.0f}"
          f" | B-last {np.mean([r['b_last']['min'] for r in full]):,.0f} / {min(r['b_last']['min'] for r in full):,.0f}")
    print(f"決済回数 (完全年・年平均): B-avg {np.mean([r['b_avg']['resets'] for r in full]):.1f} | B-last {np.mean([r['b_last']['resets'] for r in full]):.1f}")


if __name__ == "__main__":
    main()
