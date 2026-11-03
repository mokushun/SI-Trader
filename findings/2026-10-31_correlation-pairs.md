# m1.db 全銘柄の相関（2026-10-31、Muse）

スクリプト: `scripts/correlation_pairs.py`
出力: `outputs/correlation_pairs_out.txt`、`outputs/corr_hourly_full.csv`、
`outputs/corr_daily_full.csv`、`outputs/corr_hourly_1y.csv`、`outputs/correlation_pairs.json`
データ: m1.db（当時は 2020-10-01〜2026-10-29 版）、全21銘柄、mid 終値を
1時間サンプリングした対数リターン、週末ギャップ除外、n≒34,600。日次でも順位はほぼ同じ。

## 結論

- 最強は NAS100_USD / SPX500_USD **+0.93**。次点はクロス円クラスタ
  （EUR_JPY/GBP_JPY +0.80、CHF_JPY/EUR_JPY +0.78、AUD_JPY/GBP_JPY +0.73）と
  EUR_USD/GBP_USD +0.74、EUR_AUD/GBP_AUD +0.72
- 負の最強は AUD_CHF/EUR_AUD **−0.79**、EUR_USD/USD_CHF −0.74、AUD_USD/EUR_AUD −0.69、
  EUR_GBP/GBP_CHF −0.68。強い相関の大半は共通通貨レッグによる機械的なもの
- 機械的でないのに目立つもの: AUD_JPY/JP225 +0.53、AUD_JPY/SPX500 +0.48、
  AUD_CHF/SPX500 +0.46（リスクオン代理）。JP225 は SPX +0.67 / NAS +0.65
- XAU_USD は最大でも AUD_USD +0.42 と中程度。WTICO_USD は最大 +0.18、
  NATGAS_USD は全ペア最大 +0.18 でほぼ無相関（最も独立）
- 直近1年（2025-10〜）では多くのFXペアで相関が強まった
  （EUR_JPY/GBP_JPY 0.80→0.86、EUR_USD/GBP_USD 0.74→0.83、EUR_USD/USD_CHF −0.74→−0.82）

## 注意

- 価格水準ではなくリターンの相関。相関が高いことは乖離の収束（ペアトレードの
  成立）を意味しない — その点は翌日に先行・遅行検証で別途確認し、否定された
  （`findings/2026-10-31_lead-lag.md`）
- この測定は 2020-10 開始版 DB で実施。2015年版での再測定は未実施
