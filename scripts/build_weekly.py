#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
クロージングベル新聞「週次トラッカー」生成スクリプト。

使い方:
    python3 build_weekly.py 2026-10-26   # その週（月曜の日付）のページを生成
    python3 build_weekly.py              # 今週分

出力: paper/week-<月曜>.html

材料と定義:
- 判断: ~/workspace/ai-repo/projects/spx-judgment/judgments/<JST日付>_close_<model>.json
  （同一モデル・同一日の _2 ファイルは再実行分として除外し、初回分を採用）
- SPX終値: FRED SP500 系列（fredgraph CSV、キー不要）を正とし、未公表の最新日だけ
  ~/spx_data/spx_options.sqlite3 の当日最終スナップショットで補う。出所はページに明記。
- 的中判定: bias が bullish で上昇 / bearish で下落 = 的中、neutral = 様子見、それ以外は外れ。
- Brierスコア: (p_up - 結果[上昇=1, 下落=0])^2 の平均。小さいほど良い。
- 的中率: 的中 / (的中 + 外れ)。様子見は分母に入れない。
- 週の区切りは判断実行日（JST）の月曜〜金曜。対象日は判断JSONの target_date（ET）。
"""
import csv
import io
import json
import sqlite3
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

JUDGMENTS = Path.home() / "workspace/ai-repo/projects/spx-judgment/judgments"
OPTIONS_DB = Path.home() / "spx_data/spx_options.sqlite3"
OUT_DIR = Path(__file__).resolve().parent.parent / "paper"

MODEL_NAMES = {
    "claude-sonnet-4-6": "Sonnet記者",
    "gemini-3.8-flash-high": "Gemini記者",
    "gpt-oss-120b-medium": "GPT記者",
    "muse": "Muse記者",
    "muse-spark": "Muse記者",
}
BIAS_JA = {"bullish": "強気", "bearish": "弱気", "neutral": "中立"}


def load_closes() -> dict:
    closes = {}
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"
    with urllib.request.urlopen(url, timeout=30) as r:
        text = r.read().decode()
    for row in csv.reader(io.StringIO(text)):
        if len(row) == 2 and row[1] not in (".", ""):
            try:
                closes[row[0]] = float(row[1])
            except ValueError:
                pass
    # FRED未公表分をオプショDBの当日最終スナップショットで補完
    try:
        con = sqlite3.connect(str(OPTIONS_DB))
        rows = con.execute(
            "SELECT substr(data_asof_et,1,10) d, current_price FROM snapshots "
            "WHERE data_asof_et IN (SELECT MAX(data_asof_et) FROM snapshots GROUP BY substr(data_asof_et,1,10))"
        ).fetchall()
        for d, p in rows:
            closes.setdefault(d, float(p))
    except Exception:
        pass
    return closes


def load_judgments() -> list:
    items = []
    for f in sorted(JUDGMENTS.glob("*_close_*.json")):
        if "_2" in f.stem:
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        j = d.get("judgment", {})
        model = f.stem.split("_close_", 1)[1]
        items.append({
            "run_date": f.name[:10],
            "model": model,
            "name": MODEL_NAMES.get(model, model),
            "target": j.get("target_date"),
            "bias": j.get("bias"),
            "p_up": j.get("p_up"),
            "key": (j.get("key_factors") or [""])[0],
        })
    return items


def verdict(bias, up):
    if bias == "neutral":
        return "様子見"
    if (bias == "bullish" and up) or (bias == "bearish" and not up):
        return "的中"
    return "外れ"


def stats(rows):
    n_hit = sum(1 for r in rows if r["verdict"] == "的中")
    n_miss = sum(1 for r in rows if r["verdict"] == "外れ")
    n_mid = sum(1 for r in rows if r["verdict"] == "様子見")
    briers = [r["brier"] for r in rows if r["brier"] is not None]
    return {
        "n": len(rows), "hit": n_hit, "miss": n_miss, "mid": n_mid,
        "rate": (n_hit / (n_hit + n_miss)) if (n_hit + n_miss) else None,
        "brier": (sum(briers) / len(briers)) if briers else None,
    }


def main() -> None:
    if len(sys.argv) > 1:
        monday = date.fromisoformat(sys.argv[1])
    else:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)

    closes = load_closes()
    judg = load_judgments()
    for it in judg:
        t = it["target"]
        if t in closes:
            # 前営業日終値
            prev_dates = [d for d in closes if d < t]
            prev = closes[max(prev_dates)] if prev_dates else None
            if prev:
                it["close"] = closes[t]
                it["chg"] = (closes[t] / prev - 1) * 100
                it["up"] = closes[t] > prev
                it["verdict"] = verdict(it["bias"], it["up"])
                it["brier"] = (it["p_up"] - (1 if it["up"] else 0)) ** 2 if it["p_up"] is not None else None
                continue
        it["verdict"] = "結果待ち"
        it["brier"] = None

    week = [it for it in judg if monday.isoformat() <= it["run_date"] <= friday.isoformat()]
    season = [it for it in judg if it["verdict"] in ("的中", "外れ", "様子見")]
    models = []
    for m in dict.fromkeys(it["name"] for it in judg):
        models.append(m)

    def pct(x):
        return f"{x*100:.0f}%" if x is not None else "―"

    # 週の流れ（SPX終値）
    flow_dates = sorted({it["target"] for it in week if it.get("close")})
    chg_by_target = {it["target"]: it["chg"] for it in week if it.get("close")}
    flow_rows = "".join(
        f"<tr><td>{t}</td><td class='num'>{closes[t]:,.1f}</td>"
        f"<td class='num'>{chg_by_target[t]:+.2f}%</td></tr>"
        for t in flow_dates
    )

    # 日別テーブル
    day_blocks = ""
    for run_date in sorted({it["run_date"] for it in week}):
        rows = [it for it in week if it["run_date"] == run_date]
        tgt = rows[0]["target"]
        head = f"{run_date} 朝の予想（対象 {tgt}）"
        if rows[0].get("close"):
            head += f" ― 結果: {rows[0]['close']:,.1f}（{rows[0]['chg']:+.2f}%）"
        trs = "".join(
            f"<tr><td>{r['name']}</td><td>{BIAS_JA.get(r['bias'], r['bias'])}</td>"
            f"<td class='num'>{r['p_up']:.0%}</td><td class='v-{r['verdict']}'>{r['verdict']}</td>"
            f"<td class='key'>{r['key']}</td></tr>"
            for r in rows
        )
        day_blocks += f"<h3>{head}</h3><table><tr><th>記者</th><th>予想</th><th>上昇確率</th><th>結果</th><th>根拠（第1要因）</th></tr>{trs}</table>"

    # 成績表
    def stat_rows(scope_rows):
        out = ""
        for m in models:
            s = stats([r for r in scope_rows if r["name"] == m])
            if s["n"] == 0:
                continue
            b = f"{s['brier']:.3f}" if s["brier"] is not None else "―"
            out += (f"<tr><td>{m}</td><td class='num'>{s['n']}</td><td class='num'>{s['hit']}</td>"
                    f"<td class='num'>{s['miss']}</td><td class='num'>{s['mid']}</td>"
                    f"<td class='num'>{pct(s['rate'])}</td><td class='num'>{b}</td></tr>")
        return out

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>クロージングベル新聞 週次トラッカー {monday.isoformat()}週</title>
<style>
  body {{ font-family: "Hiragino Sans", "Noto Sans CJK JP", sans-serif; background:#f4f1ea; color:#1a1a1a; margin:0; }}
  .page {{ max-width: 880px; margin: 0 auto; background:#fffdf7; padding: 26px 28px 40px; box-shadow: 0 0 18px rgba(0,0,0,.12); }}
  h1 {{ font-size: 27px; border-bottom: 4px double #1a1a1a; padding-bottom: 8px; }}
  h2 {{ font-size: 20px; margin-top: 30px; border-left: 6px solid #b41e28; padding-left: 10px; }}
  h3 {{ font-size: 15.5px; margin: 20px 0 6px; color:#333; }}
  table {{ width:100%; border-collapse: collapse; font-size: 13.5px; }}
  th, td {{ border-bottom: 1px solid #ddd; padding: 5px 6px; text-align: left; vertical-align: top; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.key {{ color:#555; font-size: 12.5px; }}
  .v-的中 {{ color:#1c6e1c; font-weight:bold; }} .v-外れ {{ color:#b02020; font-weight:bold; }} .v-様子見 {{ color:#8a6d1c; }}
  footer {{ margin-top: 34px; border-top: 1px solid #999; padding-top: 8px; font-size: 11.5px; color:#777; line-height:1.7; }}
  a {{ color:#2456a6; }}
</style>
</head>
<body>
<div class="page">
  <h1>クロージングベル新聞 ― 週次トラッカー（{monday.isoformat()} の週）</h1>
  <p>AI記者たちのS&amp;P500終値予想について、その週の推移・経過・結果をまとめた記録です。毎朝、朝刊の発行に合わせて更新されます。</p>

  <h2>週の流れ（SPX終値）</h2>
  <table><tr><th>日付（ET）</th><th>終値</th><th>前日比</th></tr>{flow_rows}</table>

  <h2>日別の予想と結果</h2>
  {day_blocks}

  <h2>記者別成績（この週）</h2>
  <table><tr><th>記者</th><th>予想数</th><th>的中</th><th>外れ</th><th>様子見</th><th>的中率</th><th>平均Brier</th></tr>{stat_rows(week)}</table>

  <h2>記者別成績（シーズン通算）</h2>
  <table><tr><th>記者</th><th>予想数</th><th>的中</th><th>外れ</th><th>様子見</th><th>的中率</th><th>平均Brier</th></tr>{stat_rows(season)}</table>

  <footer>
    集計定義: 的中 = bias の方向と実際の上げ下げが一致（neutral は様子見として的中率の分母から除外）。
    Brier スコア =（上昇確率 − 結果[上昇=1, 下落=0]）²の平均で、小さいほど良い。
    SPX終値は FRED の SP500 系列を正とし、未公表分のみオプション収集DBの引けスナップショットで補完。
    判断データは ai-repo projects/spx-judgment の judgments/ に基づく。これは予想の記録であり、投資助言ではありません。<br>
    <a href="index.html">← 紙面トップ</a>
  </footer>
</div>
</body>
</html>
"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"week-{monday.isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    print(f"saved {out} (week rows: {len(week)}, season rows: {len(season)})")


if __name__ == "__main__":
    main()
