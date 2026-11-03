"""
London Fix (WM/Reuters 16:00 London) 前後の歪み 第1パス

問い: ロンフィク前後に、仲値のような一方向の（無条件で取れる）歪み、
あるいは事前の動きを手がかりに取れる条件付きの歪みはあるか。

定義（固定）:
- データ: m1.db、読み取り専用。対象ペアは下記 PAIRS。
- フィックス時刻 T = 16:00 Europe/London（夏時間 BST / 冬 GMT を tz で正しく変換。
  JST では夏は翌0:00、冬は翌1:00）。対象はロンドン日付の月〜金。
- 約定価格は指定時刻の足の始値 (bo/ao)。仲値検証と同方式。
- mid = (bo+ao)/2。pip: JPYペア 100倍、その他 10000倍。
- 測定:
  1) 平均パス: T-60..T+60 の mid 累積変化（T-60 基準、5分刻み+直近詳細）
  2) 無条件トレード: Long の [T-120,T-60,T-30,T-15,T-5 -> T] と
     [T -> T+5,T+15,T+30,T+60,T+120] を exec（ask-in/bid-out）と mid gross で。
     Short は符号反転なので Long の負がそのまま上限（exec は別途スプレッド分不利）。
  3) 条件付きリバーサル: 事前ドリフト pre = mid(T)-mid(T-30) を観測してから
     T で逆張りする戦略 (fade) と順張り (follow) の post = mid(T+30)-mid(T) 損益。
     pre と post の相関、pre の符号別 post 平均も出す。
  4) 月末: ロンドン日付がその月の最終営業日（月〜金の最終日）の部分集合。
  5) 歪みの大きさ自体: |1分リターン| とスプレッドの T 周辺 vs 同日 14:00/18:00 対照。
- 前後半 split-half で再現性を見る。価格のみ、スワップ無し。
"""

import os
import sqlite3
from math import sqrt

import numpy as np
import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))

PAIRS = {
    "GBP_USD": 10000.0,
    "EUR_USD": 10000.0,
    "USD_JPY": 100.0,
    "EUR_GBP": 10000.0,
    "GBP_JPY": 100.0,
    "AUD_USD": 10000.0,
    "USD_CHF": 10000.0,
}

PRE_ENTRIES = [-120, -60, -30, -15, -5]
POST_EXITS = [5, 15, 30, 60, 120]
OFFSETS = sorted(set(PRE_ENTRIES + [0] + POST_EXITS + [-30, 30]))


def pf(s):
    s = pd.Series(s, dtype=float)
    pos, neg = s[s > 0].sum(), -s[s < 0].sum()
    return pos / neg if neg > 0 else float("nan")


def stats(s):
    s = pd.Series(s, dtype=float).dropna()
    if len(s) < 3:
        return dict(n=len(s), mean=float("nan"), pf=float("nan"), t=float("nan"), std=float("nan"))
    sd = s.std(ddof=1)
    return dict(n=len(s), mean=s.mean(), pf=pf(s),
                t=s.mean() / (sd / sqrt(len(s))) if sd > 0 else float("nan"), std=sd)


def fmt(name, st):
    print(f"  {name:<28} n={st['n']:>5} mean={st['mean']:+7.2f}pip PF={st['pf']:.3f} t={st['t']:+.2f} std={st['std']:.1f}")


