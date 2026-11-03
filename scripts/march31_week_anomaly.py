"""
3月31日を含む週のアノマリー検証（事前固定定義、最適化なし）

ユーザーの仮説:
- 毎年「3月31日を含む週」（3月31日当日に入るのではない）の月曜日に買い、
  同じ週の金曜日に強制決済すると大きくプラスになる。
- 理屈: 3月期決算の機関投資家が全ポジションを解消した後、4月に入る前に大きく動く。
- フィルタ: エントリー時点で RSI が既に高い時は入らない。
  保有中に1時間足 RSI が70以上になったら利確する（フィルタはこの売却のみ）。

定義（固定。実行前に決めたものだけを使い、事後の最適化はしない）:
- タイムゾーン: すべて JST (Asia/Tokyo, UTC+9固定)。週の切り方も JST。
- 対象週: 3月31日を含む週の月曜日をエントリー日とする。
  「含む週」は月〜日週で判定する（例: 2024-03-31は日曜なのでその週の月曜は2024-03-25、
  2025-03-31は月曜なのでエントリー日が偶然3月31日自体になる）。
- 対象年: 2021〜2026 の6回。2020年は m1.db が2020-10-01開始のため対象外（明記）。
- データ: ~/workspace/oanda_json/m1.db, テーブル m1 (inst, t=epoch秒,
  bo/bh/bl/bc=bid OHLC, ao/ah/al/ac=ask OHLC)。全21銘柄。
- エントリー: 月曜 0:00 JST のバーは存在しない（本スクリプトで再確認する）ため、
  「月曜0:00指定は実行不能」とし、月曜（JST暦日）の最初のM1バーの ask始値(ao) で買い。
- 決済（ベースライン）: 取引週としての金曜の最後のM1バーの bid終値(bc)。
  FXの金曜セッションは NY金曜17:00 = JST土曜06:00(EDT)/07:00(EST) に終わるため、
  探索窓は「金曜00:00 JST 〜 土曜08:00 JST」とし、その最後のバーを金曜最終バーとする。
  実際の決済日時は全件記録する。参考値として JST暦日金曜(00:00〜24:00)最後のバーでの
  リターンも USD_JPY 対象週だけ併記する（本判定には使わない）。
- リターン: %表示で統一。(決済bid / エントリーask - 1) * 100。スプレッド込み
  （買いはask、売りはbid）。手数料・スワップは含めない。
  注記: 5日保有（月〜金）ではスワップは本来自明には無視できない。
  m1.db にスワップ情報が無いため本検証は価格変動+スプレッドのみ。
- RSIフィルタ版:
  - 1時間足は JST で1時間集計。各JST時の最後のM1バーの mid終値 ((bc+ac)/2) を
    その1Hバーの終値とする。週末ギャップは無視し、バーの時系列順に並べる。
  - RSI(14) は Wilder 平滑ではなく単純平均版（Cutler）で一貫させる:
    直近14本の1H終値差分の平均上昇幅・平均下落幅から RSI = 100 - 100/(1+RS)。
    平均下落幅=0 かつ平均上昇幅>0 なら 100、両方0なら 50。
  - (a) エントリー見送り: エントリー直前の「完成した」1Hバー
    （その1Hの終了時刻 <= エントリー時刻である最後の1Hバー）の RSI >= 70 なら入らない。
  - (b) 利確: 保有中、完成した1Hバーの RSI >= 70 になったら、その1H終了時刻以降の
    最初のM1バーの bid始値(bo) で決済する。発生しなければベースライン決済。
  - RSIのウォームアップとしてエントリー21日前から1Hバーを構築する
    （単純平均RSIは直近14差分しか使わないため十分）。
- 対照群: 同じ銘柄・同じ年（月曜の属する暦年）の「3月31日週以外の全ての週」について、
  同じ定義（月曜初バー ask始値 → 取引金曜最終バー bid終値、RSIフィルタなし）で計算。
  対象週6件の平均が対照群から6週を無作為に（非復元）引いた平均以上になる確率を
  モンテカルロ（乱数シード42、20,000回）で出す。
- 銘柄横断の注意: 21銘柄は同週のマクロで同時に動くため相関が高い。
  「6年 x 21銘柄 = 126件」は独立サンプルではない。

実行: python3 scripts/march31_week_anomaly.py
"""

import bisect
import os
import random
import sqlite3
import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
MC_TRIALS = 20000
MC_SEED = 42


def ep(d, hh=0, mm=0):
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=JST).timestamp())


