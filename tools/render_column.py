#!/usr/bin/env python3
"""ニャンちゅうの未来語り: 新聞コラム風の縦書き画像を生成するスクリプト。

使い方:
    python3 render_column.py \
        --text column_text_2026-10.md \
        --heading "家に人型ロボットが当たり前になる日" \
        --photo ../S__91226114.jpg \
        --date "2026年10月" \
        --out ../columns/2026-10-xxxxxxxxxxxx

本文テキストは段落を空行で区切ったプレーンテキスト(UTF-8)。
出力は --out に "-1.png" "-2.png" ... を付けた縦長PNG(幅1040px, 高さ最大3120px)。
縦書き表示には Pillow の direction="ttb" + features=["vert"] (libraqm) を使っており、
長音「ー」「〜」「…」の縦向き、括弧「」『』()の縦書き用の向き、
小さい仮名や読点の右上寄せは libraqm のvertical shapingに任せている。
半角英数字は全角に変換して描画する(縦中横の代わり)。
"""
import argparse
import os
import random
import string
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FONT = os.path.join(HERE, "fonts", "NotoSerifJP-Variable.ttf")

PAGE_W = 1040
PAGE_H_MAX = 3120  # LINE Flex Message の画像は縦横比 1:3 まで

PAPER = (247, 243, 232)
INK = (36, 31, 27)
RULE = (120, 110, 95)

MARGIN_L = 40
MARGIN_R = 40

CELL = 46                 # 本文1文字の大きさ(px) / 列内の文字送り
COL_PITCH = round(CELL * 1.62)   # 列と列の間隔(文字サイズの約1.6倍)
CHARS_PER_BAND = 15        # 1段あたりの行(列)の文字数
BAND_H = CHARS_PER_BAND * CELL
BAND_GAP = 40               # 段と段の間の余白(罫線を含む)

HEAD_CELL = 64              # 見出し文字の大きさ
HEAD_PITCH = round(HEAD_CELL * 1.5)

MASTHEAD_Y0 = 28
MASTHEAD_H = 132
BYLINE_H = 232
HEADER_RULE_GAP = 24
FOOTER_H = 70


