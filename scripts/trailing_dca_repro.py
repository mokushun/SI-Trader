#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trailing_dca.py (実運用 DCA_TREND) の m1.db 再現と比較検証。

実コード (~/workspace/user/files/trailing_dca.py) の仕様:
  - 対象: USD_JPY / GBP_CHF / AUD_CHF / USD_CHF (買い専用)
  - 1本 250 単位、最大 40 本/ペア、損切りなし
  - エントリー: 買い逆指値を1本だけ置き、modify_to_trailing_order(STEP=10pip) で
    下落に追従させる。追加は「最安建値 -10pip まで下げた新安値」が条件。
  - 決済: modify_to_trailing_stop(TRAIL_START=50, TRAIL_STEP=10) —
    含み益が 50pip に達した建玉に 10pip のネイティブトレーリングを付与。
  - 証拠金ゲート (維持率500%) は口座共有の制約なので再現しない
    (実運用はこれで新規が止まる分、シムより大人しい)。

再現モデル (両建てシム trailing_hedge_* で検証済みの oanda_API セマンティクス):
  - エントリー: 実行中の最安 bid (極値) を追い、極値+10pip への反発で約定
    (約定価格は ask 側: max(trigger, ask open))。追加は極値が最安建値-10pip 以下に
    ある場合のみ。約定ごとに極値はそのバーの安値にリセット。
    ※ 実コードの初回注文 (mid+20pip) と配置判定 (mid 基準) は、極値モデルでは
      「安値から+10pip の反発で約定」に置き換わる (細部の相違として明記)。
  - 決済: 建玉ごとに最高 bid を追い、建値+50pip 到達で武装、最高値-10pip の
    ストップに bid 安値が触れたら決済 (open が飛び越えたら open 価格)。
  - 決済判定はエントリー判定より先 (同一バーで建てた建玉はそのバーで決済しない)。

比較対象 (同一ペア・同一最大数量 10,000 単位で正規化):
  - A-hold : 年初に 10,000 単位を買い、年末まで保有。
  - B-last : 1,000 単位開始、平均-100pip で 1,000 単位追加 (最大10本)、
             直近建値+100pip で全決済して 1,000 単位買い直し (usdjpy_hold_vs_1yengrid の一般化)。

