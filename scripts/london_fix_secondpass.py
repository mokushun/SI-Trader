"""
London Fix 第2パス: (1) fade の正確な exec、(2) USD 共通因子の月末効果、(3) T-5->T の安定性

第1パスで出た3つの候補を潰しにいく:
- fade (pre30 逆張り) の gross は spread とほぼ同額だった → 符号で方向を決めた
  正確な exec (bid/ask) で測り直す。
- 月末の post30 は USD ペア全部で「USD 高」方向に揃った → 5ペアを USD 換算して
  平均した共通因子で測ればノイズが減る。
- T-5->T の mid が全ペア正だった → 前後半で割る。
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = {"GBP_USD": 10000.0, "EUR_USD": 10000.0, "USD_JPY": 100.0,
         "EUR_GBP": 10000.0, "GBP_JPY": 100.0, "AUD_USD": 10000.0, "USD_CHF": 10000.0}
# USD 因子の符号: そのペアの LONG リターンが USD 高を意味するなら -1
USD_SIGN = {"GBP_USD": -1, "EUR_USD": -1, "USD_JPY": +1, "AUD_USD": -1, "USD_CHF": +1}


def pf(s):
    s = pd.Series(s, dtype=float)
    pos, neg = s[s > 0].sum(), -s[s < 0].sum()
    return pos / neg if neg > 0 else float("nan")


def stats(s):
    s = pd.Series(s, dtype=float).dropna()
    if len(s) < 3:
        return dict(n=len(s), mean=float("nan"), pf=float("nan"), t=float("nan"))
    sd = s.std(ddof=1)
    return dict(n=len(s), mean=s.mean(), pf=pf(s),
                t=s.mean() / (sd / sqrt(len(s))) if sd > 0 else float("nan"))


def fmt(name, st):
    print(f"  {name:<34} n={st['n']:>5} mean={st['mean']:+7.2f}pip PF={st['pf']:.3f} t={st['t']:+.2f}")


def load(inst):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql_query(f"SELECT t,bo,ao FROM m1 WHERE inst='{inst}' ORDER BY t", con)
    con.close()
    price = {int(t): (bo, ao) for t, bo, ao in zip(df.t, df.bo, df.ao)}
    london = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Europe/London")
    fix = {}
    for d in sorted(set(london.dt.date)):
        if pd.Timestamp(d).dayofweek > 4:
            continue
        fix[d] = int((pd.Timestamp(d) + pd.Timedelta(hours=16)).tz_localize("Europe/London").timestamp())
    return price, fix


def main():
    usd_days = {}   # date -> list of (pre_usd_bp, post_usd_bp) in bp (log-ish, price-normalized)
    for inst, pip in PAIRS.items():
        price, fix = load(inst)
        dates = sorted(fix)
        # month-end flag
        s = pd.DataFrame({"d": dates})
        s["ym"] = s["d"].apply(lambda d: (d.year, d.month))
        last = s.groupby("ym")["d"].transform("max")
        month_end = dict(zip(s["d"], (s["d"] == last).values))

        def at(d, off):
            return price.get(fix[d] + off * 60)

        rows = []
        for d in dates:
            p_m30, p_0, p_p30, p_m5 = at(d, -30), at(d, 0), at(d, 30), at(d, -5)
            if None in (p_m30, p_0, p_p30, p_m5):
                continue
            mid = lambda p: (p[0] + p[1]) / 2
            pre_mid = (mid(p_0) - mid(p_m30)) * pip
            post_mid = (mid(p_p30) - mid(p_0)) * pip
            # exact fade exec: pre>0 -> SHORT (bid in / ask out); pre<0 -> LONG
            if pre_mid > 0:
                fade_exec = (p_0[0] - p_p30[1]) * pip
            elif pre_mid < 0:
                fade_exec = (p_p30[0] - p_0[1]) * pip
            else:
                fade_exec = 0.0
            fade_mid = -np.sign(pre_mid) * post_mid
            rows.append(dict(date=d, year=d.year, me=bool(month_end[d]),
                             qe=bool(month_end[d]) and d.month in (3, 6, 9, 12),
                             pre_mid=pre_mid, post_mid=post_mid,
                             fade_mid=fade_mid, fade_exec=fade_exec,
                             t5_mid=(mid(p_0) - mid(p_m5)) * pip))
            if inst in USD_SIGN:
                lvl = mid(p_0)
                usd_days.setdefault(d, dict(me=bool(month_end[d]),
                                            qe=bool(month_end[d]) and d.month in (3, 6, 9, 12),
                                            year=d.year, pre=[], post=[]))
                # bp換算: 価格変化 = pip値 / pip倍率。それを水準で割り 10000倍。
                # (JPYペアとその他で pip サイズが違うため倍率で割るのが正しい)
                usd_days[d]["pre"].append(USD_SIGN[inst] * pre_mid / pip / lvl * 10000)
                usd_days[d]["post"].append(USD_SIGN[inst] * post_mid / pip / lvl * 10000)
        R = pd.DataFrame(rows)
        thr = R.pre_mid.abs().quantile(0.75)
        big = R[R.pre_mid.abs() >= thr]
        print(f"\n=== {inst} fade exact (n={len(R)}, big thr={thr:.1f}pip) ===")
        fmt("FADE30 mid", stats(R.fade_mid))
        fmt("FADE30 exec (exact)", stats(R.fade_exec))
        fmt("FADE30 big mid", stats(big.fade_mid))
        fmt("FADE30 big exec (exact)", stats(big.fade_exec))
        fmt("FADE30 month-end exec", stats(R.loc[R.me, "fade_exec"]))
        fmt("T-5->T mid all", stats(R.t5_mid))
        fmt("T-5->T mid <=2023", stats(R.loc[R.year <= 2023, "t5_mid"]))
        fmt("T-5->T mid 2024+", stats(R.loc[R.year >= 2024, "t5_mid"]))

    # USD composite
    recs = []
    for d, v in sorted(usd_days.items()):
        if len(v["pre"]) < 4:
            continue
        recs.append(dict(date=d, year=v["year"], me=v["me"], qe=v["qe"],
                         pre_usd=np.mean(v["pre"]), post_usd=np.mean(v["post"])))
    U = pd.DataFrame(recs)
    print(f"\n=== USD composite (5 pairs avg, bp) n={len(U)} ===")
    fmt("pre30 USD all", stats(U.pre_usd))
    fmt("post30 USD all", stats(U.post_usd))
    fmt("pre30 USD month-end", stats(U.loc[U.me, "pre_usd"]))
    fmt("pre30 USD non-ME", stats(U.loc[~U.me, "pre_usd"]))
    fmt("post30 USD month-end", stats(U.loc[U.me, "post_usd"]))
    fmt("post30 USD non-ME", stats(U.loc[~U.me, "post_usd"]))
    fmt("post30 USD quarter-end", stats(U.loc[U.qe, "post_usd"]))
    fmt("pre30 USD quarter-end", stats(U.loc[U.qe, "pre_usd"]))
    # month-end trade idea: at T, buy USD (post direction), exact cost not in composite (mid only)
    # split-half for ME post
    fmt("post30 USD ME <=2023", stats(U.loc[U.me & (U.year <= 2023), "post_usd"]))
    fmt("post30 USD ME 2024+", stats(U.loc[U.me & (U.year >= 2024), "post_usd"]))
    # ME の pre->post 合算 (T-30 に USD を売り T で買い戻し+買い増し相当の単純形: -pre + post)
    me = U[U.me]
    fmt("ME (-pre+post) USD", stats(-me.pre_usd + me.post_usd))
    print("\nDONE")


if __name__ == "__main__":
    main()