def last_business_day_flags(dates):
    """ロンドン日付 Series に対して、その月の月〜金最終日かどうか"""
    s = pd.Series(dates)
    df = pd.DataFrame({"d": s})
    df["ym"] = df["d"].apply(lambda d: (d.year, d.month))
    last = df.groupby("ym")["d"].transform("max")
    return (df["d"] == last).values


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    for inst, pip in PAIRS.items():
        df = pd.read_sql_query(
            f"SELECT t,bo,ao,bc,ac,bh,bl,ah,al FROM m1 WHERE inst='{inst}' ORDER BY t", con)
        # dict: epoch -> (bo, ao, mid_open, spread_open_pip, high, low)
        price = {}
        for t, bo, ao, bc, ac, bh, bl, ah, al in zip(df.t, df.bo, df.ao, df.bc, df.ac,
                                                     df.bh, df.bl, df.ah, df.al):
            price[int(t)] = (bo, ao, (bo + ao) / 2, (ao - bo) * pip, bh, bl, ah, al)

        london = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Europe/London")
        lon_dates = sorted(set(london.dt.date))
        # fixing epoch per London date: 16:00 local
        fix_epoch = {}
        for d in lon_dates:
            dow = pd.Timestamp(d).dayofweek
            if dow > 4:
                continue
            ts = (pd.Timestamp(d) + pd.Timedelta(hours=16)).tz_localize("Europe/London")
            fix_epoch[d] = int(ts.timestamp())
        dates = sorted(fix_epoch)
        is_month_end = dict(zip(dates, last_business_day_flags(dates)))

        def at(d, off_min):
            return price.get(fix_epoch[d] + off_min * 60)

        # ---- collect per-day records ----
        recs = []
        path_offsets = list(range(-60, 61, 5))
        for d in dates:
            base = at(d, -60)
            if base is None:
                continue
            rec = {"date": d, "year": d.year, "month_end": bool(is_month_end[d]),
                   "dow": pd.Timestamp(d).dayofweek}
            ok = True
            mids = {}
            for off in sorted(set(path_offsets + OFFSETS + [-180, 180])):
                p = at(d, off)
                if p is None:
                    if off in OFFSETS:
                        ok = False
                    continue
                mids[off] = p[2]
            if not ok:
                continue
            rec["mids"] = mids
            # executable long returns for pre/post windows
            for e in PRE_ENTRIES:
                pe, pf_ = at(d, e), at(d, 0)
                rec[f"pre{e}_exec"] = (pf_[0] - pe[1]) * pip   # bid out - ask in
                rec[f"pre{e}_mid"] = (mids[0] - mids[e]) * pip
            for x in POST_EXITS:
                pe, px = at(d, 0), at(d, x)
                rec[f"post{x}_exec"] = (px[0] - pe[1]) * pip
                rec[f"post{x}_mid"] = (mids[x] - mids[0]) * pip
            rec["pre30"] = rec["pre-30_mid"]
            rec["post30"] = rec["post30_mid"]
            rec["post15"] = rec["post15_mid"]
            rec["post60"] = rec["post60_mid"]
            # path (from -60)
            for off in path_offsets:
                if off in mids:
                    rec[f"path{off}"] = (mids[off] - mids[-60]) * pip
            # vol / spread around fix vs control (14:00 = T-120 bar block, 18:00 = T+120)
            def block_stats(center_off):
                tot_abs, spr, n = 0.0, [], 0
                for off in range(center_off - 10, center_off + 10):
                    p, q = at(d, off), at(d, off + 1)
                    if p is None or q is None:
                        continue
                    tot_abs += abs(q[2] - p[2]) * pip
                    spr.append(p[3])
                    n += 1
                return (tot_abs, np.mean(spr) if spr else np.nan, n)
            rec["abs_fix"], rec["spr_fix"], _ = block_stats(0)
            rec["abs_m120"], rec["spr_m120"], _ = block_stats(-120)
            rec["abs_p120"], rec["spr_p120"], _ = block_stats(120)
            recs.append(rec)

        R = pd.DataFrame(recs)
        print(f"\n================ {inst} (n={len(R)} days, "
              f"{R.date.min()} .. {R.date.max()}) ================")

        print("-- 平均パス mid (T-60基準, pip) --")
        row = []
        for off in path_offsets:
            col = f"path{off}"
            if col in R:
                row.append(f"T{off:+d}:{R[col].mean():+.1f}")
        print("  " + "  ".join(row))

        print("-- 無条件 LONG 事前ドリフト (entry -> T) --")
        for e in PRE_ENTRIES:
            fmt(f"in T{e} -> T exec", stats(R[f"pre{e}_exec"]))
            fmt(f"in T{e} -> T mid", stats(R[f"pre{e}_mid"]))
        print("-- 無条件 LONG 事後 (T -> exit) --")
        for x in POST_EXITS:
            fmt(f"T -> T+{x} exec", stats(R[f"post{x}_exec"]))
            fmt(f"T -> T+{x} mid", stats(R[f"post{x}_mid"]))

        print("-- split-half: 事前 T-30->T mid / 事後 T->T+30 mid --")
        for lab, mask in [("<=2023", R.year <= 2023), ("2024+", R.year >= 2024)]:
            fmt(f"{lab} pre30 mid", stats(R.loc[mask, "pre-30_mid"]))
            fmt(f"{lab} post30 mid", stats(R.loc[mask, "post30_mid"]))

        print("-- 条件付き: pre30 と post の関係 --")
        both = R[["pre30", "post15", "post30", "post60"]].dropna()
        for pc in ["post15", "post30", "post60"]:
            c = np.corrcoef(both["pre30"], both[pc])[0, 1]
            print(f"  corr(pre30, {pc}) = {c:+.3f} (n={len(both)})")
        up, dn = both[both.pre30 > 0], both[both.pre30 < 0]
        fmt("post30 mid | pre30>0", stats(up.post30))
        fmt("post30 mid | pre30<0", stats(dn.post30))
        # fade 戦略: Tで pre と逆方向に張り T+30 で手仕舞い (mid, コスト前)
        fade30 = -np.sign(both.pre30) * both.post30
        follow30 = np.sign(both.pre30) * both.post30
        fmt("FADE30 mid (gross)", stats(fade30))
        fmt("FOLLOW30 mid (gross)", stats(follow30))
        # fade の exec 近似: post30 exec の符号反転は spread を二重に払うので
        # mid - 往復spread中央値 で概算
        med_spr = R["spr_fix"].median()
        print(f"  (参考: T周辺 spread 中央値 {med_spr:.1f}pip — fade/follow の exec は概ね mid - 約{med_spr:.1f}pip)")
        fmt("FADE30 mid - spread", stats(fade30 - med_spr))
        # 大きな pre のときだけ fade (|pre30| >= 75 percentile)
        thr = both.pre30.abs().quantile(0.75)
        big = both[both.pre30.abs() >= thr]
        fmt(f"FADE30 big|pre|>={thr:.1f} mid", stats(-np.sign(big.pre30) * big.post30))
        fmt(f"FADE30 big|pre| mid - spread", stats(-np.sign(big.pre30) * big.post30 - med_spr))

        print("-- 月末 vs 非月末 --")
        me, nme = R[R.month_end], R[~R.month_end]
        print(f"  month-end n={len(me)}, other n={len(nme)}")
        fmt("ME pre30 mid", stats(me["pre-30_mid"]))
        fmt("non-ME pre30 mid", stats(nme["pre-30_mid"]))
        fmt("ME post30 mid", stats(me.post30))
        fmt("non-ME post30 mid", stats(nme.post30))
        bme = me[["pre30", "post30"]].dropna()
        if len(bme) > 10:
            fmt("ME FADE30 mid", stats(-np.sign(bme.pre30) * bme.post30))

        print("-- ボラとスプレッドの歪み (20分ブロック) --")
        for col, lab in [("abs_fix", "|move| T-10..+10"), ("abs_m120", "|move| T-130..-110 (14時台対照)"),
                         ("abs_p120", "|move| T+110..+130 (18時台対照)")]:
            print(f"  {lab:<34} mean {R[col].mean():6.1f}pip  median {R[col].median():6.1f}pip")
        for col, lab in [("spr_fix", "spread T周辺"), ("spr_m120", "spread 対照-120"), ("spr_p120", "spread 対照+120")]:
            print(f"  {lab:<34} median {R[col].median():5.2f}pip  mean {R[col].mean():5.2f}pip")

    con.close()
    print("\nDONE (price only; swap NOT included)")


if __name__ == "__main__":
    main()
