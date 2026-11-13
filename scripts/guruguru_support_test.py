#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
グルトレ (ぐるぐるトレイン, 川崎ドルえもん) 基本形の「サポート有無」検証。

問: 両建てグリッド (子本体) の逆行側含み損を、サポート (方向を持った
10倍の錨ポジション) が本当に打ち消すのか。サポート有/無だけを差し替えて、
月次ランの「全決済したときの純資産」で比較する。

定義 (開発者公式ページの基本形どおり, USD_JPY):
  - 子本体: 10pip 刻みの全レベルに買い/売り両方の IFD スロット (各1,000通貨)。
    レベルに価格が触れたら約定 (下から=指値/上から=逆指値の区別なく初回タッチで約定)。
    利確 +50pip で決済、損切りなし。決済されたスロットは翌バー以降に再約定可
    (リピート系サービスの自動再注文に相当)。
  - バンド: 開始時 mid ±3.00円。子本体レベルは 0.10 の絶対グリッド、
    サポートラインは 1.00 の絶対グリッド (バンド内のみ)。
  - サポート (LONG変種): バンド内の 1.00 ラインに価格が触れるたび買い 10,000通貨、
    ラン中は決済しない。SHORT変種はその鏡像。OFF変種は子本体のみ。
    ※ ラインより上への約定は逆指値として扱う (開発者の作例の損益計算と整合する
      解釈。指値のみなら上方ラインは約定しないため、サポートには甘めの前提)。
  - ラン: JST の暦月単位 (2015-02 〜 2026-10 の完全月)。開始時フラット、
    月末の最終バーの bid/ask で全ポジションを時価評価 (= 全決済したときの純資産)。
  - 子本体は3変種で共通 (サポートは子本体の挙動に干渉しない) ので1回だけ回し、
    サポート損益を別集計して合算する。

コスト/約定の前提 (m1.db の bid/ask バーで判定):
  - 買い約定は ask 側レンジ、売り約定は bid 側レンジでタッチ判定。
    約定価格はレベル価格 (バー open がレベルを飛び越えた場合は open 価格)。
  - 利確は指値として TP 価格で約定 (open が飛び越えた場合は open 価格)。
  - 約定したバー内では利確しない (同一バー内の順序不定への保守的扱い)。
  - 時価評価: ロングは bid 終値、ショートは ask 終値 (スプレッド込み)。
  - スワップは含まない (価格損益のみ)。証拠金ゲートは再現しない。
  - M1 内の値動き順序は近似のため絶対値は推定値。
