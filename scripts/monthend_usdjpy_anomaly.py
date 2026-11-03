"""
月末の USD/JPY アノマリー検証（事前固定定義、最適化なし）

ユーザーの仮説（口伝で曖昧）:
- 「月末の USD/JPY は上がる。20日過ぎくらいから買い始めて、月末に決済する」
  というアノマリーが昔から言われている。
- 曖昧な点: 買い始める日が「20日過ぎくらい」で不特定／月末決済の時刻も不特定。

定義（固定。実行前に決めたものだけを使い、事後の最適化はしない）:
- タイムゾーン: すべて JST (Asia/Tokyo)。暦日・月境界も JST。
- 銘柄: USD_JPY のみ。データ: ~/workspace/oanda_json/m1.db, テーブル m1
  (inst, t=epoch秒, bo/bh/bl/bc=bid OHLC, ao/ah/al/ac=ask OHLC)。期間 2020-10-01〜2026-10-29。
- 本定義（月末区間, セグメント3）:
  - エントリー: 各月21日 00:00 JST 以降に最初に現れる M1 バーの ask 始値(ao) で買い。
    21日が週末等でバーが無ければ翌営業日の初バーに滑る（実行可能定義）。
  - 決済: 各月末日（暦日）23:59 JST 以前で最後の M1 バーの bid 終値(bc)。
    実装上は「翌月1日 00:00 JST 未満の最後のバー」。
  - リターン: %表示。(決済bid / エントリーask - 1) * 100。スプレッド込み。
    手数料・スワップは含めない（約7営業日保有ではスワップは本来無視できない。
    USD/JPY ロングのスワップは概ねプラス側なので、本検証はロング側に保守的。
    ただし対照区間も同条件）。
- 対象月: 月末区間が完全に取れる 2020-10 〜 2026-09 の 72 か月。
  - 2020-10 は DB 開始が 2020-10-01 のため月としては部分月だが、21日→月末区間は
    完全に含まれるため含める（月内セグメント1のエントリーだけ DB 初バー
    2020-10-01 09:00 JST になる点は出力に明記）。
  - 2026-10 は DB が 2026-10-29 で終わり月末（10/31）決済ができないため除外する。
- 主対照（月内セグメントのペア比較）: 各月を
  セグメント1: 1日00:00 -> 10日23:59 / セグメント2: 11日00:00 -> 20日23:59 /
  セグメント3: 21日00:00 -> 月末23:59 の3区間に分け、全区間を同じ実行可能定義
  （区間開始以降の初バー ask 始値 -> 区間終了以前の最終バー bid 終値）で計算する。
  月末区間が同月の他区間より大きいかを、平均差（対応のある t 値）と
  「月末区間が3区間中最大だった月の割合」で見る。
  これにより 2021〜2024年の円安ドリフト（どの区間も上がりやすい）という交絡を抑える。
- 副対照: 全期間の「任意の開始日から、月末区間と同じ暦日数 D ぶん保有するロング」
  のリターン分布。D は月ごとに異なる（月末区間の暦日数 = 月の日数 - 20、8〜11日）。
  各月の月末リターンについて、同 D の全開始日トレード分布におけるパーセンタイルを出し、
  月末平均がランダムな開始日の組合せの平均以上になる確率をモンテカルロ
  （各月につき同 D の対照から1件ずつ独立に抽出、シード42、20,000回）で出す。
  対照トレードの定義も同じ: 開始日 00:00 JST 以降の初バー ask 始値
  （週末開始は翌営業日に滑ることを許容、エントリーは開始日+3日以内に限る）->
  開始日 + D 日 00:00 JST 未満の最終バー bid 終値（最終バーは終了前24時間以内にあるもののみ）。
- 感度（事前宣言分のみ）: エントリー日を 20日 / 21日（本定義）/ 25日 に変えた3通り。
  決済は月末で共通。それ以外は漁らない。
- 年別層別: 月の属する暦年ごとに月末区間の平均・勝率を出す（2020年は10〜12月の3件のみ）。

実行: python3 scripts/monthend_usdjpy_anomaly.py
"""