def fullwidth(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if ch == " ":
            out.append("　")
        elif 0x21 <= o <= 0x7E:
            out.append(chr(o + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def load_paragraphs(path: str):
    raw = open(path, encoding="utf-8").read()
    blocks = [b.strip("\n") for b in raw.split("\n\n")]
    paras = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        joined = "".join(line.strip() for line in b.splitlines())
        paras.append(fullwidth(joined))
    return paras


def get_font(size: int, weight: str, font_path: str) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(font_path, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def draw_vertical_chunk(draw: ImageDraw.ImageDraw, cx: int, y_top: int, text: str, font):
    """1列分の文字列を中央寄せで縦書き描画する。"""
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font, direction="ttb", features=["vert"])
    w = bbox[2] - bbox[0]
    x = cx - w / 2 - bbox[0]
    draw.text((x, y_top), text, font=font, fill=INK, direction="ttb", features=["vert"])


def circular_photo(path: str, diameter: int) -> Image.Image:
    im = Image.open(path).convert("RGB")
    side = min(im.size)
    left = (im.width - side) // 2
    top = (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    ring = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((1, 1, diameter - 2, diameter - 2), outline=INK, width=4)
    out.alpha_composite(ring)
    return out


class Page:
    def __init__(self, page_no: int, is_first: bool, font_path: str, title: str,
                 photo_img, byline: str, date_text: str):
        self.page_no = page_no
        self.font_path = font_path
        self.img = Image.new("RGB", (PAGE_W, PAGE_H_MAX), PAPER)
        self.draw = ImageDraw.Draw(self.img)
        self.content_right = PAGE_W - MARGIN_R
        self.content_left = MARGIN_L
        self.max_y_used = 0

        if is_first:
            self._draw_masthead(title, photo_img, byline)
            self.content_top = MASTHEAD_Y0 + MASTHEAD_H + HEADER_RULE_GAP + BYLINE_H + HEADER_RULE_GAP
        else:
            self._draw_continuation_header(title)
            self.content_top = MASTHEAD_Y0 + 70

        self.footer_top = PAGE_H_MAX - FOOTER_H
        self.date_text = date_text

        self.band_top = self.content_top
        self.cursor_x = self.content_right
        self.first_band_drawn = False

    def _draw_masthead(self, title, photo_img, byline):
        d = self.draw
        x0, y0 = MARGIN_L, MASTHEAD_Y0
        x1, y1 = PAGE_W - MARGIN_R, MASTHEAD_Y0 + MASTHEAD_H
        d.rectangle((x0, y0, x1, y1), outline=INK, width=5)
        d.rectangle((x0 + 8, y0 + 8, x1 - 8, y1 - 8), outline=INK, width=2)
        title_font = get_font(66, "Bold", self.font_path)
        tb = d.textbbox((0, 0), title, font=title_font)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
        tx = x0 + (x1 - x0 - tw) / 2 - tb[0]
        ty = y0 + (y1 - y0 - th) / 2 - tb[1]
        d.text((tx, ty), title, font=title_font, fill=INK)

        by0 = y1 + HEADER_RULE_GAP
        diameter = 152
        px = PAGE_W - MARGIN_R - diameter
        py = by0 + 6
        if photo_img is not None:
            self.img.paste(photo_img, (px, py), photo_img)
        cap_font = get_font(22, "Regular", self.font_path)
        cap_y = py + diameter + 12
        for i, line in enumerate(byline.split("\n")):
            cb = d.textbbox((0, 0), line, font=cap_font)
            cw = cb[2] - cb[0]
            cx = px + diameter / 2 - cw / 2 - cb[0]
            cx = min(cx, PAGE_W - MARGIN_R - cw - cb[0] - 4)
            d.text((cx, cap_y + i * 30), line, font=cap_font, fill=INK)

        rule_y = by0 + BYLINE_H
        d.line((MARGIN_L, rule_y, PAGE_W - MARGIN_R, rule_y), fill=INK, width=4)
        d.line((MARGIN_L, rule_y + 7, PAGE_W - MARGIN_R, rule_y + 7), fill=INK, width=1)

    def _draw_continuation_header(self, title):
        d = self.draw
        f = get_font(32, "Bold", self.font_path)
        text = title + "(つづき)"
        tb = d.textbbox((0, 0), text, font=f)
        tw = tb[2] - tb[0]
        d.text(((PAGE_W - tw) / 2 - tb[0], MASTHEAD_Y0), text, font=f, fill=INK)
        rule_y = MASTHEAD_Y0 + 55
        d.line((MARGIN_L, rule_y, PAGE_W - MARGIN_R, rule_y), fill=INK, width=2)

    def band_bottom(self, band_h):
        return self.band_top + band_h

    def ensure_room_for_band(self, band_h) -> bool:
        """現在の段に収まらない場合、新しい段(または新しいページ)が必要かを判定して整える。
        戻り値 True なら新規ページが必要。
        """
        if self.band_bottom(band_h) > self.footer_top:
            return True
        return False

    def new_band(self, band_h):
        if self.first_band_drawn:
            rule_y = self.band_top + band_h + BAND_GAP // 2
            self.draw.line((self.content_left, rule_y, self.content_right, rule_y), fill=RULE, width=2)
            self.band_top = self.band_top + band_h + BAND_GAP
        self.first_band_drawn = True
        self.cursor_x = self.content_right

    def draw_footer_and_crop(self):
        d = self.draw
        y_bottom = min(self.max_y_used + FOOTER_H + 40, PAGE_H_MAX)
        y_bottom = max(y_bottom, self.content_top + FOOTER_H + 40)
        f = get_font(24, "Regular", self.font_path)
        tb = d.textbbox((0, 0), self.date_text, font=f)
        tw = tb[2] - tb[0]
        date_y = y_bottom - FOOTER_H + 20
        d.line((MARGIN_L, y_bottom - FOOTER_H, PAGE_W - MARGIN_R, y_bottom - FOOTER_H), fill=RULE, width=1)
        d.text(((PAGE_W - tw) / 2 - tb[0], date_y), self.date_text, font=f, fill=INK)
        return self.img.crop((0, 0, PAGE_W, y_bottom))


def render(paragraphs, heading, title, photo_path, byline, date_text, font_path, out_prefix):
    photo_img = circular_photo(photo_path, 152) if photo_path else None

    pages = []
    page_no = 1
    page = Page(page_no, True, font_path, title, photo_img, byline, date_text)
    page.new_band(BAND_H)

    # --- 見出し(本文より大きい縦書き、右端) ---
    head_rows_cap = max(1, BAND_H // HEAD_CELL)
    head_font = get_font(HEAD_CELL, "Bold", font_path)
    hi = 0
    while hi < len(heading):
        chunk = heading[hi:hi + head_rows_cap]
        hi += head_rows_cap
        if page.cursor_x - HEAD_PITCH / 2 < page.content_left:
            pages.append(page.draw_footer_and_crop())
            page_no += 1
            page = Page(page_no, False, font_path, title, None, byline, date_text)
            page.new_band(BAND_H)
        draw_vertical_chunk(page.draw, page.cursor_x - HEAD_PITCH / 2, page.band_top, chunk, head_font)
        page.max_y_used = max(page.max_y_used, page.band_top + len(chunk) * HEAD_CELL)
        page.cursor_x -= HEAD_PITCH

    page.cursor_x -= COL_PITCH // 3  # 見出しと本文の間の余白

    body_font = get_font(CELL, "Regular", font_path)
    row_cap = CHARS_PER_BAND

    for para in paragraphs:
        indent = True
        pos = 0
        while pos < len(para):
            cap = row_cap - (1 if indent else 0)
            chunk = para[pos:pos + cap]
            pos += cap

            if page.cursor_x - COL_PITCH / 2 < page.content_left:
                if page.ensure_room_for_band(BAND_H):
                    pages.append(page.draw_footer_and_crop())
                    page_no += 1
                    page = Page(page_no, False, font_path, title, None, byline, date_text)
                page.new_band(BAND_H)

            y0 = page.band_top + (CELL if indent else 0)
            draw_vertical_chunk(page.draw, page.cursor_x - COL_PITCH / 2, y0, chunk, body_font)
            page.max_y_used = max(page.max_y_used, y0 + len(chunk) * CELL)
            page.cursor_x -= COL_PITCH
            indent = False

        # 次の段落は必ず新しい列から始める(縦書きの行替えに相当)
        if page.cursor_x - COL_PITCH / 2 < page.content_left:
            if page.ensure_room_for_band(BAND_H):
                pages.append(page.draw_footer_and_crop())
                page_no += 1
                page = Page(page_no, False, font_path, title, None, byline, date_text)
            page.new_band(BAND_H)

    pages.append(page.draw_footer_and_crop())

    out_paths = []
    for i, img in enumerate(pages, start=1):
        path = f"{out_prefix}-{i}.png"
        img.save(path, "PNG")
        out_paths.append(path)
    return out_paths


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True, help="本文テキストファイル(段落は空行区切り)")
    ap.add_argument("--heading", required=True, help="見出し(問い)")
    ap.add_argument("--title", default="ニャンちゅうの未来語り", help="題字")
    ap.add_argument("--photo", default=None, help="顔写真(正方形に中央トリミングして丸く使用)")
    ap.add_argument("--byline", default="ニャンちゅう\n(自称5歳・研究好き)", help="肩書き(\\nで改行)")
    ap.add_argument("--date", default="2026年10月", help="日付(フッターに表示)")
    ap.add_argument("--font", default=DEFAULT_FONT, help="縦書きに使うフォント(可変フォント)")
    ap.add_argument("--out", required=True, help="出力プレフィックス(-1.png などが付く)")
    args = ap.parse_args()

    paragraphs = load_paragraphs(args.text)
    heading = fullwidth(args.heading)
    byline = fullwidth(args.byline)
    date_text = fullwidth(args.date)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    paths = render(paragraphs, heading, args.title, args.photo, byline, date_text,
                    args.font, args.out)
    for p in paths:
        im = Image.open(p)
        print(f"{p}\t{im.size[0]}x{im.size[1]}\t{os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()
