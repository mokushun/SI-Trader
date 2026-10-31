#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
クロージングベル新聞「現状想像図」ジェネレータ。

使い方:
    python3 make_sozozu.py <data.json> <output.png>

data.json の形式（全フィールド必須ではない。無いゾーンは描かれない）:
{
  "issue": "10/31号",                      # 右上の号表記
  "title": "SPXの現状想像図",               # タイトル帯
  "left_sign":  {"text": "弱気派（的中）", "color": [30, 90, 40]},
  "left_people": 3,                         # 左ゾーンの人数
  "left_color": [40, 110, 60],
  "left_bubbles": ["Nasdaq −1.77%は\n警告だった", ...],   # 左から順に配置
  "mid_sign":   {"text": "中立派", "color": [110, 100, 40]},
  "mid_people": 1,
  "mid_color": [200, 170, 40],
  "mid_bubbles": ["..."],
  "hill_sign":  "高的見物の丘",
  "hill_people": 3,
  "hill_bubbles": ["弱気の2人が的中！", "強気58%って…"],
  "cliff": {"name": "決算ショックの崖", "height": "高さ −1.8%"},
  "falling": [                              # 崖下に落ちる人（上から順）
     {"color": [200, 60, 50], "bubble": "GEX +78億って\n言ったのに…"},
     {"color": [60, 90, 200], "bubble": "AIは買いだと\n思ったのに…"}
  ],
  "hell_label": "大外れ地獄",
  "footer1": "クロージングベル新聞  2026年10月31日朝刊",
  "footer2": "SPX 7,580.1（前日比 −1.82%）／答え合わせは全文ページで"
}

描画は PIL のみ。日本語フォントは Noto Sans CJK（VM標準）を使う。
数字は呼び出し側が判断ファイル・入力ファイルから転記すること（このスクリプトは計算しない）。
"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont

REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

W, H = 1280, 960