import bisect
import calendar
import os
import random
import sqlite3
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
INST = "USD_JPY"
MC_TRIALS = 20000
MC_SEED = 42

# 月末区間が完全に取れる月: 2020-10 .. 2026-09 (72か月)
MONTHS = []
_y, _m = 2020, 10
while (_y, _m) <= (2026, 9):
    MONTHS.append((_y, _m))
    _m += 1
    if _m == 13:
        _y, _m = _y + 1, 1


def ep(d, hh=0, mm=0):
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=JST).timestamp())


def fmt(t):
    return datetime.fromtimestamp(t, JST).strftime("%Y-%m-%d %H:%M %a")


def pct(new, old):
    return (new / old - 1.0) * 100.0


def first_bar(cur, start, end):
    return cur.execute(
        "SELECT t, ao FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t ASC LIMIT 1",
        (INST, start, end),
    ).fetchone()


def last_bar(cur, start, end):
    return cur.execute(
        "SELECT t, bc FROM m1 WHERE inst=? AND t>=? AND t<? ORDER BY t DESC LIMIT 1",
        (INST, start, end),
    ).fetchone()


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def t_stat(vals):
    n = len(vals)
    if n < 2:
        return float("nan")
    sd = statistics.stdev(vals)
    if sd == 0:
        return float("nan")
    return statistics.mean(vals) / (sd / (n ** 0.5))


