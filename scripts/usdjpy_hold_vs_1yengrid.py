#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USD_JPY: A (年初一括買い・保有) vs B (1円グリッドDCA) の年次比較。

定義 (固定):
  A: 年初に 100万円分を買い (数量 = floor(1,000,000 / 年初 ask))、年末まで保有。
  B: 年初に 10万円分を買う。その後、保有建玉の平均約定単価を基準に
     - bid が 平均+1.00円 に触れたら全決済し、直ちに 10万円分を買い直す
     - bid が 平均-1.00円 に触れたら 10万円分を追加で買う (保有コスト合計が
       100万円を超える追加はしない = 最大10トランシェ)
     を繰り返す。数量は各トランシェ floor(100,000 / 約定 ask)。
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


def run_year(bars):
    n = len(bars)
    t = bars[:, 0]
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ac = bars[:, 5], bars[:, 6]
    spread = ac - bc
    dates = np.array([datetime.fromtimestamp(x, JST).date() for x in t])

    # ---- A ----
    ua = int(CAP // ao[0])
    basis_a = ua * ao[0]
    min_a = 0.0
    swap_a = 0.0

    # ---- B ----
    ub = int(TRANCHE // ao[0])
    basis_b = ub * ao[0]
    tr_b = 1
    realized_b = 0.0
    resets = adds = cap_hits = 0
    min_b = 0.0
    swap_b = 0.0
    units_time_b = 0.0
    cap_time_b = 0

    prev_date = dates[0]
    prev_mid = (bc[0] + ac[0]) / 2.0
    prev_ua, prev_ub = ua, ub

    for i in range(n):
        # ---- B のトリガ ----
        if ub > 0:
            avg = basis_b / ub
            hit_exit = bh[i] >= avg + STEP
            hit_add = bl[i] <= avg - STEP
            if hit_exit:  # 両触れの場合は決済優先
                fill = max(avg + STEP, bo[i])
                realized_b += ub * fill - basis_b
                resets += 1
                ask_fill = fill + spread[i]
                ub = int(TRANCHE // ask_fill)
                basis_b = ub * ask_fill
                tr_b = 1
            elif hit_add:
                while tr_b < 10 and bl[i] <= basis_b / ub - STEP:
                    trig = basis_b / ub - STEP
                    ask_fill = trig + spread[i]
                    u = int(TRANCHE // ask_fill)
                    ub += u
                    basis_b += u * ask_fill
                    tr_b += 1
                    adds += 1
                if tr_b >= 10 and bl[i] <= basis_b / ub - STEP:
                    cap_hits += 1
        # ---- 時価 (価格のみ) ----
        ma = ua * bc[i] - basis_a
        if ma < min_a:
            min_a = ma
        mb = realized_b + ub * bc[i] - basis_b
        if mb < min_b:
            min_b = mb
        units_time_b += ub
        if tr_b >= 10:
            cap_time_b += 1
        # ---- スワップ原資 (日付が進んだら前バー末保有分を計上) ----
        if dates[i] != prev_date:
            gap = (dates[i] - prev_date).days
            if gap > 0:
                swap_a += prev_ua * prev_mid * gap
                swap_b += prev_ub * prev_mid * gap
            prev_date = dates[i]
        prev_mid = (bc[i] + ac[i]) / 2.0
        prev_ua, prev_ub = ua, ub

    end_bid = bc[-1]
    return {
        "n": n,
        "start_mid": (bo[0] + ao[0]) / 2.0, "end_mid": (bc[-1] + ac[-1]) / 2.0,
        "a_units": ua, "a_price": ua * end_bid - basis_a, "a_min": min_a, "a_swapbase": swap_a,
        "b_price": realized_b + ub * end_bid - basis_b, "b_realized": realized_b,
        "b_min": min_b, "b_swapbase": swap_b, "b_resets": resets, "b_adds": adds,
        "b_cap_hits": cap_hits, "b_end_tr": tr_b, "b_end_units": ub,
        "b_avg_units": units_time_b / n, "b_cap_share": cap_time_b / n,
    }


def main():
    years = []
    for y in range(2015, 2027):
        bars = load_year(y)
        if len(bars) < 50000:
            continue
        r = run_year(bars)
        r["year"] = y
        r["partial"] = (y == 2026) or len(bars) < 300000  # 2026 はデータが 11-10 まで
        years.append(r)
        print(f"... {y} done (bars {len(bars):,})", file=sys.stderr)

    def tot(r, strat, d):
        return r[f"{strat}_price"] + r[f"{strat}_swapbase"] * d / 365.0

    print("USD_JPY A(年初100万円一括・保有) vs B(10万円開始・平均±1円で全決済再購入/追加, 上限100万円)")
    print("価格損益は年末 bid 評価 (実現+含み)。合計は d=2% のスワップ込み (括弧内はスワップ分)。")
    print()
    hdr = f"{'年':>6} {'始値→終値':>17} {'A価格':>10} {'A合計d2':>11} {'A最大含損':>11} | {'B価格':>10} {'B合計d2':>11} {'B最大含損':>11} {'B決済/追加':>10} {'B末本数':>7} {'B平均数量':>9}"
    print(hdr)
    for r in years:
        a2 = tot(r, "a", 0.02); b2 = tot(r, "b", 0.02)
        a_sw = r["a_swapbase"] * 0.02 / 365.0; b_sw = r["b_swapbase"] * 0.02 / 365.0
        tag = " (途中)" if r["partial"] else ""
        print(f"{r['year']:>6}{tag:6} {r['start_mid']:7.2f}→{r['end_mid']:7.2f} "
              f"{r['a_price']:>+10,.0f} {a2:>+11,.0f} {r['a_min']:>+11,.0f} | "
              f"{r['b_price']:>+10,.0f} {b2:>+11,.0f} {r['b_min']:>+11,.0f} "
              f"{r['b_resets']:>4}/{r['b_adds']:<5} {r['b_end_tr']:>7} {r['b_avg_units']:>9,.0f}")
        print(f"       スワップd2: A {a_sw:+,.0f} / B {b_sw:+,.0f}   B上限張り付き時間 {r['b_cap_share']*100:.0f}% / 上限到達後の追加不能バー {r['b_cap_hits']}")
    print()
    full = [r for r in years if not r["partial"]]
    for d in (0.0, 0.01, 0.02, 0.03):
        ta = sum(tot(r, "a", d) for r in full); tb = sum(tot(r, "b", d) for r in full)
        wins = sum(1 for r in full if tot(r, "b", d) > tot(r, "a", d))
        print(f"d={d*100:.0f}% (完全年 {len(full)} 年合計): A {ta:+,.0f} | B {tb:+,.0f} | 差 B-A {tb-ta:+,.0f} | Bが勝った年 {wins}/{len(full)}")
    print(f"最大含み損 (完全年中の最深): A {min(r['a_min'] for r in full):,.0f} | B {min(r['b_min'] for r in full):,.0f}")
    print(f"年平均の最大含み損: A {np.mean([r['a_min'] for r in full]):,.0f} | B {np.mean([r['b_min'] for r in full]):,.0f}")


if __name__ == "__main__":
    main()
