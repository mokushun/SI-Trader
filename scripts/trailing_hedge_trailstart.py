#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trailing_hedge (HEDGE_REPEAT, EUR_GBP) の TRAIL_START 感度テスト。

実ボット (trailing_hedge.py) のロジックを m1.db の M1 バー (bid/ask) で再現:
  - エントリー: 片側ごとに極値 (買い=最安値 / 売り=最高値) を追い、STEP(10pip)
    戻したところの逆指値で約定。追加建ては最安/最高の建値から STEP 以上
    離れた新極値 + STEP の戻しが必要。バンド: 買い trigger < 0.87、
    売り trigger > 0.83。本数上限 long 60 / short 100、1本 150 単位。
  - 決済: 含み益が TRAIL_START に達したらトレーリング開始、最高値から
    TRAIL_STEP(10pip) 逆行で決済 (modify_to_trailing_stop 相当)。
  - 証拠金ゲート (口座全体 500%) は再現しない (エントリーが止まる分、
    実運用はこれより大人しい)。開始時はフラット。
TRAIL_START だけを変えて、回転 (決済回数) と捕捉益・最大含み損を比較する。
M1 内の値動き順序は近似 (バーの高安で判定) のため絶対値は推定値。
"""
import sqlite3
import sys
import numpy as np

DB = "/home/hatch/workspace/oanda_json/m1.db"
INST = "EUR_GBP"
PIP = 0.0001
STEP = 10
TRAIL_STEP = 10
VOLUME = 150
LONG_MAX = 60
SHORT_MAX = 100
BUY_MAX = 0.87
SELL_MIN = 0.83


def load_bars_simple(start_iso):
    """m1 は (inst,t,bo,bh,bl,bc,ao,ah,al,ac) の1行形式。"""
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(m1)")]
    need = ["t", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"]
    if not all(c in cols for c in need):
        print("unexpected schema:", cols)
        sys.exit(1)
    q = f"SELECT {','.join(need)} FROM m1 WHERE inst=? AND t>=strftime('%s',?) ORDER BY t"
    rows = con.execute(q, (INST, start_iso)).fetchall()
    return np.array(rows, dtype=np.float64)


def run(bars, trail_start):
    n = len(bars)
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ah, al, ac = bars[:, 5], bars[:, 6], bars[:, 7], bars[:, 8]

    # long side state
    l_entry = np.empty(0)
    l_peak = np.empty(0)
    l_extreme = np.inf       # running lowest bid
    # short side state
    s_entry = np.empty(0)
    s_peak = np.empty(0)
    s_extreme = -np.inf      # running highest ask

    realized = 0.0           # pip-tranches
    exits = 0
    entries = 0
    worst_float = 0.0
    max_open = 0

    for i in range(n):
        # ---------- exits ----------
        if l_entry.size:
            l_peak = np.maximum(l_peak, bh[i])
            armed = (l_peak - l_entry) / PIP >= trail_start
            if armed.any():
                stop = l_peak - TRAIL_STEP * PIP
                hit = armed & (bl[i] <= stop)
                if hit.any():
                    fill = np.where(bo[i] > stop, stop, bo[i])
                    realized += float(np.sum((fill[hit] - l_entry[hit]) / PIP))
                    exits += int(hit.sum())
                    keep = ~hit
                    l_entry, l_peak = l_entry[keep], l_peak[keep]
        if s_entry.size:
            s_peak = np.minimum(s_peak, al[i])
            armed = (s_entry - s_peak) / PIP >= trail_start
            if armed.any():
                stop = s_peak + TRAIL_STEP * PIP
                hit = armed & (ah[i] >= stop)
                if hit.any():
                    fill = np.where(ao[i] < stop, stop, ao[i])
                    realized += float(np.sum((s_entry[hit] - fill[hit]) / PIP))
                    exits += int(hit.sum())
                    keep = ~hit
                    s_entry, s_peak = s_entry[keep], s_peak[keep]

        # ---------- entries: long ----------
        if bl[i] < l_extreme:
            l_extreme = bl[i]
        if l_entry.size < LONG_MAX:
            anchor_ok = True
            if l_entry.size:
                anchor_ok = l_extreme <= l_entry.min() - STEP * PIP - 1e-12
            if anchor_ok and np.isfinite(l_extreme):
                trig = l_extreme + STEP * PIP
                if trig < BUY_MAX and ah[i] >= trig:
                    fill = max(trig, ao[i])
                    if fill < BUY_MAX:
                        l_entry = np.append(l_entry, fill)
                        l_peak = np.append(l_peak, bh[i])
                        entries += 1
                        l_extreme = bl[i]
        # ---------- entries: short ----------
        if ah[i] > s_extreme:
            s_extreme = ah[i]
        if s_entry.size < SHORT_MAX:
            anchor_ok = True
            if s_entry.size:
                anchor_ok = s_extreme >= s_entry.max() + STEP * PIP - 1e-12
            if anchor_ok and np.isfinite(s_extreme):
                trig = s_extreme - STEP * PIP
                if trig > SELL_MIN and bl[i] <= trig:
                    fill = min(trig, bo[i])
                    if fill > SELL_MIN:
                        s_entry = np.append(s_entry, fill)
                        s_peak = np.append(s_peak, al[i])
                        entries += 1
                        s_extreme = ah[i]

        # ---------- floating (bar close) ----------
        tot_open = l_entry.size + s_entry.size
        if tot_open > max_open:
            max_open = tot_open
        if tot_open:
            f = 0.0
            if l_entry.size:
                f += float(np.sum((bc[i] - l_entry) / PIP))
            if s_entry.size:
                f += float(np.sum((s_entry - ac[i]) / PIP))
            if f < worst_float:
                worst_float = f

    end_float = 0.0
    if l_entry.size:
        end_float += float(np.sum((bc[-1] - l_entry) / PIP))
    if s_entry.size:
        end_float += float(np.sum((s_entry - ac[-1]) / PIP))
    return {
        "trail_start": trail_start,
        "entries": entries,
        "exits": exits,
        "realized": realized,
        "avg_per_exit": (realized / exits if exits else 0.0),
        "worst_float": worst_float,
        "max_open": max_open,
        "end_open": int(l_entry.size + s_entry.size),
        "end_float": end_float,
    }


def main():
    windows = [("2026-01-01", [50, 40, 30, 25, 20]), ("2025-01-01", [50, 30, 25])]
    for start_iso, starts in windows:
        bars = load_bars_simple(start_iso)
        months = (bars[-1, 0] - bars[0, 0]) / (86400 * 30.44)
        print(f"\n=== window {start_iso} -> end ({len(bars):,} bars, {months:.1f} months) ===")
        print(f"{'START':>5} {'entries':>7} {'exits':>5} {'exits/mo':>8} {'realized':>10} "
              f"{'avg/exit':>8} {'worstFloat':>10} {'maxOpen':>7} {'endOpen':>7} {'endFloat':>9}")
        for ts in starts:
            r = run(bars, ts)
            print(f"{r['trail_start']:>5} {r['entries']:>7} {r['exits']:>5} "
                  f"{r['exits']/months:>8.1f} {r['realized']:>10.0f} {r['avg_per_exit']:>8.1f} "
                  f"{r['worst_float']:>10.0f} {r['max_open']:>7} {r['end_open']:>7} {r['end_float']:>9.0f}")


if __name__ == "__main__":
    main()
