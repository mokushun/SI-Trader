#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
クロージングベル新聞「縦型デイリーカード」ジェネレータ（スマホ/Threads向け 1080x1440）。

使い方:
    python3 make_daily_card.py <data.json> <output.png>

data.json:
{
  "date_line": "2026年10月31日（土）朝刊",
  "kicker": "10月30日のニューヨーク",
  "headline": ["SPX急落 −1.8%", "AI決算ショック連鎖"],
  "lead": "Alphabet決算ミスをきっかけに売りが連鎖。…",
  "score_title": "昨日の答え合わせ（10/29予想 → 結果 −0.30%）",
  "score_rows": [
    {"name": "Sonnet記者", "bias": "弱気", "p_up": 42, "result": "的中"},
    {"name": "GPT記者",   "bias": "強気", "p_up": 58, "result": "外れ"}
  ],
  "cards": [
    {"title": "本日の予想（今夜のSPX）", "lines": ["Muse記者： 弱気　上昇確率 45%", "根拠： …"]},
    {"title": "今夜以降の材料", "lines": ["11/2（月）引け後： Apple決算", "11/3（火）： JOLTS求人"]}
  ],
  "numbers": [["SPX終値", "7,580.1（−1.82%）"], ["VIX", "17.22"], ["米10年債", "4.94%"]],
  "footer": "全文 https://mokushun.github.io/SI-Trader/paper/ ― 投資助言ではありません"
}

bias の色: 弱気=青, 中立=黄土, 強気=赤。result の色: 的中=緑, 外れ=赤, その他=灰。
数字は呼び出し側が入力・判断ファイルから転記すること（このスクリプトは計算しない）。
"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont

REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

W = 1080
H_MAX = 2600  # 描画用キャンバス。最終画像は内容量に合わせて下端を切り取る
BG = (247, 243, 234)
NAVY = (24, 42, 74)
RED = (178, 30, 40)
INK = (26, 26, 26)
BIAS_COLOR = {"弱気": (38, 92, 168), "中立": (168, 134, 38), "強気": (196, 52, 44)}
RES_COLOR = {"的中": (28, 120, 44), "外れ": (190, 34, 34)}


def main() -> None:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    img = Image.new("RGB", (W, H_MAX), BG)
    d = ImageDraw.Draw(img)

    def F(size, bold=False):
        return ImageFont.truetype(BOLD if bold else REG, size)

    def wrap(text, font, max_w):
        lines, cur = [], ""
        for ch in text:
            if d.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines

    # マストヘッド
    d.rectangle([0, 0, W, 118], fill=NAVY)
    d.text((44, 26), "クロージングベル新聞", font=F(46, bold=True), fill=(255, 255, 255))
    dl = data.get("date_line", "")
    d.text((W - 44 - d.textlength(dl, font=F(26)), 66), dl, font=F(26), fill=(210, 220, 240))

    y = 148
    # キッカー＋見出し
    if data.get("kicker"):
        d.rectangle([44, y, 44 + d.textlength(data["kicker"], font=F(27, bold=True)) + 28, y + 46], fill=RED)
        d.text((58, y + 6), data["kicker"], font=F(27, bold=True), fill=(255, 255, 255))
        y += 66
    for line in data.get("headline", []):
        d.text((44, y), line, font=F(63, bold=True), fill=INK)
        y += 82
    y += 6
    if data.get("lead"):
        for line in wrap(data["lead"], F(27), W - 88):
            d.text((44, y), line, font=F(27), fill=(60, 60, 60))
            y += 40
    y += 18

    def card_top(title, color=NAVY):
        nonlocal y
        d.rounded_rectangle([36, y, W - 36, y + 64], radius=10, fill=color)
        d.text((60, y + 13), title, font=F(30, bold=True), fill=(255, 255, 255))
        y += 64

    # 答え合わせ
    rows = data.get("score_rows", [])
    if rows:
        card_top(data.get("score_title", "昨日の答え合わせ"))
        for r in rows:
            # 名前
            d.text((60, y + 14), r["name"], font=F(29, bold=True), fill=INK)
            # バイアスチップ
            bc = BIAS_COLOR.get(r["bias"], (90, 90, 90))
            chip_x = 300
            d.rounded_rectangle([chip_x, y + 12, chip_x + 96, y + 54], radius=8, fill=bc)
            tw = d.textlength(r["bias"], font=F(26, bold=True))
            d.text((chip_x + (96 - tw) / 2, y + 17), r["bias"], font=F(26, bold=True), fill=(255, 255, 255))
            # p_up バー（0-100、50に中心線）
            bx0, bx1 = 424, 800
            d.rectangle([bx0, y + 20, bx1, y + 46], fill=(225, 220, 205), outline=(170, 165, 150))
            d.rectangle([bx0, y + 20, bx0 + (bx1 - bx0) * r["p_up"] / 100, y + 46], fill=bc)
            d.line([bx0 + (bx1 - bx0) / 2, y + 14, bx0 + (bx1 - bx0) / 2, y + 52], fill=(90, 90, 90), width=2)
            d.text((bx1 + 14, y + 15), f'{r["p_up"]}%', font=F(28, bold=True), fill=INK)
            # 結果
            rc = RES_COLOR.get(r["result"], (110, 110, 110))
            d.text((W - 60 - d.textlength(r["result"], font=F(29, bold=True)), y + 14), r["result"], font=F(29, bold=True), fill=rc)
            y += 62
            d.line([60, y, W - 60, y], fill=(215, 208, 192), width=1)
        y += 20

    # 情報カード
    for c in data.get("cards", []):
        card_top(c["title"], color=(84, 62, 30))
        for line in c["lines"]:
            for wl in wrap(line, F(28), W - 120):
                d.text((60, y + 10), wl, font=F(28), fill=INK)
                y += 42
            y += 2
        y += 22

    # 数字欄
    nums = data.get("numbers", [])
    if nums:
        card_top("数字で見る市場", color=(52, 84, 60))
        for k, v in nums:
            d.text((60, y + 10), k, font=F(28, bold=True), fill=INK)
            d.text((330, y + 10), v, font=F(28), fill=INK)
            y += 46
        y += 14

    # フッター（内容の直下に置き、キャンバスを最終高さに切り取る）
    ft_lines = wrap(data.get("footer", ""), F(22), W - 88)
    final_h = y + 30 + len(ft_lines) * 32 + 26
    d.line([44, y + 14, W - 44, y + 14], fill=(160, 152, 132), width=2)
    for i, line in enumerate(ft_lines):
        d.text((44, y + 28 + i * 32), line, font=F(22), fill=(110, 104, 92))

    img.crop((0, 0, W, final_h)).save(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
