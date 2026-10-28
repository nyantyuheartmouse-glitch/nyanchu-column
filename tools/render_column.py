#!/usr/bin/env python3
"""ニャンちゅうの未来語り: 新聞コラム風の縦書き画像を生成するスクリプト。

使い方:
    # 専用venv(Pillow + libraqm)を用意してから実行する
    python3 -m venv ~/.venv_column
    ~/.venv_column/bin/pip install pillow

    ~/.venv_column/bin/python3 render_column.py \
        --text column_text_2026-10.txt \
        --heading "家に人型ロボットが当たり前になる日" \
        --photo ../S__91226114.jpg \
        --date "2026年10月" \
        --out ../columns/2026-10-xxxxxxxxxxxx

本文テキストは段落を空行で区切ったプレーンテキスト(UTF-8)。
段落内で改行だけされている行は区切り文字なしで連結されるので、
文の切れ目には必ず終端記号を置くこと。コラムは新聞調なので句点「。」を使う。
「!」は本当に驚いたり強調したい箇所だけに留め、「…」の多用は避ける。
出力は --out に "-1.png" "-2.png" ... を付けた縦長PNG(幅1040px, 高さ最大3120px)。
縦書き表示には Pillow の direction="ttb" + features=["vert"] (libraqm) を使っており、
長音「ー」「〜」「…」の縦向き、括弧「」『』()の縦書き用の向き、
小さい仮名や読点の右上寄せは libraqm のvertical shapingに任せている。
半角英数字は全角に変換して描画する。
2桁の数字の並び(「(10)」のような参照番号や「2027」のような4桁の年号を2桁ずつ)は、
縦中横(1マスの中に横向きで2文字並べる)で描画し、列の途中で分割しない。
"""
import argparse
import os
import random
import re
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

CELL = 46                 # 本文1文字(1行)の大きさ(px) / 列内の文字送り
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

FOOTER_GAP = 40              # 本文下端と罫線の間の余白
FOOTER_H = 70                # 罫線から画像下端まで(日付を含む領域)

ZEN_DIGITS = "０１２３４５６７８９"
ZEN_DIGIT_SET = set(ZEN_DIGITS)


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


_MARK_SPACING_RE = re.compile(r"([！？])(?!$)(?!　)")


def add_mark_spacing(s: str) -> str:
    """縦書きでは「!」「?」の後ろに全角スペース1つ分の間を空ける(文末を除く)。
    既にスペースがある場合は足さず、万一の連続スペースは1つにまとめる。"""
    s = _MARK_SPACING_RE.sub(r"\1　", s)
    s = re.sub(r"　{2,}", "　", s)
    return s


def load_paragraphs(path: str):
    raw = open(path, encoding="utf-8").read()
    blocks = [b.strip("\n") for b in raw.split("\n\n")]
    paras = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        joined = "".join(line.strip() for line in b.splitlines())
        paras.append(add_mark_spacing(fullwidth(joined)))
    return paras


def get_font(size: int, weight: str, font_path: str) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(font_path, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


# --- 本文トークン化 -----------------------------------------------------
# 段落文字列を「1マス分の描画単位(row)」の列に変換する。
# 通常の文字は1マス=1文字。
# 「（１０）」のような括弧付き参照番号や、2桁以上の全角数字の並びは、
# 数字を2桁ずつ縦中横(1マスに横並びで2文字)にまとめ、
# 括弧を含めて1つの塊(group)として列の途中で分割しないようにする。

_PAREN_DIGITS_RE = re.compile(r"[（(]([０-９0-9]+)[）)]")


def _digit_pair_rows(run: str, group: int):
    rows = []
    k = 0
    n = len(run)
    if n % 2 == 1:
        rows.append({"text": run[0], "tatechuyoko": False, "group": group})
        k = 1
    while k < n:
        rows.append({"text": run[k:k + 2], "tatechuyoko": True, "group": group})
        k += 2
    return rows


def tokenize_paragraph(text: str):
    rows = []
    group_id = 0
    i = 0
    n = len(text)
    while i < n:
        m = _PAREN_DIGITS_RE.match(text, i)
        if m:
            gid = group_id
            group_id += 1
            rows.append({"text": text[i], "tatechuyoko": False, "group": gid})  # （
            rows.extend(_digit_pair_rows(m.group(1), gid))
            rows.append({"text": text[m.end() - 1], "tatechuyoko": False, "group": gid})  # ）
            i = m.end()
            continue
        if text[i] in ZEN_DIGIT_SET:
            j = i
            while j < n and text[j] in ZEN_DIGIT_SET:
                j += 1
            run = text[i:j]
            if len(run) >= 2:
                gid = group_id
                group_id += 1
                rows.extend(_digit_pair_rows(run, gid))
            else:
                rows.append({"text": run, "tatechuyoko": False, "group": None})
            i = j
            continue
        rows.append({"text": text[i], "tatechuyoko": False, "group": None})
        i += 1
    return rows


def _group_run_length(rows, start_idx):
    """rows[start_idx] が属する group が、そこから何マス続くか。"""
    gid = rows[start_idx]["group"]
    if gid is None:
        return 1
    j = start_idx
    while j < len(rows) and rows[j]["group"] == gid:
        j += 1
    return j - start_idx


def draw_vertical_chunk(draw: ImageDraw.ImageDraw, cx: int, y_top: int, text: str, font):
    """1マス分の文字を中央寄せで縦書き描画する。"""
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font, direction="ttb", features=["vert"])
    w = bbox[2] - bbox[0]
    x = cx - w / 2 - bbox[0]
    draw.text((x, y_top), text, font=font, fill=INK, direction="ttb", features=["vert"])


