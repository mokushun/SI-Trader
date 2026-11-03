"""
仲値ロングへのトレーリングストップ (60, 10) の効果検証

背景: 実売買コード nakane_oanda.py は毎分 manage_trailing_stop(60, 10) を呼ぶ。
2026-10-30 に oanda_API.py のセマンティクスが確定した:
  「mid 基準の含み益が 60pip 以上になった建玉に、OANDA ネイティブの
    TRAILING_STOP_LOSS (距離 10pip) を一度だけ付ける」
本スクリプトはこの仕様を m1.db の M1 バーで近似シミュレーションし、
トレーリング無しの現行測定 (00:00 IN -> 09:55 OUT, 木曜 05:35) と比較する。

定義:
- 対象: 5ペア (USD_JPY, EUR_JPY, GBP_JPY, AUD_JPY, CHF_JPY)、ロングのみ、
  exit 日が火〜金、2020-10〜2026-10 (DB の範囲)
- エントリー: 00:00 バーの ask 始値。予定決済: 09:55 (木曜 05:35) の bid 始値
- 含み益 (mid基準) = そのバーの mid 高値 - エントリー価格 (ask)
  mid 高値は (bid高値 + ask高値) / 2 で近似 (bid/ask の高値の同時性は M1 では不明)
- 含み益が初めて 60pip 以上になったバーを「発動バー」とし、その次のバーから
  ネイティブトレーリングを模す: ストップ = 発動以降の bid 高値の最大 - 10pip。
  bid 安値がストップ以下になったらストップ価格で決済したものとする
- バー内の順序 (高値更新とストップ到達の前後) は M1 では判別できないため2通り:
  P (悲観): 前バーまでの最大で作ったストップで当バーの安値を判定 → その後 高値を更新
  O (楽観): 当バーの高値で最大を更新してから、そのストップで当バーの安値を判定
- 1 pip = 0.01 円。スワップなし。価格のみ
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 0.01  # 価格単位
ACT = 60.0  # 発動 (pip)
DIST = 10.0  # トレーリング距離 (pip)
PAIRS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY"]


def pf(s):
    s = pd.Series(s, dtype=float)
    pos, neg = s[s > 0].sum(), -s[s < 0].sum()
    return pos / neg if neg > 0 else float("nan")


def stats_line(name, s):
    s = pd.Series(s, dtype=float).dropna()
    if len(s) == 0:
        print(f"{name}: n=0")
        return
    t = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    print(f"{name}: n={len(s)} mean={s.mean():+.2f}pip PF={pf(s):.3f} win={100*(s>0).mean():.1f}% t={t:.2f}")


def sim_pair(con, inst):
    df = pd.read_sql_query(
        f"SELECT t,bo,bh,bl,ao,ah FROM m1 WHERE inst='{inst}' ORDER BY t", con
    )
    bars = {int(t): (bo, bh, bl, ao, ah) for t, bo, bh, bl, ao, ah in zip(df.t, df.bo, df.bh, df.bl, df.ao, df.ah)}
    jst_dates = sorted(set(pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.date))

    def ep(date, hh, mm):
        return int((pd.Timestamp(date) + pd.Timedelta(hours=hh, minutes=mm)).tz_localize("Asia/Tokyo").timestamp())

    rows = []
    for d in jst_dates:
        ts = pd.Timestamp(d)
        if ts.dayofweek not in (1, 2, 3, 4):
            continue
        te = ep(d, 0, 0)
        tx = ep(d, 5, 35) if ts.dayofweek == 3 else ep(d, 9, 55)
        if te not in bars or tx not in bars:
            continue
        entry = bars[te][3]  # ask open
        base = (bars[tx][0] - entry) / PIP

        # walk bars entry+1 .. exit
        armed = False
        arm_bar = None
        max_bid = -1e18
        res = {}
        for variant in ("P", "O"):
            res[variant] = None
        state = {v: dict(armed=False, max_bid=-1e18, done=False) for v in ("P", "O")}
        e = te + 60
        while e < tx:
            if e not in bars:
                e += 60
                continue
            bo, bh, bl, ao, ah = bars[e]
            mid_high = (bh + ah) / 2.0
            for v in ("P", "O"):
                st = state[v]
                if st["done"]:
                    continue
                if not st["armed"]:
                    if (mid_high - entry) / PIP >= ACT:
                        st["armed"] = True
                        st["max_bid"] = bh
                    continue
                if v == "P":
                    stop = st["max_bid"] - DIST * PIP
                    if bl <= stop:
                        res[v] = (stop - entry) / PIP
                        st["done"] = True
                    else:
                        st["max_bid"] = max(st["max_bid"], bh)
                else:  # O
                    st["max_bid"] = max(st["max_bid"], bh)
                    stop = st["max_bid"] - DIST * PIP
                    if bl <= stop:
                        res[v] = (stop - entry) / PIP
                        st["done"] = True
            e += 60
        rows.append(
            dict(
                date=d,
                gotobi=(ts.day % 5 == 0),
                base=base,
                trailP=res["P"] if res["P"] is not None else base,
                trailO=res["O"] if res["O"] is not None else base,
                armed=state["P"]["armed"],
                stoppedP=res["P"] is not None,
                stoppedO=res["O"] is not None,
            )
        )
    return pd.DataFrame(rows)


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for inst in PAIRS:
        r = sim_pair(con, inst)
        print(f"\n===== {inst} (n={len(r)}) =====")
        stats_line("no trailing (base)", r.base)
        stats_line("trailing P (pessimistic order)", r.trailP)
        stats_line("trailing O (optimistic order)", r.trailO)
        print(f"armed rate: {100*r.armed.mean():.1f}%  stopped-before-exit: P {100*r.stoppedP.mean():.1f}% / O {100*r.stoppedO.mean():.1f}%")
        both = r[r.armed]
        if len(both):
            print(f"among armed trades (n={len(both)}): base mean={both.base.mean():+.2f}  trailP mean={both.trailP.mean():+.2f}  trailO mean={both.trailO.mean():+.2f}")
        g = r[r.gotobi]
        if len(g):
            print(f"gotobi (n={len(g)}): base mean={g.base.mean():+.2f} PF={pf(g.base):.3f} | trailP mean={g.trailP.mean():+.2f} PF={pf(g.trailP):.3f} | armed {100*g.armed.mean():.0f}%")
    con.close()
    print("\nDONE (approximation on M1 bars; swap not included)")


if __name__ == "__main__":
    main()
