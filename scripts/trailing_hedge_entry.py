#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trailing_hedge (HEDGE_REPEAT, EUR_GBP) のエントリー側感度テスト。
2026-11-07 の TRAIL_START 感度の続編。ユーザーの問い:
「ピラミッド型に積みますと詰むよね」— 益出し (TRAIL_START) を下げても
最悪含み損は -18k〜-20k pip×本数で変わらず、本数は上限160に張り付く。
詰まりを軽くするには入口側 (STEP・上限・テーパー) を変えるしかない。
実ボット (trailing_hedge.py) のロジックを m1.db の M1 バー (bid/ask) で再現し、
以下を比較する。TRAIL_START 感度テストと同一のシミュレータ構造で、
STEP / 上限 / テーパーだけを変える:
  - BASE:      STEP 10, caps long 60 / short 100, TS 50 (現行)
  - STEP20:    STEP 20, caps 60/100, TS 50
  - CAP_HALF:  STEP 10, caps long 30 / short 50, TS 50
  - TAPER:     STEP 10 (<20本) -> 20 (20-39本) -> 30 (40本以上), caps 60/100, TS 50
  - STEP20+TS25: STEP 20, caps 60/100, TS 25 (回転と軽量化の組合せ)
  - CAP_HALF+TS25: STEP 10, caps 30/50, TS 25
証拠金ゲート (口座全体500%) は再現しない。開始時フラット。M1 内の値動き
順序は近似のため絶対値は推定値。スワップは含まない (価格のみ)。
窓: 2026-01-01〜 (10.1か月、現行の細いレジーム)。
"""
import sqlite3
import sys
import numpy as np

DB = "/home/hatch/workspace/oanda_json/m1.db"
INST = "EUR_GBP"
PIP = 0.0001
TRAIL_STEP = 10
VOLUME = 150
BUY_MAX = 0.87
SELL_MIN = 0.83


def load_bars_simple(start_iso):
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(m1)")]
    need = ["t", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"]
    if not all(c in cols for c in need):
        print("unexpected schema:", cols)
        sys.exit(1)
    q = f"SELECT {','.join(need)} FROM m1 WHERE inst=? AND t>=strftime('%s',?) ORDER BY t"
    rows = con.execute(q, (INST, start_iso)).fetchall()
    return np.array(rows, dtype=np.float64)


def step_for(count, mode):
    if mode == "taper":
        if count < 20:
            return 10
        if count < 40:
            return 20
        return 30
    return mode  # int STEP


def run(bars, trail_start, step_mode, long_max, short_max):
    n = len(bars)
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ah, al, ac = bars[:, 5], bars[:, 6], bars[:, 7], bars[:, 8]

    l_entry = np.empty(0)
    l_peak = np.empty(0)
    l_extreme = np.inf
    s_entry = np.empty(0)
    s_peak = np.empty(0)
    s_extreme = -np.inf

    realized = 0.0
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
        if l_entry.size < long_max:
            st = step_for(int(l_entry.size), step_mode)
            anchor_ok = True
            if l_entry.size:
                anchor_ok = l_extreme <= l_entry.min() - st * PIP - 1e-12
            if anchor_ok and np.isfinite(l_extreme):
                trig = l_extreme + st * PIP
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
        if s_entry.size < short_max:
            st = step_for(int(s_entry.size), step_mode)
            anchor_ok = True
            if s_entry.size:
                anchor_ok = s_extreme >= s_entry.max() + st * PIP - 1e-12
            if anchor_ok and np.isfinite(s_extreme):
                trig = s_extreme - st * PIP
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
    variants = [
        ("BASE", 50, 10, 60, 100),
        ("STEP20", 50, 20, 60, 100),
        ("CAP_HALF", 50, 10, 30, 50),
        ("TAPER", 50, "taper", 60, 100),
        ("STEP20+TS25", 25, 20, 60, 100),
        ("CAP_HALF+TS25", 25, 10, 30, 50),
    ]
    bars = load_bars_simple("2026-01-01")
    months = (bars[-1, 0] - bars[0, 0]) / (86400 * 30.44)
    print(f"=== window 2026-01-01 -> end ({len(bars):,} bars, {months:.1f} months) ===")
    print(f"{'variant':>14} {'entries':>7} {'exits':>5} {'exits/mo':>8} {'realized':>10} "
          f"{'avg/exit':>8} {'worstFloat':>10} {'maxOpen':>7} {'endOpen':>7} {'endFloat':>9}")
    for name, ts, step_mode, lmax, smax in variants:
        r = run(bars, ts, step_mode, lmax, smax)
        print(f"{name:>14} {r['entries']:>7} {r['exits']:>5} "
              f"{r['exits']/months:>8.1f} {r['realized']:>10.0f} {r['avg_per_exit']:>8.1f} "
              f"{r['worst_float']:>10.0f} {r['max_open']:>7} {r['end_open']:>7} {r['end_float']:>9.0f}")


if __name__ == "__main__":
    main()