def draw_tatechuyoko(draw: ImageDraw.ImageDraw, cx: int, y_top: int, text: str, font, cell: int):
    """2桁の数字などを1マスの中に横向きで並べて描画する(縦中横)。"""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = cx - w / 2 - bbox[0]
    y = y_top + (cell - h) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=INK)


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
        # このページで実際に描画した文字数(縦中横は2文字分として数える)。
        self.chars_drawn = 0

        if is_first:
            self._draw_masthead(title, photo_img, byline)
            self.content_top = MASTHEAD_Y0 + MASTHEAD_H + HEADER_RULE_GAP + BYLINE_H + HEADER_RULE_GAP
        else:
            self._draw_continuation_header(title)
            self.content_top = MASTHEAD_Y0 + 70

        # 段の下端がここを超えてはいけない(この下に余白+罫線+日付の領域を確保する)。
        self.footer_top = PAGE_H_MAX - FOOTER_GAP - FOOTER_H
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

    def prospective_band_top(self, band_h):
        """まだ新しい段を実際には作らず、new_band() が置く位置を先読みする。"""
        if self.first_band_drawn:
            return self.band_top + band_h + BAND_GAP
        return self.band_top

    def ensure_room_for_band(self, band_h) -> bool:
        """これから作る段(まだ new_band() していない)が、段の下に必要な
        余白+罫線+日付の領域を含めてこのページに収まるかを判定する。
        戻り値 True なら新規ページが必要。
        """
        top = self.prospective_band_top(band_h)
        return top + band_h > self.footer_top

    def new_band(self, band_h):
        if self.first_band_drawn:
            rule_y = self.band_top + band_h + BAND_GAP // 2
            self.draw.line((self.content_left, rule_y, self.content_right, rule_y), fill=RULE, width=2)
            self.band_top = self.band_top + band_h + BAND_GAP
        self.first_band_drawn = True
        self.cursor_x = self.content_right

    def draw_footer_and_crop(self, is_last: bool):
        d = self.draw
        content_bottom = max(self.max_y_used, self.content_top)
        rule_y = content_bottom + FOOTER_GAP
        y_bottom = min(rule_y + FOOTER_H, PAGE_H_MAX)
        d.line((MARGIN_L, rule_y, PAGE_W - MARGIN_R, rule_y), fill=RULE, width=1)
        if is_last:
            f = get_font(24, "Regular", self.font_path)
            tb = d.textbbox((0, 0), self.date_text, font=f)
            tw = tb[2] - tb[0]
            date_y = rule_y + 20
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
            pages.append(page.draw_footer_and_crop(False))
            page_no += 1
            page = Page(page_no, False, font_path, title, None, byline, date_text)
            page.new_band(BAND_H)
        draw_vertical_chunk(page.draw, page.cursor_x - HEAD_PITCH / 2, page.band_top, chunk, head_font)
        page.max_y_used = max(page.max_y_used, page.band_top + len(chunk) * HEAD_CELL)
        page.cursor_x -= HEAD_PITCH

    page.cursor_x -= COL_PITCH // 3  # 見出しと本文の間の余白

    body_font = get_font(CELL, "Regular", font_path)
    tcy_font = get_font(round(CELL * 0.56), "Regular", font_path)
    row_cap = CHARS_PER_BAND

    total_chars_in = sum(len(p) for p in paragraphs)
    total_chars_drawn = 0

    for para in paragraphs:
        rows = tokenize_paragraph(para)
        indent = True
        ri = 0
        n_rows = len(rows)
        while ri < n_rows:
            cap = row_cap - (1 if indent else 0)

            if page.cursor_x - COL_PITCH / 2 < page.content_left:
                if page.ensure_room_for_band(BAND_H):
                    pages.append(page.draw_footer_and_crop(False))
                    page_no += 1
                    page = Page(page_no, False, font_path, title, None, byline, date_text)
                page.new_band(BAND_H)

            # このマスから cap 個ぶんのマスを積む。ただし group の途中で
            # 列が切れる場合は、その group ごと次の列へ送る。
            taken = 0
            while taken < cap and ri + taken < n_rows:
                item = rows[ri + taken]
                if item["group"] is not None:
                    gap_len = _group_run_length(rows, ri + taken)
                    if taken > 0 and taken + gap_len > cap:
                        # 既にこの列に文字がある場合のみ、塊ごと次列へ送る。
                        break
                taken += 1
            if taken == 0:
                taken = 1  # 塊そのものが1列の容量より大きい場合のフォールバック

            y0 = page.band_top + (CELL if indent else 0)
            y = y0
            for k in range(taken):
                item = rows[ri + k]
                cx = page.cursor_x - COL_PITCH / 2
                if item["tatechuyoko"]:
                    draw_tatechuyoko(page.draw, cx, y, item["text"], tcy_font, CELL)
                else:
                    draw_vertical_chunk(page.draw, cx, y, item["text"], body_font)
                total_chars_drawn += len(item["text"])
                page.chars_drawn += len(item["text"])
                y += CELL
            page.max_y_used = max(page.max_y_used, y)
            assert y0 + taken * CELL <= page.footer_top + FOOTER_GAP + FOOTER_H, "段が footer 領域にはみ出した"

            page.cursor_x -= COL_PITCH
            ri += taken
            indent = False

        # 次の段落はここで区切りが付く(次のチャンクは自動的に新しい列から始まる)。
        if page.cursor_x - COL_PITCH / 2 < page.content_left:
            if page.ensure_room_for_band(BAND_H):
                pages.append(page.draw_footer_and_crop(False))
                page_no += 1
                page = Page(page_no, False, font_path, title, None, byline, date_text)
            page.new_band(BAND_H)

    pages.append(page.draw_footer_and_crop(True))

    if total_chars_drawn != total_chars_in:
        raise AssertionError(
            f"配置した文字数({total_chars_drawn})が本文の文字数({total_chars_in})と一致しないニャ"
        )

    out_paths = []
    for i, img in enumerate(pages, start=1):
        path = f"{out_prefix}-{i}.png"
        img.save(path, "PNG")
        out_paths.append(path)
    return out_paths, total_chars_drawn, total_chars_in


