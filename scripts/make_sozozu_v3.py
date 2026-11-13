#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
現状想像図ジェネレータ v3: AI生成イラスト（文字なし）＋ PILで巨大タイポグラフィ合成（2026-11-13 本番化）。
使い方: python3 make_sozozu_v3_proto.py <bg.webp> <data.json> <output.png>
data.json: {kicker, line1, line2, move, sub, footer_left, footer_right}

シーンテンプレート（背景イラスト生成プロンプトの型）:
- tenraku（転落）: 外れた側のキャラが崖から落ちる。強気が外れたら牛の角、弱気が外れたらクマ耳。勝った側・様子見は丘の上
- zenmetsu（全滅）: 全員が崖下・炎の中
- gyakuten（的中）: 的中した側が崖の上で万歳、外れた側は下
- yosuumi（様子見）: 全員が丘の上で静観、嵐は遠く
共通: 縦型4:5、上半分は文字を載せるため暗く空ける、文字・数字・看板は一切入れない
"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
W, H = 1080, 1350


def F(size):
    return ImageFont.truetype(BOLD, size)


def glow_text(base, xy, text, font, fill, glow_color, stroke=6, blur=18, passes=2):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for _ in range(passes):
        ld.text(xy, text, font=font, fill=glow_color + (255,), stroke_width=stroke + 10, stroke_fill=glow_color + (255,))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)
    d = ImageDraw.Draw(base)
    d.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(20, 8, 8))


def main() -> None:
    bg_path, data_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
    data = json.load(open(data_path, encoding="utf-8"))
    bg = Image.open(bg_path).convert("RGB")
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
    x0 = (bg.width - W) // 2
    y0 = 0
    img = bg.crop((x0, y0, x0 + W, y0 + H)).convert("RGBA")

    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(0, 860):
        a = int(120 * (1 - y / 860))
        sd.line([0, y, W, y], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img, "RGBA")

    kicker = data.get("kicker", "【答え合わせ】")
    kf = F(46)
    kw = d.textlength(kicker, font=kf)
    d.rectangle([36, 38, 36 + kw + 44, 116], fill=(178, 16, 28))
    d.text((58, 52), kicker, font=kf, fill=(255, 255, 255))

    glow_text(img, (40, 138), data["line1"], F(196), (255, 255, 255), (200, 30, 30), stroke=8)
    if data.get("line2"):
        glow_text(img, (40, 356), data["line2"], F(238), (255, 96, 54), (255, 190, 60), stroke=9)

    if data.get("move"):
        glow_text(img, (44, 640), data["move"], F(148), (186, 228, 255), (70, 140, 255), stroke=6, blur=14)

    sub = data.get("sub", "")
    if sub:
        sf = ImageFont.truetype(BOLD, 35)
        sw = d.textlength(sub, font=sf)
        d.rounded_rectangle([36, 824, 36 + sw + 36, 884], radius=10, fill=(0, 0, 0, 150))
        d.text((54, 836), sub, font=sf, fill=(255, 255, 255))

    d.rectangle([0, H - 78, W, H], fill=(10, 10, 16, 235))
    fl, fr = data.get("footer_left", ""), data.get("footer_right", "")
    if fl:
        d.text((36, H - 56), fl, font=F(27), fill=(255, 255, 255))
    if fr:
        f = F(27)
        d.text((W - 36 - d.textlength(fr, font=f), H - 56), fr, font=f, fill=(250, 210, 110))

    img.convert("RGB").save(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