def fmt(t):
    return datetime.fromtimestamp(t, JST).strftime("%Y-%m-%d %H:%M %a")


def target_monday(year):
    mar31 = date(year, 3, 31)
    return mar31 - timedelta(days=mar31.weekday())


def pct(new, old):
    return (new / old - 1.0) * 100.0


def first_bar(cur, inst, start, end):
    return cur.execute(
        "SELECT t, ao FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t ASC LIMIT 1",
        (inst, start, end),
    ).fetchone()


def last_bar(cur, inst, start, end):
    return cur.execute(
        "SELECT t, bc FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t DESC LIMIT 1",
        (inst, start, end),
    ).fetchone()


def next_bar_bid_open(cur, inst, start, end):
    return cur.execute(
        "SELECT t, bo FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t ASC LIMIT 1",
        (inst, start, end),
    ).fetchone()


def hourly_closes(cur, inst, start, end):
    hours = {}
    for t, bc, ac in cur.execute(
        "SELECT t, bc, ac FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t ASC",
        (inst, start, end),
    ):
        hour_start = ((t + 32400) // 3600) * 3600 - 32400
        hours[hour_start] = (bc + ac) / 2.0
    return sorted(hours.items())


def rsi_series(closes):
    out = [None] * len(closes)
    for i in range(14, len(closes)):
        gains = 0.0
        losses = 0.0
        for j in range(i - 13, i + 1):
            diff = closes[j] - closes[j - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        ag = gains / 14.0
        al = losses / 14.0
        if al == 0:
            out[i] = 100.0 if ag > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + ag / al)
    return out


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def stats_line(name, vals):
    if not vals:
        return f"{name}: n=0"
    s = sorted(vals)
    win = 100.0 * sum(1 for v in vals if v > 0) / len(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return (
        f"{name}: n={len(vals)} mean={statistics.mean(vals):+.4f}% median={statistics.median(vals):+.4f}% "
        f"sum={sum(vals):+.4f}% win={win:.1f}% sd={sd:.4f} "
        f"min={s[0]:+.4f}% p10={percentile(s, 0.10):+.4f}% p25={percentile(s, 0.25):+.4f}% "
        f"p75={percentile(s, 0.75):+.4f}% p90={percentile(s, 0.90):+.4f}% max={s[-1]:+.4f}%"
    )


def week_trade(cur, inst, monday):
    fri = monday + timedelta(days=4)
    ent = first_bar(cur, inst, ep(monday), ep(monday + timedelta(days=1)))
    if ent is None:
        return None
    ext = last_bar(cur, inst, ep(fri), ep(fri + timedelta(days=1), 8))
    if ext is None:
        return None
    return {
        "monday": monday,
        "entry_t": ent[0],
        "entry_px": ent[1],
        "exit_t": ext[0],
        "exit_px": ext[1],
        "ret": pct(ext[1], ent[1]),
    }


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    insts = [r[0] for r in cur.execute("SELECT inst FROM m1 GROUP BY inst ORDER BY inst")]

    print("=" * 100)
    print("3月31日を含む週のアノマリー検証 (march31_week_anomaly.py)")
    print(f"DB: {DB}")
    print(f"run_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("定義: JST基準 / 対象週=3月31日を含む月〜日週の月曜IN -> 同週取引金曜(金00:00 JST〜土08:00 JST窓の最終バー)OUT")
    print("      エントリー=月曜最初のM1バー ask始値(ao), 決済=最終M1バー bid終値(bc), リターンは% (スプレッド込み, 手数料/スワップなし)")
    print("      RSI: 1H JST集計, 終値=各時の最後M1のmid終値, RSI(14)単純平均(Cutler), エントリー直前完成1H RSI>=70で見送り,")
    print("           保有中完成1H RSI>=70で次のM1バー bid始値(bo)で利確")
    print("対象年: 2021-2026 の6回 (2020年はDBが2020-10-01開始のため対象週なし=対象外)")
    for y in YEARS:
        m = target_monday(y)
        print(f"  {y}: 3/31={date(y, 3, 31).strftime('%a')} -> Monday={m} Fri={m + timedelta(days=4)}")
    print(f"銘柄 ({len(insts)}): {', '.join(insts)}")
    print()

    targets = {inst: {} for inst in insts}
    controls = {inst: [] for inst in insts}
    monday_first_times = []
    monday_0000_bar_exists = 0
    monday_total_checked = 0

    mondays = []
    d = date(2021, 1, 4)
    while d <= date(2026, 10, 19):
        mondays.append(d)
        d += timedelta(days=7)
    target_mondays = {target_monday(y): y for y in YEARS}

    for inst in insts:
        for mon in mondays:
            tr = week_trade(cur, inst, mon)
            if tr is None:
                continue
            monday_first_times.append((inst, mon, datetime.fromtimestamp(tr["entry_t"], JST).strftime("%H:%M")))
            monday_total_checked += 1
            row = cur.execute("SELECT 1 FROM m1 WHERE inst=? AND t=? LIMIT 1", (inst, ep(mon))).fetchone()
            if row:
                monday_0000_bar_exists += 1
            if mon in target_mondays:
                targets[inst][target_mondays[mon]] = tr
            else:
                controls[inst].append(tr["ret"])

    print("-" * 100)
    print("【再確認】月曜0:00 JSTバーの存在と月曜初バーの時刻")
    print(f"  チェックした (銘柄 x 月曜) 組合せ: {monday_total_checked} 件")
    print(f"  月曜0:00 JSTちょうどのバーが存在した組合せ: {monday_0000_bar_exists} 件")
    cnt = Counter(hm for _, _, hm in monday_first_times)
    print(f"  月曜初バー時刻の最頻値 (全銘柄): {cnt.most_common(5)}")
    uj_cnt = Counter(hm for i, _, hm in monday_first_times if i == "USD_JPY")
    print(f"  月曜初バー時刻の最頻値 (USD_JPY): {uj_cnt.most_common(5)}")
    all_hm = sorted(hm for _, _, hm in monday_first_times)
    print(f"  月曜初バー時刻の範囲 (全銘柄): 最早 {all_hm[0]} / 最遅 {all_hm[-1]}")
    print("  -> 結論: 月曜0:00指定は実行不能。月曜初バーで代替する本定義が必須。")
    print()

    for inst in insts:
        for y in YEARS:
            tr = targets[inst].get(y)
            if tr is None:
                continue
            mon = tr["monday"]
            warm_start = ep(mon - timedelta(days=21))
            window_end = ep(mon + timedelta(days=5), 8)
            hours = hourly_closes(cur, inst, warm_start, window_end)
            closes = [c for _, c in hours]
            rsis = rsi_series(closes)
            entry_rsi = None
            for i, (hs, _) in enumerate(hours):
                if hs + 3600 <= tr["entry_t"] and rsis[i] is not None:
                    entry_rsi = rsis[i]
            tr["entry_rsi"] = entry_rsi
            tr["rsi_skipped"] = entry_rsi is not None and entry_rsi >= 70.0
            tr["rsi_exit_t"] = None
            tr["rsi_exit_px"] = None
            tr["rsi_trigger_hour"] = None
            tr["rsi_ret"] = None
            if not tr["rsi_skipped"]:
                baseline_limit = tr["exit_t"] + 60
                trig_end = None
                for i, (hs, _) in enumerate(hours):
                    hend = hs + 3600
                    if tr["entry_t"] < hend <= baseline_limit and rsis[i] is not None and rsis[i] >= 70.0:
                        trig_end = hend
                        tr["rsi_trigger_hour"] = (hs, rsis[i])
                        break
                if trig_end is not None:
                    nb = next_bar_bid_open(cur, inst, trig_end, window_end)
                    if nb is not None and nb[0] <= tr["exit_t"]:
                        tr["rsi_exit_t"], tr["rsi_exit_px"] = nb
                        tr["rsi_ret"] = pct(nb[1], tr["entry_px"])
                if tr["rsi_ret"] is None:
                    tr["rsi_ret"] = tr["ret"]
            fri = mon + timedelta(days=4)
            cal = last_bar(cur, inst, ep(fri), ep(fri + timedelta(days=1)))
            tr["calfri_exit_t"] = cal[0] if cal else None
            tr["calfri_ret"] = pct(cal[1], tr["entry_px"]) if cal else None

    print("-" * 100)
    print("【USD_JPY 重点】対象週 年別明細 (ベースライン / RSIフィルタ版)")
    uj = targets["USD_JPY"]
    for y in YEARS:
        tr = uj.get(y)
        if tr is None:
            print(f"  {y}: データなし")
            continue
        ersi = "n/a" if tr["entry_rsi"] is None else f"{tr['entry_rsi']:.2f}"
        if tr["rsi_skipped"]:
            filt = f"見送り (エントリー直前1H RSI={ersi} >= 70)"
        elif tr["rsi_exit_t"] is not None:
            filt = (
                f"利確 {fmt(tr['rsi_exit_t'])} bid始値={tr['rsi_exit_px']:.3f} ret={tr['rsi_ret']:+.4f}% "
                f"(トリガー1H: {fmt(tr['rsi_trigger_hour'][0])}開始 RSI={tr['rsi_trigger_hour'][1]:.2f})"
            )
        else:
            filt = f"金曜決済まで保有 ret={tr['rsi_ret']:+.4f}% (エントリー直前1H RSI={ersi})"
        print(f"  {y}: IN {fmt(tr['entry_t'])} ask={tr['entry_px']:.3f} -> OUT {fmt(tr['exit_t'])} bid={tr['exit_px']:.3f} "
              f"ret={tr['ret']:+.4f}% | RSIフィルタ: {filt} | 参考JST金曜暦日最終バー {fmt(tr['calfri_exit_t'])} ret={tr['calfri_ret']:+.4f}%")
    uj_base = [uj[y]["ret"] for y in YEARS if y in uj]
    uj_taken = [uj[y]["rsi_ret"] for y in YEARS if y in uj and not uj[y]["rsi_skipped"]]
    uj_sum0 = sum(uj_taken)
    print("  " + stats_line("USD_JPY ベースライン", uj_base))
    mean_taken = statistics.mean(uj_taken) if uj_taken else float("nan")
    print(f"  USD_JPY RSIフィルタ版: 実行={len(uj_taken)}件 見送り={len(uj_base) - len(uj_taken)}件 "
          f"実行分平均={mean_taken:+.4f}% 見送りを0%として6年合計={uj_sum0:+.4f}% (平均={uj_sum0 / 6:+.4f}%)")
    print()

    print("-" * 100)
    print("【全銘柄サマリ】対象6週ベースライン vs 対照群 (同銘柄・対象週以外の全週, RSIなし)")
    rng = random.Random(MC_SEED)
    rows = []
    for inst in insts:
        tvals = [targets[inst][y]["ret"] for y in YEARS if y in targets[inst]]
        cvals = controls[inst]
        taken = [targets[inst][y]["rsi_ret"] for y in YEARS if y in targets[inst] and not targets[inst][y]["rsi_skipped"]]
        skipped = len(tvals) - len(taken)
        if cvals and tvals:
            cs = sorted(cvals)
            indiv_pctiles = [100.0 * bisect.bisect_left(cs, v) / len(cs) for v in tvals]
            mean_indiv_pctile = statistics.mean(indiv_pctiles)
        else:
            indiv_pctiles, mean_indiv_pctile = [], float("nan")
        mc_p = float("nan")
        if len(cvals) >= 6 and tvals:
            tmean = statistics.mean(tvals)
            ge = 0
            for _ in range(MC_TRIALS):
                if statistics.mean(rng.sample(cvals, 6)) >= tmean:
                    ge += 1
            mc_p = ge / MC_TRIALS
        rows.append({"inst": inst, "tvals": tvals, "cvals": cvals, "taken": taken,
                     "skipped": skipped, "mc_p": mc_p, "indiv_pctiles": indiv_pctiles,
                     "mean_indiv_pctile": mean_indiv_pctile})
        wins = sum(1 for v in tvals if v > 0)
        tmean = statistics.mean(tvals) if tvals else float("nan")
        tmed = statistics.median(tvals) if tvals else float("nan")
        cmean = statistics.mean(cvals) if cvals else float("nan")
        cmed = statistics.median(cvals) if cvals else float("nan")
        cwin = 100.0 * sum(1 for v in cvals if v > 0) / len(cvals) if cvals else float("nan")
        tkmean = statistics.mean(taken) if taken else float("nan")
        print(f"{inst}: tgt n={len(tvals)} 勝{wins}/負{len(tvals) - wins} sum={sum(tvals):+.4f}% mean={tmean:+.4f}% "
              f"med={tmed:+.4f}% | RSI版 実行{len(taken)}/見送り{skipped} 実行平均={tkmean:+.4f}% 見送り0換算合計={sum(taken):+.4f}% | "
              f"ctrl n={len(cvals)} mean={cmean:+.4f}% med={cmed:+.4f}% win={cwin:.1f}% | "
              f"対象各件の対照パーセンタイル={[f'{p:.0f}' for p in indiv_pctiles]} 平均={mean_indiv_pctile:.1f} | MC p={mc_p:.4f}")
    print()
    print("  対象平均%の降順:")
    for r in sorted(rows, key=lambda r: statistics.mean(r["tvals"]) if r["tvals"] else -1e9, reverse=True):
        print(f"    {r['inst']}: mean={statistics.mean(r['tvals']):+.4f}% sum={sum(r['tvals']):+.4f}% MC p={r['mc_p']:.4f}")
    print()
    print("  " + stats_line("対照群 USD_JPY", controls["USD_JPY"]))
    for r in rows:
        if r["inst"] == "USD_JPY":
            cs = sorted(r["cvals"])
            print(f"    USD_JPY対照分布: p5={percentile(cs, 0.05):+.4f}% p10={percentile(cs, 0.10):+.4f}% p25={percentile(cs, 0.25):+.4f}% "
                  f"p50={percentile(cs, 0.50):+.4f}% p75={percentile(cs, 0.75):+.4f}% p90={percentile(cs, 0.90):+.4f}% p95={percentile(cs, 0.95):+.4f}%")
            print(f"    USD_JPY対象6件の対照パーセンタイル: {[f'{p:.1f}' for p in r['indiv_pctiles']]} (年順 2021->2026)")
    print()

    print("-" * 100)
    print("【年別クロス】各年の対象週リターン (全銘柄) ※銘柄間相関のため年が実質の単位")
    for y in YEARS:
        vals = [targets[i][y]["ret"] for i in insts if y in targets[i]]
        print(f"  {y} (Mon {target_monday(y)}): " + stats_line(f"year {y}", vals) +
              f" plus_inst={sum(1 for v in vals if v > 0)}/{len(vals)}")
    all_t = [targets[i][y]["ret"] for i in insts for y in YEARS if y in targets[i]]
    all_c = [v for i in insts for v in controls[i]]
    print("  " + stats_line("全銘柄プール 対象126件(独立ではない)", all_t))
    print("  " + stats_line("全銘柄プール 対照群", all_c))
    print()

    print("-" * 100)
    print("【RSIフィルタ集計】全銘柄 x 6年")
    n_skip = sum(1 for i in insts for y in YEARS if y in targets[i] and targets[i][y]["rsi_skipped"])
    n_early = sum(1 for i in insts for y in YEARS if y in targets[i] and targets[i][y]["rsi_exit_t"] is not None)
    n_exec = sum(1 for i in insts for y in YEARS if y in targets[i] and not targets[i][y]["rsi_skipped"])
    base_sum = sum(all_t)
    filt_sum0 = sum(targets[i][y]["rsi_ret"] for i in insts for y in YEARS if y in targets[i] and not targets[i][y]["rsi_skipped"])
    print(f"  エントリー見送り (直前1H RSI>=70): {n_skip} / {len(all_t)} 件")
    print(f"  利確発動 (保有中1H RSI>=70): {n_early} / {n_exec} 件 (見送りを除く実行分)")
    print(f"  ベースライン合計 (126件, 独立ではない): {base_sum:+.4f}%")
    print(f"  RSIフィルタ版合計 (見送り=0%換算): {filt_sum0:+.4f}%  差={filt_sum0 - base_sum:+.4f}%")
    early_detail = [(i, y, targets[i][y]["ret"], targets[i][y]["rsi_ret"]) for i in insts for y in YEARS
                    if y in targets[i] and targets[i][y]["rsi_exit_t"] is not None]
    if early_detail:
        print("  利確発動の明細 (inst, year, ベースライン%, 利確後%):")
        for i, y, b, f in early_detail:
            print(f"    {i} {y}: base={b:+.4f}% -> rsi={f:+.4f}% (差 {f - b:+.4f}%)")
    skip_detail = [(i, y, targets[i][y]["entry_rsi"], targets[i][y]["ret"]) for i in insts for y in YEARS
                   if y in targets[i] and targets[i][y]["rsi_skipped"]]
    if skip_detail:
        print("  見送りの明細 (inst, year, 直前1H RSI, 見送らなかった場合のベースライン%):")
        for i, y, r, b in skip_detail:
            print(f"    {i} {y}: entryRSI={r:.2f} base_would_be={b:+.4f}%")
    print()
    print("注記: n=6/年・銘柄しかない。21銘柄全探索は多重検定であり、個別銘柄の MC p は")
    print("      Bonferroni 等の補正なしでは有意性の根拠にならない。スワップ・手数料は含んでいない。")
    con.close()


if __name__ == "__main__":
    main()