def main() -> None:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]

    img = Image.new("RGB", (W, H), (168, 205, 234))
    d = ImageDraw.Draw(img)

    def F(size, bold=False):
        return ImageFont.truetype(BOLD if bold else REG, size)

    # 地形
    d.rectangle([0, 430, W, H], fill=(196, 164, 120))
    d.rectangle([0, 430, 800, 470], fill=(150, 190, 110))
    d.rectangle([830, 300, W, 340], fill=(150, 190, 110))
    d.polygon([(800, 430), (880, 300), (880, 430)], fill=(196, 164, 120))
    for x in range(830, W, 46):
        d.polygon([(x, H), (x + 23, H - 90), (x + 46, H)], fill=(232, 90, 30))
        d.polygon([(x + 10, H), (x + 23, H - 55), (x + 36, H)], fill=(245, 170, 40))
    d.rectangle([830, H - 34, W, H], fill=(120, 30, 20))

    def person(x, y, color, pose="stand", scale=1.0):
        r = int(13 * scale)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(245, 205, 160), outline=(60, 60, 60), width=2)
        by = y + r
        lw = max(3, int(6 * scale))
        d.line([x, by, x, by + int(34 * scale)], fill=tuple(color), width=int(9 * scale))
        if pose == "cheer":
            d.line([x, by + 8, x - int(17 * scale), by - int(12 * scale)], fill=tuple(color), width=lw)
            d.line([x, by + 8, x + int(17 * scale), by - int(12 * scale)], fill=tuple(color), width=lw)
        elif pose == "fall":
            d.line([x, by + 6, x - int(20 * scale), by + 2], fill=tuple(color), width=lw)
            d.line([x, by + 6, x + int(20 * scale), by - 4], fill=tuple(color), width=lw)
        else:
            d.line([x, by + 8, x - int(16 * scale), by + int(22 * scale)], fill=tuple(color), width=lw)
            d.line([x, by + 8, x + int(16 * scale), by + int(22 * scale)], fill=tuple(color), width=lw)
        d.line([x, by + int(34 * scale), x - int(13 * scale), by + int(58 * scale)], fill=(70, 70, 90), width=lw)
        d.line([x, by + int(34 * scale), x + int(13 * scale), by + int(58 * scale)], fill=(70, 70, 90), width=lw)

    def bubble(x, y, text, anchor="left", size=21):
        f = F(size)
        lines = text.split("\n")
        w = max(d.textlength(t, font=f) for t in lines) + 22
        h = len(lines) * (size + 7) + 16
        if anchor == "right":
            x = x - w
        d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=(255, 255, 255), outline=(70, 70, 70), width=2)
        for i, t in enumerate(lines):
            d.text((x + 11, y + 8 + i * (size + 7)), t, font=f, fill=(30, 30, 30))

    def sign(x, y, text, color):
        f = F(22, bold=True)
        w = d.textlength(text, font=f) + 24
        d.rectangle([x, y, x + w, y + 38], fill=tuple(color), outline=(255, 255, 255), width=2)
        d.text((x + 12, y + 6), text, font=f, fill=(255, 255, 255))

    # タイトル帯
    d.polygon([(240, 18), (1040, 18), (1000, 78), (280, 78)], fill=(178, 30, 40))
    d.rectangle([230, 12, 1050, 84], outline=(250, 210, 60), width=4)
    t = data.get("title", "SPXの現状想像図")
    f = F(40, bold=True)
    d.text(((W - d.textlength(t, font=f)) / 2, 24), t, font=f, fill=(255, 240, 200))
    if data.get("issue"):
        d.text((1056, 34), data["issue"], font=F(20, bold=True), fill=(40, 40, 40))

    # 左ゾーン
    if data.get("left_sign"):
        sign(40, 120, data["left_sign"]["text"], data["left_sign"].get("color", [30, 90, 40]))
    for i in range(data.get("left_people", 0)):
        person(110 + i * 88, 335, data.get("left_color", [40, 110, 60]), "cheer" if i < 2 else "stand")
    for i, b in enumerate(data.get("left_bubbles", [])):
        bubble(60 + i * 160, 180 + (i % 2) * 26, b, size=20)

    # 中央ゾーン
    if data.get("mid_sign"):
        sign(430, 120, data["mid_sign"]["text"], data["mid_sign"].get("color", [110, 100, 40]))
    for i in range(data.get("mid_people", 0)):
        person(500 + i * 80, 350, data.get("mid_color", [200, 170, 40]), "stand")
    for i, b in enumerate(data.get("mid_bubbles", [])):
        bubble(430, 190 + i * 70, b, size=20)

    # 丘（高的見物）
    if data.get("hill_sign"):
        sign(880, 210, data["hill_sign"], [120, 60, 140])
    for i in range(data.get("hill_people", 0)):
        person(930 + i * 80, 265, [140, 80, 160], "cheer" if i % 2 == 0 else "stand", 0.85)
    hb = data.get("hill_bubbles", [])
    if len(hb) > 0:
        bubble(880, 120, hb[0], size=20)
    if len(hb) > 1:
        bubble(1085, 165, hb[1], anchor="right", size=19)

    # 崖プレート
    if data.get("cliff"):
        d.rectangle([560, 500, 800, 580], fill=(222, 205, 160), outline=(90, 70, 40), width=3)
        d.text((585, 512), data["cliff"]["name"], font=F(24, bold=True), fill=(90, 40, 20))
        d.text((610, 546), data["cliff"]["height"], font=F(21), fill=(90, 40, 20))

    # 落ちる人たち
    fy = [470, 620, 760]
    fx = [950, 1060, 905]
    for i, fp in enumerate(data.get("falling", [])[:3]):
        person(fx[i], fy[i], fp["color"], "fall", 1.0 if i < 2 else 0.9)
        if fp.get("bubble"):
            bubble(fx[i] - 20, fy[i] - 90, fp["bubble"], size=20)

    # 地獄ラベル・フッター
    t = data.get("hell_label", "大外れ地獄")
    d.text((W - 260, H - 78), t, font=F(26, bold=True), fill=(255, 220, 160))
    if data.get("footer1"):
        d.text((30, H - 46), data["footer1"], font=F(20, bold=True), fill=(60, 40, 20))
    if data.get("footer2"):
        d.text((30, H - 20), data["footer2"], font=F(16), fill=(70, 60, 50))

    img.save(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