"""
import sqlite3
import sys
import numpy as np
from datetime import datetime, timezone, timedelta

DB = "/home/hatch/workspace/oanda_json/m1.db"
INST = "USD_JPY"
PIP = 0.01
LEVEL_STEP = 0.10          # 子本体の刻み (10pip)
TP = 0.50                   # 子本体の利確幅 (50pip)
CHILD_UNITS = 1000
SUP_UNITS = 10000
BAND = 3.00                 # 開始 mid ±3円
JST = timezone(timedelta(hours=9))
EPS = 1e-9


def load_year(year):
    con = sqlite3.connect(DB)
    t0 = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    t1 = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    rows = con.execute(
        "SELECT t,bo,bh,bl,bc,ao,ah,al,ac FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t",
        (INST, t0, t1)).fetchall()
    con.close()
    return np.array(rows, dtype=np.float64)


def month_bounds(y, m):
    start = datetime(y, m, 1, tzinfo=JST)
    end = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=JST)
    return int(start.timestamp()), int(end.timestamp())


def run_month(bars):
    """bars: (n,9) t,bo,bh,bl,bc,ao,ah,al,ac. 1ラン分の結果 dict を返す。"""
    n = len(bars)
    bo, bh, bl, bc = bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    ao, ah, al, ac = bars[:, 5], bars[:, 6], bars[:, 7], bars[:, 8]

    s0 = (bc[0] + ac[0]) / 2.0
    lo_lvl = np.floor((s0 - BAND) / LEVEL_STEP) * LEVEL_STEP
    hi_lvl = np.ceil((s0 + BAND) / LEVEL_STEP) * LEVEL_STEP
    levels = np.round(np.arange(lo_lvl, hi_lvl + EPS, LEVEL_STEP), 2)
    nL = len(levels)
    L0 = levels[0]
    is_sup = np.abs(levels / 1.0 - np.round(levels / 1.0)) < 1e-6

    long_e = np.full(nL, np.nan)    # 子本体ロング建値
    short_e = np.full(nL, np.nan)   # 子本体ショート建値
    sup_l_n, sup_l_sum = 0, 0.0     # サポートロング 本数/建値合計
    sup_s_n, sup_s_sum = 0, 0.0
    sup_l_done = np.zeros(nL, bool)
    sup_s_done = np.zeros(nL, bool)

    realized = 0.0
    tp_count = 0
    fill_count = 0
    worst = {"off": 0.0, "long": 0.0, "short": 0.0}
    prev_bc, prev_ac = s0, s0  # 注文は開始価格 s0 に置かれる (バー0のギャップ約定判定用)

    def idx_range(lo, hi):
        a = int(np.ceil((lo - L0) / LEVEL_STEP - 1e-6))
        b = int(np.floor((hi - L0) / LEVEL_STEP + 1e-6))
        return max(a, 0), min(b, nL - 1)

    for i in range(n):
        blocked_l = np.zeros(nL, bool)
        blocked_s = np.zeros(nL, bool)
        # ---------- 利確 (前バー以前の建玉のみ) ----------
        m = ~np.isnan(long_e)
        if m.any():
            tp = long_e + TP
            hit = m & (bh[i] >= tp)
            if hit.any():
                fill = np.where(bo[i] > tp, bo[i], tp)
                realized += float(np.sum((fill[hit] - long_e[hit]) * CHILD_UNITS))
                tp_count += int(hit.sum())
                long_e[hit] = np.nan
                blocked_l |= hit
        m = ~np.isnan(short_e)
        if m.any():
            tp = short_e - TP
            hit = m & (al[i] <= tp)
            if hit.any():
                fill = np.where(ao[i] < tp, ao[i], tp)
                realized += float(np.sum((short_e[hit] - fill[hit]) * CHILD_UNITS))
                tp_count += int(hit.sum())
                short_e[hit] = np.nan
                blocked_s |= hit
        # ---------- 子本体エントリー ----------
        a, b = idx_range(min(al[i], prev_ac), max(ah[i], prev_ac))
        for k in range(a, b + 1):
            if np.isnan(long_e[k]) and not blocked_l[k]:
                long_e[k] = levels[k] if al[i] - EPS <= levels[k] <= ah[i] + EPS else ao[i]
                fill_count += 1
        a, b = idx_range(min(bl[i], prev_bc), max(bh[i], prev_bc))
        for k in range(a, b + 1):
            if np.isnan(short_e[k]) and not blocked_s[k]:
                short_e[k] = levels[k] if bl[i] - EPS <= levels[k] <= bh[i] + EPS else bo[i]
                fill_count += 1
        # ---------- サポート約定 ----------
        a, b = idx_range(min(al[i], prev_ac), max(ah[i], prev_ac))
        for k in range(a, b + 1):
            if is_sup[k] and not sup_l_done[k]:
                sup_l_done[k] = True
                sup_l_n += 1
                sup_l_sum += levels[k] if al[i] - EPS <= levels[k] <= ah[i] + EPS else ao[i]
        a, b = idx_range(min(bl[i], prev_bc), max(bh[i], prev_bc))
        for k in range(a, b + 1):
            if is_sup[k] and not sup_s_done[k]:
                sup_s_done[k] = True
                sup_s_n += 1
                sup_s_sum += levels[k] if bl[i] - EPS <= levels[k] <= bh[i] + EPS else bo[i]
        # ---------- 時価評価 ----------
        cf = 0.0
        m = ~np.isnan(long_e)
        if m.any():
            cf += float(np.sum((bc[i] - long_e[m]) * CHILD_UNITS))
        m = ~np.isnan(short_e)
        if m.any():
            cf += float(np.sum((short_e[m] - ac[i]) * CHILD_UNITS))
        sl = SUP_UNITS * (sup_l_n * bc[i] - sup_l_sum)
        ss = SUP_UNITS * (sup_s_sum - sup_s_n * ac[i])
        tot_off = realized + cf
        if tot_off < worst["off"]:
            worst["off"] = tot_off
        if tot_off + sl < worst["long"]:
            worst["long"] = tot_off + sl
        if tot_off + ss < worst["short"]:
            worst["short"] = tot_off + ss
        prev_bc, prev_ac = bc[i], ac[i]

    end_mid = (bc[-1] + ac[-1]) / 2.0
    return {
        "start_mid": s0, "disp": end_mid - s0,
        "realized": realized, "child_float": cf,
        "sup_l_n": sup_l_n, "sup_l_pnl": sl,
        "sup_s_n": sup_s_n, "sup_s_pnl": ss,
        "net_off": tot_off, "net_long": tot_off + sl, "net_short": tot_off + ss,
        "worst_off": worst["off"], "worst_long": worst["long"], "worst_short": worst["short"],
        "tp": tp_count, "fills": fill_count,
    }


def stats(rows, key):
    v = np.array([r[key] for r in rows])
    return (f"mean {v.mean():+9,.0f}  med {np.median(v):+9,.0f}  "
            f"pos {(v > 0).mean() * 100:4.1f}%  min {v.min():+10,.0f}  max {v.max():+10,.0f}")


def main():
    runs = []
    for year in range(2015, 2027):
        bars = load_year(year)
        if len(bars) == 0:
            continue
        t = bars[:, 0]
        for m in range(1, 13):
            if year == 2015 and m == 1:
                continue  # 2015-01 は月初が欠けるため除外 (完全月のみ)
            if year == 2026 and m > 10:
                continue  # データは 2026-11-10 まで
            a, b = month_bounds(year, m)
            sel = bars[(t >= a) & (t < b)]
            if len(sel) < 15000:
                continue
            r = run_month(sel)
            r["ym"] = f"{year}-{m:02d}"
            r["era"] = "POST" if (year, m) >= (2020, 10) else "PRE"
            runs.append(r)
        print(f"... {year} done, runs so far {len(runs)}", file=sys.stderr)

    print(f"グルトレ サポート有無検証 USD_JPY 月次ラン (完全月) n={len(runs)}")
    print(f"期間 {runs[0]['ym']} 〜 {runs[-1]['ym']} / バンド ±{BAND:.0f}円 / 子本体 10pip刻み・50pip利確・1,000通貨 / サポート 1円ごと10,000通貨")
    print()
    for era in ("ALL", "PRE", "POST"):
        sub = runs if era == "ALL" else [r for r in runs if r["era"] == era]
        print(f"== {era} (n={len(sub)}) 全決済時ネット (円) ==")
        print("  OFF   :", stats(sub, "net_off"))
        print("  SUP-L :", stats(sub, "net_long"))
        print("  SUP-S :", stats(sub, "net_short"))
        print(f"  子本体: 実現 {np.mean([r['realized'] for r in sub]):+,.0f} / 終了時含み {np.mean([r['child_float'] for r in sub]):+,.0f} / 利確回数 平均 {np.mean([r['tp'] for r in sub]):.0f}")
        print(f"  ラン中 最悪時価 (平均/最深): OFF {np.mean([r['worst_off'] for r in sub]):,.0f} / {np.min([r['worst_off'] for r in sub]):,.0f}"
              f" | SUP-L {np.mean([r['worst_long'] for r in sub]):,.0f} / {np.min([r['worst_long'] for r in sub]):,.0f}"
              f" | SUP-S {np.mean([r['worst_short'] for r in sub]):,.0f} / {np.min([r['worst_short'] for r in sub]):,.0f}")
        print()
    print("== 月の値動き別 (ALL): 終了時ネット平均 (円) ==")
    bins = [("上昇月 Δ>=+0.5円", lambda d: d >= 0.5), ("レンジ月 |Δ|<0.5円", lambda d: abs(d) < 0.5), ("下落月 Δ<=-0.5円", lambda d: d <= -0.5)]
    for name, f in bins:
        sub = [r for r in runs if f(r["disp"])]
        if not sub:
            continue
        print(f"  {name} (n={len(sub)}): OFF {np.mean([r['net_off'] for r in sub]):+,.0f} | SUP-L {np.mean([r['net_long'] for r in sub]):+,.0f} | SUP-S {np.mean([r['net_short'] for r in sub]):+,.0f}")
    print()
    print("== ワースト5ラン (SUP-L の全決済時ネット) ==")
    for r in sorted(runs, key=lambda x: x["net_long"])[:5]:
        print(f"  {r['ym']} Δ{r['disp']:+.2f}円 OFF {r['net_off']:+,.0f} SUP-L {r['net_long']:+,.0f} (サポ {r['sup_l_n']}本 {r['sup_l_pnl']:+,.0f}) SUP-S {r['net_short']:+,.0f} ラン中最悪 SUP-L {r['worst_long']:+,.0f}")
    print("== ベスト5ラン (SUP-L) ==")
    for r in sorted(runs, key=lambda x: x["net_long"])[-5:]:
        print(f"  {r['ym']} Δ{r['disp']:+.2f}円 OFF {r['net_off']:+,.0f} SUP-L {r['net_long']:+,.0f} (サポ {r['sup_l_n']}本 {r['sup_l_pnl']:+,.0f}) SUP-S {r['net_short']:+,.0f} ラン中最悪 SUP-L {r['worst_long']:+,.0f}")


if __name__ == "__main__":
    main()