def stats_line(name, vals):
    if not vals:
        return f"{name}: n=0"
    s = sorted(vals)
    win = 100.0 * sum(1 for v in vals if v > 0) / len(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return (
        f"{name}: n={len(vals)} mean={statistics.mean(vals):+.4f}% median={statistics.median(vals):+.4f}% "
        f"sum={sum(vals):+.4f}% win={win:.1f}% sd={sd:.4f} t={t_stat(vals):+.2f} "
        f"min={s[0]:+.4f}% p25={percentile(s, 0.25):+.4f}% p75={percentile(s, 0.75):+.4f}% max={s[-1]:+.4f}%"
    )


def segment_trade(cur, seg_start, seg_end_excl):
    ent = first_bar(cur, ep(seg_start), ep(seg_end_excl))
    if ent is None:
        return None
    ext = last_bar(cur, ep(seg_start), ep(seg_end_excl))
    if ext is None or ext[0] <= ent[0]:
        return None
    return {"entry_t": ent[0], "entry_px": ent[1], "exit_t": ext[0], "exit_px": ext[1],
            "ret": pct(ext[1], ent[1])}


def month_segments(cur, y, m):
    ndays = calendar.monthrange(y, m)[1]
    d1 = date(y, m, 1)
    d11 = date(y, m, 11)
    d21 = date(y, m, 21)
    nxt = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
    seg1 = segment_trade(cur, d1, d11)
    seg2 = segment_trade(cur, d11, d21)
    seg3 = segment_trade(cur, d21, nxt)
    return {"ndays": ndays, "seg1": seg1, "seg2": seg2, "seg3": seg3}


def entry_variant(cur, y, m, entry_day):
    d = date(y, m, entry_day)
    nxt = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
    return segment_trade(cur, d, nxt)


def control_trades(cur, dur_days, db_start_day, db_max_t):
    out = []
    s = db_start_day
    while ep(s) <= db_max_t:
        win_end = s + timedelta(days=dur_days)
        ent = first_bar(cur, ep(s), ep(s + timedelta(days=3)))
        if ent is not None:
            ext = last_bar(cur, ep(s), ep(win_end))
            if ext is not None and ext[0] > ent[0] and ext[0] >= ep(win_end) - 86400:
                out.append(pct(ext[1], ent[1]))
        s += timedelta(days=1)
    return out


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    db_min, db_max = cur.execute("SELECT MIN(t), MAX(t) FROM m1 WHERE inst=?", (INST,)).fetchone()

    print("=" * 100)
    print("月末 USD/JPY アノマリー検証 (monthend_usdjpy_anomaly.py)")
    print(f"DB: {DB}")
    print(f"run_at_jst: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"USD_JPY バー範囲: {fmt(db_min)} .. {fmt(db_max)}")
    print("定義: JST基準 / エントリー=各月21日00:00 JST以降の初バー ask始値, 決済=月末23:59 JST以前の最終バー bid終値")
    print("      リターンは% (スプレッド込み, 手数料/スワップなし) / 主対照=同月の3セグメント比較 / 感度=エントリー20日・21日・25日")
    print(f"対象月: {MONTHS[0][0]}-{MONTHS[0][1]:02d} .. {MONTHS[-1][0]}-{MONTHS[-1][1]:02d} の {len(MONTHS)} か月")
    print("  2020-10 は21日->月末区間が完全に含まれるため対象に含む (月としてはDB開始 2020-10-01 の部分月)。")
    print("  2026-10 はDBが2026-10-29で終わり月末決済ができないため除外する。")
    print()

    data = {}
    for (y, m) in MONTHS:
        data[(y, m)] = month_segments(cur, y, m)

    s1_oct20 = data[(2020, 10)]["seg1"]
    if s1_oct20:
        print(f"注記: 2020-10 セグメント1のエントリーは {fmt(s1_oct20['entry_t'])} "
              f"(DB開始直後の初バー。10/1 00:00〜この時刻の変動は欠落)")
    slipped = []
    for (y, m), d in data.items():
        s3 = d["seg3"]
        if s3:
            ent_date = datetime.fromtimestamp(s3["entry_t"], JST).date()
            if ent_date.day != 21:
                slipped.append(f"{y}-{m:02d}: 21日({date(y, m, 21).strftime('%a')})->初バー {ent_date} {ent_date.strftime('%a')}")
    print(f"月末区間のエントリーが21日当日でなかった月: {len(slipped)} 件")
    for s in slipped:
        print(f"    {s}")
    print()

    seg1 = [d["seg1"]["ret"] for d in data.values() if d["seg1"]]
    seg2 = [d["seg2"]["ret"] for d in data.values() if d["seg2"]]
    seg3 = [d["seg3"]["ret"] for d in data.values() if d["seg3"]]

    print("-" * 100)
    print("【本定義】月末区間 (21日 -> 月末) USD/JPY")
    print("  " + stats_line("セグメント3 (21日->月末)", seg3))
    s3trades = [d["seg3"] for d in data.values() if d["seg3"]]
    pip_vals = [(tr["exit_px"] - tr["entry_px"]) * 100.0 for tr in s3trades]
    print(f"  参考 (pip換算, 価格差x100): mean={statistics.mean(pip_vals):+.2f} pip median={statistics.median(pip_vals):+.2f} pip")
    print("  月別明細 (年-月: seg1% / seg2% / seg3%, seg3が最大なら *)")
    for (y, m) in MONTHS:
        d = data[(y, m)]
        if not (d["seg1"] and d["seg2"] and d["seg3"]):
            print(f"    {y}-{m:02d}: データ不足")
            continue
        vals = [d["seg1"]["ret"], d["seg2"]["ret"], d["seg3"]["ret"]]
        star = " *" if vals[2] == max(vals) else ""
        print(f"    {y}-{m:02d} (月末{d['ndays']}日, D={d['ndays'] - 20}日): seg1={vals[0]:+.4f}% seg2={vals[1]:+.4f}% seg3={vals[2]:+.4f}%{star}")
    print()

    print("-" * 100)
    print("【主対照】月内セグメント比較 (ペア比較, 同じ実行可能定義)")
    print("  " + stats_line("セグメント1 (1日->10日)", seg1))
    print("  " + stats_line("セグメント2 (11日->20日)", seg2))
    print("  " + stats_line("セグメント3 (21日->月末)", seg3))
    pairs = [(d["seg1"]["ret"], d["seg2"]["ret"], d["seg3"]["ret"]) for d in data.values()
             if d["seg1"] and d["seg2"] and d["seg3"]]
    diff31 = [c - a for a, b, c in pairs]
    diff32 = [c - b for a, b, c in pairs]
    diff3avg = [c - (a + b) / 2.0 for a, b, c in pairs]
    n_max = sum(1 for a, b, c in pairs if c == max(a, b, c))
    print(f"  ペア数: {len(pairs)} か月")
    print("  " + stats_line("差 seg3 - seg1", diff31))
    print("  " + stats_line("差 seg3 - seg2", diff32))
    print("  " + stats_line("差 seg3 - (seg1+seg2)/2", diff3avg))
    print(f"  月末区間が3区間中最大だった月: {n_max}/{len(pairs)} = {100.0 * n_max / len(pairs):.1f}% (偶然なら約33%)")
    print(f"  月末区間がプラスだった月: {sum(1 for c in seg3 if c > 0)}/{len(seg3)}")
    print()

    print("-" * 100)
    print("【年別層別】月末区間 (21日->月末)")
    years = sorted({y for y, _ in MONTHS})
    for y in years:
        vals = [data[(yy, m)]["seg3"]["ret"] for (yy, m) in MONTHS if yy == y and data[(yy, m)]["seg3"]]
        note = " (10-12月のみ)" if y == 2020 else " (1-9月のみ)" if y == 2026 else ""
        print("  " + stats_line(f"{y}年{note}", vals))
    early = [data[(y, m)]["seg3"]["ret"] for (y, m) in MONTHS if 2021 <= y <= 2024 and data[(y, m)]["seg3"]]
    late = [data[(y, m)]["seg3"]["ret"] for (y, m) in MONTHS if y >= 2025 and data[(y, m)]["seg3"]]
    print("  " + stats_line("2021-2024 (円安期)", early))
    print("  " + stats_line("2025-2026 (下落局面含む)", late))
    print()

    print("-" * 100)
    print("【感度】エントリー日のみ変更 (事前宣言: 20日 / 21日 / 25日, 決済は月末で共通)")
    for eday in (20, 21, 25):
        vals = []
        for (y, m) in MONTHS:
            tr = data[(y, m)]["seg3"] if eday == 21 else entry_variant(cur, y, m, eday)
            if tr:
                vals.append(tr["ret"])
        print("  " + stats_line(f"エントリー {eday}日 -> 月末", vals))
    print()

    print("-" * 100)
    print("【副対照】任意の開始日から同じ暦日数 D ぶん保有するロングの分布")
    durations = sorted({d["ndays"] - 20 for d in data.values()})
    print(f"  月末区間の暦日数 D の種類: {durations}")
    db_start_day = datetime.fromtimestamp(db_min, JST).date()
    pools = {}
    for dur in durations:
        pools[dur] = control_trades(cur, dur, db_start_day, db_max)
        print("  " + stats_line(f"対照 D={dur}日 全開始日", pools[dur]))
    pctiles = []
    for (y, m) in MONTHS:
        d = data[(y, m)]
        if not d["seg3"]:
            continue
        dur = d["ndays"] - 20
        pool_sorted = sorted(pools[dur])
        p = 100.0 * bisect.bisect_left(pool_sorted, d["seg3"]["ret"]) / len(pool_sorted)
        pctiles.append(p)
    print(f"  月末リターンの対照分布内パーセンタイル: 平均={statistics.mean(pctiles):.1f} 中央値={statistics.median(pctiles):.1f}")
    print(f"  パーセンタイルが50を超えた (対照中央値より良かった) 月: {sum(1 for p in pctiles if p > 50)}/{len(pctiles)}")
    target_mean = statistics.mean(seg3)
    rng = random.Random(MC_SEED)
    ge = 0
    month_durs = [(data[(y, m)]["ndays"] - 20) for (y, m) in MONTHS if data[(y, m)]["seg3"]]
    for _ in range(MC_TRIALS):
        samp = [rng.choice(pools[dur]) for dur in month_durs]
        if statistics.mean(samp) >= target_mean:
            ge += 1
    print(f"  モンテカルロ: 各月につき同 D の対照から1件ずつ引いた平均が月末区間平均 ({target_mean:+.4f}%) "
          f"以上になる確率 p={ge / MC_TRIALS:.4f} (シード{MC_SEED}, {MC_TRIALS}回)")
    print()
    print("注記: スワップ・手数料は含んでいない。感度は事前宣言した3通りのみ。")
    print("      複数区間・複数年の切り口は多重検定として割引くこと。")
    con.close()


if __name__ == "__main__":
    main()
