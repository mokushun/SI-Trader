#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
現状想像図ジェネレータ v2（縦型 1080x1350・オールコード描画、2026-11-13 本番化）。
- データJSONのスキーマは現行と互換（追加フィールド: move, move_label, spx, mood）。
- 縦型 1080x1350（Threadsのフィードで大きく見える 4:5）。
- 空はグラデーション、人物は大型化＋影、吹き出しに尻尾と影、地獄は溶岩帯。
- フッターは安全マージン内の帯に置き、下端切れを構造的に無くす。
"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

W, H = 1080, 1350


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(draw, y0, y1, c_top, c_bot):
    for y in range(y0, y1):
        t = (y - y0) / max(1, (y1 - y0))
        draw.line([0, y, W, y], fill=lerp(c_top, c_bot, t))


def main() -> None:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    mood = data.get("mood", "down")  # down=夕焼けドラマ / up=朝焼け
    img = Image.new("RGB", (W, H), (20, 20, 40))
    d = ImageDraw.Draw(img, "RGBA")

    def F(size, bold=False):
        return ImageFont.truetype(BOLD if bold else REG, size)

    # ---- 空（グラデーション）----
    if mood == "down":
        vgrad(d, 0, 900, (38, 32, 74), (214, 96, 62))
        sun_c, sun_xy = (255, 176, 92), (880, 560)
    else:
        vgrad(d, 0, 900, (46, 84, 148), (146, 196, 232))
        sun_c, sun_xy = (255, 226, 140), (880, 500)
    # 太陽とグロー
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    sx, sy = sun_xy
    gd.ellipse([sx - 150, sy - 150, sx + 150, sy + 150], fill=sun_c + (110,))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([sx - 62, sy - 62, sx + 62, sy + 62], fill=sun_c)
    # 雲（半透明の横長楕円の重ね）
    for cx, cy, s in [(180, 300, 1.0), (520, 210, 0.7), (760, 330, 0.85), (300, 430, 0.6)]:
        for dx, dy, rx, ry in [(0, 0, 90, 26), (-60, 8, 60, 20), (60, 6, 66, 22)]:
            d.ellipse([cx + dx * s - rx * s, cy + dy * s - ry * s,
                       cx + dx * s + rx * s, cy + dy * s + ry * s], fill=(255, 255, 255, 46))

    # ---- ヘッダー帯 ----
    d.rectangle([0, 0, W, 128], fill=(13, 18, 43))
    d.rectangle([0, 128, W, 136], fill=(250, 190, 60))
    d.text((36, 18), "クロージングベル新聞", font=F(26, bold=True), fill=(250, 210, 110))
    title = data.get("title", "SPXの現状想像図")
    d.text((36, 54), title, font=F(46, bold=True), fill=(255, 255, 255))
    if data.get("issue"):
        t = data["issue"]
        d.text((W - 36 - d.textlength(t, font=F(28, bold=True)), 46), t,
               font=F(28, bold=True), fill=(255, 255, 255))

    # ---- 大数字ストリップ ----
    d.rectangle([0, 136, W, 252], fill=(250, 247, 238))
    move = data.get("move", "")
    spx = data.get("spx", "")
    label = data.get("move_label", "S&P500 前日比")
    d.text((36, 158), label, font=F(24, bold=True), fill=(90, 90, 90))
    if spx:
        d.text((36, 192), spx, font=F(40, bold=True), fill=(30, 30, 30))
    if move:
        col = (196, 40, 34) if move.strip().startswith(("−", "-")) else (24, 130, 62)
        f = F(76, bold=True)
        tw = d.textlength(move, font=f)
        d.text((W - 36 - tw, 158), move, font=f, fill=col)
        tri = "▼" if col == (196, 40, 34) else "▲"
        d.text((W - 36 - tw - 58, 178), tri, font=F(44, bold=True), fill=col)

    # ---- 地形 ----
    GROUND = 880          # 主地表
    HILL = 640            # 右上の丘の地表
    HELL = 1170           # 地獄帯の上端
    # 崖の岩盤（主地表から地獄まで、右側）
    d.polygon([(700, GROUND), (W, HILL), (W, HELL), (760, HELL), (724, 1010), (700, GROUND)],
              fill=(146, 104, 74))
    d.polygon([(700, GROUND), (W, HILL), (W, HILL + 26), (706, GROUND + 26)], fill=(170, 128, 92))
    # 主地面
    d.rectangle([0, GROUND, 706, HELL], fill=(104, 166, 84))
    d.rectangle([0, GROUND, 706, GROUND + 16], fill=(140, 200, 110))
    d.rectangle([0, HELL - 60, 706, HELL], fill=(146, 104, 74))  # 崖下の土
    # 丘（下は岩盤、表面だけ緑）
    d.rectangle([720, HILL, W, HELL], fill=(146, 104, 74))
    d.polygon([(720, HILL), (760, HELL), (700, GROUND), (706, GROUND)], fill=(146, 104, 74))
    d.rectangle([720, HILL, W, HILL + 18], fill=(140, 200, 110))
    # 地獄（溶岩グラデーション＋炎）
    vgrad(d, HELL, H, (120, 18, 12), (214, 62, 20))
    for x in range(6, W, 52):
        d.polygon([(x, HELL + 64), (x + 26, HELL + 6), (x + 52, HELL + 64)], fill=(238, 110, 30))
        d.polygon([(x + 12, HELL + 64), (x + 26, HELL + 28), (x + 40, HELL + 64)], fill=(250, 178, 52))

    # ---- 人物 ----
    def person(x, y, color, pose="stand", scale=1.35):
        r = int(20 * scale)
        # 足元の影
        d.ellipse([x - int(30 * scale), y + int(88 * scale), x + int(30 * scale), y + int(100 * scale)],
                  fill=(0, 0, 0, 60))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(248, 208, 164), outline=(50, 45, 40), width=3)
        # 目
        er = max(2, int(3 * scale))
        for ex in (-8, 8):
            d.ellipse([x + int(ex * scale) - er, y + int(1 * scale) - er,
                       x + int(ex * scale) + er, y + int(1 * scale) + er], fill=(40, 35, 30))
        by = y + r
        lw = max(5, int(9 * scale))
        body = tuple(color)
        d.line([x, by, x, by + int(52 * scale)], fill=body, width=int(15 * scale))
        if pose == "cheer":
            d.line([x, by + int(12 * scale), x - int(26 * scale), by - int(18 * scale)], fill=body, width=lw)
            d.line([x, by + int(12 * scale), x + int(26 * scale), by - int(18 * scale)], fill=body, width=lw)
        elif pose == "fall":
            d.line([x, by + int(10 * scale), x - int(30 * scale), by + int(2 * scale)], fill=body, width=lw)
            d.line([x, by + int(10 * scale), x + int(30 * scale), by - int(6 * scale)], fill=body, width=lw)
        else:
            d.line([x, by + int(12 * scale), x - int(24 * scale), by + int(34 * scale)], fill=body, width=lw)
            d.line([x, by + int(12 * scale), x + int(24 * scale), by + int(34 * scale)], fill=body, width=lw)
        d.line([x, by + int(52 * scale), x - int(20 * scale), by + int(90 * scale)], fill=(56, 56, 82), width=lw)
        d.line([x, by + int(52 * scale), x + int(20 * scale), by + int(90 * scale)], fill=(56, 56, 82), width=lw)

    def bubble(x, y, text, anchor="left", size=25):
        f = F(size, bold=True)
        lines = text.split("\n")
        tw = max(d.textlength(t, font=f) for t in lines)
        w, h = tw + 34, len(lines) * (size + 9) + 22
        if anchor == "right":
            x -= w
        # 影
        d.rounded_rectangle([x + 5, y + 6, x + w + 5, y + h + 6], radius=16, fill=(0, 0, 0, 70))
        d.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=(255, 255, 255), outline=(40, 40, 40), width=3)
        # 尻尾
        tx = x + (w - 34 if anchor == "right" else 22)
        d.polygon([(tx, y + h - 2), (tx + 22, y + h - 2), (tx + 6, y + h + 18)], fill=(255, 255, 255))
        for i, t in enumerate(lines):
            d.text((x + 17, y + 11 + i * (size + 9)), t, font=f, fill=(25, 25, 25))
        return w, h

    def sign(x, y, text, color):
        f = F(27, bold=True)
        w = d.textlength(text, font=f) + 34
        d.rectangle([x + w // 2 - 6, y + 44, x + w // 2 + 6, y + 84], fill=(96, 66, 38))
        d.rounded_rectangle([x + 4, y + 5, x + w + 4, y + 49], radius=8, fill=(0, 0, 0, 70))
        d.rounded_rectangle([x, y, x + w, y + 44], radius=8, fill=tuple(color), outline=(255, 255, 255), width=3)
        d.text((x + 17, y + 7), text, font=f, fill=(255, 255, 255))
        return w

    # ---- 左ゾーン ----
    if data.get("left_sign"):
        sign(44, 560, data["left_sign"]["text"], data["left_sign"].get("color", [30, 90, 40]))
    for i in range(data.get("left_people", 0)):
        person(120 + i * 118, 736, data.get("left_color", [40, 110, 60]), "cheer" if i < 2 else "stand")
    for i, b in enumerate(data.get("left_bubbles", [])):
        bubble(44 + i * 170, 330 + (i % 2) * 122, b, size=24)

    # ---- 中央ゾーン ----
    if data.get("mid_sign"):
        sign(414, 560, data["mid_sign"]["text"], data["mid_sign"].get("color", [110, 100, 40]))
    for i in range(data.get("mid_people", 0)):
        person(492 + i * 112, 736, data.get("mid_color", [200, 170, 40]), "stand")
    for i, b in enumerate(data.get("mid_bubbles", [])):
        bubble(452 + i * 10, 330 + i * 106, b, size=24)

    # ---- 丘 ----
    if data.get("hill_sign"):
        sign(724, 448, data["hill_sign"], [120, 60, 140])
    for i in range(data.get("hill_people", 0)):
        person(842 + i * 92, 512, [140, 80, 160], "cheer" if i % 2 == 0 else "stand", 1.12)
    hb = data.get("hill_bubbles", [])
    if len(hb) > 0:
        bubble(W - 36, 264, hb[0], anchor="right", size=24)
    if len(hb) > 1:
        bubble(W - 36, 340, hb[1], anchor="right", size=23)

    # ---- 崖プレート ----
    if data.get("cliff"):
        d.rounded_rectangle([150, 972, 560, 1078], radius=10, fill=(236, 218, 168), outline=(90, 70, 40), width=4)
        d.text((182, 986), data["cliff"]["name"], font=F(34, bold=True), fill=(96, 42, 18))
        d.text((182, 1030), data["cliff"]["height"], font=F(27), fill=(96, 42, 18))

    # ---- 落ちる人たち ----
    fpos = [(838, 800), (972, 958), (852, 1064)]
    for i, fp in enumerate(data.get("falling", [])[:3]):
        x, y = fpos[i]
        person(x, y, fp["color"], "fall", 1.25 if i < 2 else 1.1)
        if fp.get("bubble"):
            bubble(x - 16, y - 148, fp["bubble"], anchor="right", size=23)

    # ---- 地獄ラベル＋フッター帯（安全マージン内）----
    t = data.get("hell_label", "大外れ地獄")
    f = F(36, bold=True)
    d.text((W - 44 - d.textlength(t, font=f), HELL + 66), t, font=f, fill=(255, 226, 150))
    d.rectangle([0, H - 64, W, H], fill=(13, 18, 43))
    if data.get("footer1"):
        d.text((36, H - 50), data["footer1"], font=F(23, bold=True), fill=(255, 255, 255))
    if data.get("footer2"):
        f2 = F(21)
        d.text((W - 36 - d.textlength(data["footer2"], font=f2), H - 46), data["footer2"],
               font=f2, fill=(210, 214, 230))

    img.save(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
