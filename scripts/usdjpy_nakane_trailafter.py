"""
仲値後トレーリングの検証: 09:55 固定決済の代わりに、09:54 からトレーリングを開始したらどうか

ユーザーの案 (2026-10-30): 「9:54からトレーリングストップを始める。仲値過ぎても上げてることもある」

定義:
- 対象: 5ペア、ロングのみ、exit 日が火〜金、2020-10〜2026-10
- エントリー: 00:00 ask 始値
- 木曜は従来どおり 05:35 固定決済 (スワップ回避が目的なので両案共通)
- 非木曜:
  - base: 09:55 bid 始値で決済
  - trail: 09:54 の時点でネイティブトレーリングを付けたものとして模す。
    初期の最大値は 09:54 バーの bid 始値。以後は bid 高値で最大を更新し、
    ストップ = 最大 - 距離。bid 安値がストップ以下で決済 (価格はストップ値)
  - 距離: 10pip / 20pip。上限 (強制決済): 11:00 / 15:00 の bid 始値
  - バー内順序は悲観 P (前バーまでの最大で判定→更新) / 楽観 O (更新→判定) の2通り
- 1 pip = 0.01 円。スワップなし。価格のみ
"""

import os
import sqlite3
from math import sqrt

import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PIP = 0.01
PAIRS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY"]
DISTS = [10.0, 20.0]
CAPS = [(11, 0), (15, 0)]


def pf(s):
    s = pd.Series(s, dtype=float)
    pos, neg = s[s > 0].sum(), -s[s < 0].sum()
    return pos / neg if neg > 0 else float("nan")


def line(name, s):
    s = pd.Series(s, dtype=float).dropna()
    if len(s) == 0:
        print(f"{name}: n=0")
        return
    t = s.mean() / (s.std(ddof=1) / sqrt(len(s))) if len(s) > 1 and s.std(ddof=1) > 0 else float("nan")
    print(f"{name}: n={len(s)} mean={s.mean():+.2f}pip PF={pf(s):.3f} win={100*(s>0).mean():.1f}% t={t:.2f}")


def sim_pair(con, inst):
    df = pd.read_sql_query(f"SELECT t,bo,bh,bl,ao FROM m1 WHERE inst='{inst}' ORDER BY t", con)
    bars = {int(t): (bo, bh, bl, ao) for t, bo, bh, bl, ao in zip(df.t, df.bo, df.bh, df.bl, df.ao)}
    jst_dates = sorted(set(pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.date))

    def ep(date, hh, mm):
        return int((pd.Timestamp(date) + pd.Timedelta(hours=hh, minutes=mm)).tz_localize("Asia/Tokyo").timestamp())

    rows = []
    for d in jst_dates:
        ts = pd.Timestamp(d)
        if ts.dayofweek not in (1, 2, 3, 4):
            continue
        te = ep(d, 0, 0)
        if te not in bars:
            continue
        entry = bars[te][3]
        if ts.dayofweek == 3:
            tx = ep(d, 5, 35)
            if tx not in bars:
                continue
            rows.append(dict(date=d, thu=True, base=(bars[tx][0] - entry) / PIP))
            continue
        tx955 = ep(d, 9, 55)
        t954 = ep(d, 9, 54)
        if tx955 not in bars or t954 not in bars:
            continue
        row = dict(date=d, thu=False, base=(bars[tx955][0] - entry) / PIP)
        for dist in DISTS:
            for cap in CAPS:
                tcap = ep(d, *cap)
                if tcap not in bars:
                    continue
                for v in ("P", "O"):
                    max_bid = bars[t954][0]  # bid open at attach
                    out = None
                    e = t954
                    while e < tcap:
                        if e not in bars:
                            e += 60
                            continue
                        bo, bh, bl, ao = bars[e]
                        if v == "P":
                            stop = max_bid - dist * PIP
                            if bl <= stop:
                                out = (stop - entry) / PIP
                                break
                            max_bid = max(max_bid, bh)
                        else:
                            max_bid = max(max_bid, bh)
                            stop = max_bid - dist * PIP
                            if bl <= stop:
                                out = (stop - entry) / PIP
                                break
                        e += 60
                    if out is None:
                        out = (bars[tcap][0] - entry) / PIP
                    row[f"trail{int(dist)}_cap{cap[0]}{v}"] = out
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for inst in PAIRS:
        r = sim_pair(con, inst)
        nth = r[~r.thu]
        print(f"\n===== {inst} (all n={len(r)}, non-Thu n={len(nth)}) =====")
        line("ALL base (Thu 05:35 / else 09:55 fixed)", r.base)
        line("non-Thu base 09:55 fixed", nth.base)
        for dist in DISTS:
            for cap in CAPS:
                for v in ("P", "O"):
                    col = f"trail{int(dist)}_cap{cap[0]}{v}"
                    if col in nth:
                        line(f"non-Thu trail d={int(dist)} cap {cap[0]:02d}:00 {v}", nth[col])
    con.close()
    print("\nDONE (M1 approximation; swap not included)")


if __name__ == "__main__":
    main()