損益は円換算: CHF 建てペアは CHF_JPY の mid (同一時刻、無ければ直近) で換算。
実現は約定時点、含みは評価時点のレート (近似)。スワップは価格損益と分離し、
swap_base = Σ (保有数量 × mid の円換算 × 経過日数) から d シナリオで換算する
(実際の d はペアごとに異なり、CHF クロスの金利差は USD_JPY より大きい傾向があるため
一律 d はクロス側に保守的)。
年は JST 暦年、毎年フラット開始、年末は bid で時価評価。M1 内順序は近似。
"""
import sqlite3
import sys
import numpy as np
from datetime import datetime, timezone, timedelta

DB = "/home/hatch/workspace/oanda_json/m1.db"
JST = timezone(timedelta(hours=9))
PAIRS = {"USD_JPY": 0.01, "GBP_CHF": 0.0001, "AUD_CHF": 0.0001, "USD_CHF": 0.0001}
VOL = 250
MAX_POS = 40
STEP = 10
TRAIL_START = 50
TRAIL_STEP = 10
MAX_UNITS = VOL * MAX_POS  # 10,000


def load_year(year, inst):
    t0 = int(datetime(year, 1, 1, tzinfo=JST).timestamp())
    t1 = int(datetime(year + 1, 1, 1, tzinfo=JST).timestamp())
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t",
        (inst, t0, t1)).fetchall()
    con.close()
    return np.array(rows, dtype=np.float64)


def fx_series(pair_bars, chf_bars):
    """pair の各バー時刻における quote→JPY レート (USD_JPY は 1)。"""
    if chf_bars is None:
        return np.ones(len(pair_bars))
    mid = (chf_bars[:, 4] + chf_bars[:, 8]) / 2.0
    idx = np.searchsorted(chf_bars[:, 0], pair_bars[:, 0], side="right") - 1
    idx = np.clip(idx, 0, len(mid) - 1)
    return mid[idx]


def dates_of(bars):
    return np.array([datetime.fromtimestamp(x, JST).date() for x in bars[:, 0]])


def sim_dca(bars, pip, fx, dates, trail_start=TRAIL_START):
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ah, ac = bars[:, 5], bars[:, 6], bars[:, 8]
    entry = np.empty(0)
    peak = np.empty(0)
    extreme = bl[0]
    realized_j = 0.0
    exits = entries = 0
    worst = 0.0
    swap_base = 0.0
    units_time = 0.0
    prev_date, prev_mid, prev_u = dates[0], (bc[0] + ac[0]) / 2.0, 0
    for i in range(len(bars)):
        # ---- exits ----
        if entry.size:
            peak = np.maximum(peak, bh[i])
            armed = (peak - entry) / pip >= trail_start
            if armed.any():
                stop = peak - TRAIL_STEP * pip
                hit = armed & (bl[i] <= stop)
                if hit.any():
                    fill = np.where(bo[i] > stop, stop, bo[i])
                    realized_j += float(np.sum((fill[hit] - entry[hit]) * VOL)) * fx[i]
                    exits += int(hit.sum())
                    keep = ~hit
                    entry, peak = entry[keep], peak[keep]
        # ---- entry ----
        if bl[i] < extreme:
            extreme = bl[i]
        if entry.size < MAX_POS:
            anchor_ok = True
            if entry.size:
                anchor_ok = extreme <= entry.min() - STEP * pip - 1e-12
            if anchor_ok and np.isfinite(extreme):
                trig = extreme + STEP * pip
                if ah[i] >= trig:
                    fill = max(trig, ao[i])
                    entry = np.append(entry, fill)
                    peak = np.append(peak, bh[i])
                    entries += 1
                    extreme = bl[i]
        # ---- marks / swap ----
        units = entry.size * VOL
        units_time += units
        if entry.size:
            mtm = realized_j + float(np.sum((bc[i] - entry) * VOL)) * fx[i]
        else:
            mtm = realized_j
        if mtm < worst:
            worst = mtm
        if dates[i] != prev_date:
            gap = (dates[i] - prev_date).days
            if gap > 0:
                swap_base += prev_u * prev_mid * fx[i] * gap
            prev_date = dates[i]
        prev_mid = (bc[i] + ac[i]) / 2.0
        prev_u = units
    end_float_j = float(np.sum((bc[-1] - entry) * VOL)) * fx[-1] if entry.size else 0.0
    return {"realized": realized_j, "end_float": end_float_j, "price": realized_j + end_float_j,
            "worst": worst, "exits": exits, "entries": entries, "end_open": int(entry.size),
            "avg_units": units_time / len(bars), "swapbase": swap_base}


def sim_hold(bars, fx, dates):
    bc, ac, ao = bars[:, 4], bars[:, 8], bars[:, 5]
    basis = MAX_UNITS * ao[0]
    worst = 0.0
    swap_base = 0.0
    prev_date, prev_mid = dates[0], (bc[0] + ac[0]) / 2.0
    for i in range(len(bars)):
        m = (MAX_UNITS * bc[i] - basis) * (fx[i] if fx is not None else 1.0)
        if m < worst:
            worst = m
        if dates[i] != prev_date:
            gap = (dates[i] - prev_date).days
            if gap > 0:
                swap_base += MAX_UNITS * prev_mid * fx[i] * gap
            prev_date = dates[i]
        prev_mid = (bc[i] + ac[i]) / 2.0
    price = (MAX_UNITS * bc[-1] - basis) * fx[-1]
    return {"realized": 0.0, "end_float": price, "price": price, "worst": worst,
            "exits": 0, "entries": 1, "end_open": MAX_POS, "avg_units": float(MAX_UNITS),
            "swapbase": swap_base}


def sim_blast(bars, pip, fx, dates):
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ac = bars[:, 5], bars[:, 8]
    spread = ac - bc
    step = 100 * pip
    tr_units = MAX_UNITS // 10
    ub = tr_units
    basis = ub * ao[0]
    last_entry = ao[0]
    tr = 1
    realized_j = 0.0
    exits = adds = 0
    worst = 0.0
    swap_base = 0.0
    units_time = 0.0
    prev_date, prev_mid, prev_u = dates[0], (bc[0] + ac[0]) / 2.0, ub
    for i in range(len(bars)):
        if ub > 0:
            avg = basis / ub
            hit_exit = bh[i] >= last_entry + step
            hit_add = bl[i] <= avg - step
            if hit_exit:
                fill = max(last_entry + step, bo[i])
                realized_j += (ub * fill - basis) * fx[i]
                exits += 1
                ask_fill = fill + spread[i]
                ub = tr_units
                basis = ub * ask_fill
                last_entry = ask_fill
                tr = 1
            elif hit_add:
                while tr < 10 and bl[i] <= basis / ub - step:
                    trig = basis / ub - step
                    ask_fill = trig + spread[i]
                    ub += tr_units
                    basis += tr_units * ask_fill
                    last_entry = ask_fill
                    tr += 1
                    adds += 1
        mtm = realized_j + (ub * bc[i] - basis) * fx[i]
        if mtm < worst:
            worst = mtm
        units_time += ub
        if dates[i] != prev_date:
            gap = (dates[i] - prev_date).days
            if gap > 0:
                swap_base += prev_u * prev_mid * fx[i] * gap
            prev_date = dates[i]
        prev_mid = (bc[i] + ac[i]) / 2.0
        prev_u = ub
    end_float_j = (ub * bc[-1] - basis) * fx[-1]
    return {"realized": realized_j, "end_float": end_float_j, "price": realized_j + end_float_j,
            "worst": worst, "exits": exits, "entries": adds + 1, "end_open": tr,
            "avg_units": units_time / len(bars), "swapbase": swap_base}


def main():
    results = []  # (year, pair, partial, {strat: res})
    for year in range(2015, 2027):
        chf = load_year(year, "CHF_JPY")
        for pair, pip in PAIRS.items():
            bars = load_year(year, pair)
            if len(bars) < 50000:
                continue
            fx = fx_series(bars, None if pair == "USD_JPY" else chf)
            dates = dates_of(bars)
            res = {"dca": sim_dca(bars, pip, fx, dates),
                   "hold": sim_hold(bars, fx, dates),
                   "blast": sim_blast(bars, pip, fx, dates)}
            results.append((year, pair, (year == 2026) or len(bars) < 300000, res))
        print(f"... {year} done", file=sys.stderr)

    def tot(s, d):
        return s["price"] + s["swapbase"] * d / 365.0

    print("trailing_dca 再現 vs A-hold vs B-last (最大10,000単位で正規化, 円換算, 合計は d=2% スワップ込み)")
    print()
    for year, pair, partial, res in results:
        tag = " (途中)" if partial else ""
        print(f"{year}{tag} {pair}: DCA 価格 {res['dca']['price']:+10,.0f} 合計d2 {tot(res['dca'], 0.02):+10,.0f} 最悪 {res['dca']['worst']:+10,.0f} 決済{res['dca']['exits']:4} 末{res['dca']['end_open']:2}本"
              f" | HOLD {tot(res['hold'], 0.02):+10,.0f} (価格 {res['hold']['price']:+10,.0f}) | B-last {tot(res['blast'], 0.02):+10,.0f} (価格 {res['blast']['price']:+10,.0f})")
    print()
    full = [(y, p, r) for (y, p, part, r) in results if not part]
    print(f"== 完全年 {len(set(y for y,_,_ in full))} 年・4ペア合計 ==")
    for d in (0.0, 0.02):
        td = sum(tot(r["dca"], d) for _, _, r in full)
        th = sum(tot(r["hold"], d) for _, _, r in full)
        tb = sum(tot(r["blast"], d) for _, _, r in full)
        print(f"d={d*100:.0f}%: DCA {td:+,.0f} | HOLD {th:+,.0f} | B-last {tb:+,.0f}")
    print()
    for pair in PAIRS:
        sub = [(y, r) for (y, p, r) in full if p == pair]
        td = sum(tot(r["dca"], 0.02) for _, r in sub); th = sum(tot(r["hold"], 0.02) for _, r in sub); tb = sum(tot(r["blast"], 0.02) for _, r in sub)
        wins = sum(1 for _, r in sub if tot(r["dca"], 0.02) >= max(tot(r["hold"], 0.02), tot(r["blast"], 0.02)))
        print(f"{pair}: DCA {td:+,.0f} | HOLD {th:+,.0f} | B-last {tb:+,.0f} | DCAが最良の年 {wins}/{len(sub)}"
              f" | DCA決済/年 {np.mean([r['dca']['exits'] for _, r in sub]):.0f} | DCA最悪含損 年平均 {np.mean([r['dca']['worst'] for _, r in sub]):,.0f} / 最深 {min(r['dca']['worst'] for _, r in sub):,.0f}")
    print()
    post = [(y, p, r) for (y, p, r) in full if y >= 2021]
    for d in (0.0, 0.02):
        td = sum(tot(r["dca"], d) for _, _, r in post); th = sum(tot(r["hold"], d) for _, _, r in post); tb = sum(tot(r["blast"], d) for _, _, r in post)
        print(f"2021-25 d={d*100:.0f}%: DCA {td:+,.0f} | HOLD {th:+,.0f} | B-last {tb:+,.0f}")


if __name__ == "__main__":
    main()