def verify_images(out_paths, page_h_max=PAGE_H_MAX):
    """出力画像が高さ制限内に収まっているかの事後チェック。"""
    for p in out_paths:
        with Image.open(p) as im:
            w, h = im.size
            assert w == PAGE_W, f"{p}: 幅が {w} (期待値 {PAGE_W})"
            assert h <= page_h_max, f"{p}: 高さ {h} が上限 {page_h_max} を超えているニャ"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", required=True, help="本文テキストファイル(段落は空行区切り)")
    ap.add_argument("--heading", required=True, help="見出し(問い)")
    ap.add_argument("--title", default="ニャンちゅうの未来語り", help="題字")
    ap.add_argument("--photo", default=None, help="顔写真(正方形に中央トリミングして丸く使用)")
    ap.add_argument("--byline", default="ニャンちゅう\n(自称5歳・研究好き)", help="肩書き(\\nで改行)")
    ap.add_argument("--date", default="2026年10月", help="日付(最後のページのフッターに表示)")
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

    paths, drawn, expected = render(paragraphs, heading, args.title, args.photo, byline, date_text,
                                     args.font, args.out)
    verify_images(paths)
    print(f"配置した文字数: {drawn} / 本文の文字数: {expected} -> OK")
    for p in paths:
        im = Image.open(p)
        print(f"{p}\t{im.size[0]}x{im.size[1]}\t{os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()
