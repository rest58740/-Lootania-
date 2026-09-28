"""
裝備擷取（像 POE 的 Ctrl+C）＋詞綴表

在遊戲裡把滑鼠移到裝備上，按擷取快捷鍵（預設 F9，可自訂）：
  1. 截下一般的裝備說明（有秘藏能力的實際數值）
  2. 自動按住 Tab 截下詳細資訊（前綴/後綴、T 級、該 T 級的數值範圍），再放開
  3. 用 Windows 內建 OCR 讀文字，跳出確認視窗讓你修正讀錯的地方
  4. 確認後加到背包，並把每條詞綴記到詞綴表（affix_db.json），慢慢累積出 T1～T10 的範圍

只有找到裝備說明時才會按 Tab（沒有說明框時按 Tab 會切換道具欄分頁）。
"""
import asyncio
import csv
import datetime
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import cv2
import keyboard
import mss
import numpy as np
import win32api
from PIL import Image, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "capture_settings.json")
DB_FILE = os.path.join(HERE, "affix_db.json")
CAP_DIR = os.path.join(HERE, "captures")
FONT = "Microsoft JhengHei"

DEFAULT_SETTINGS = dict(hotkey="f9", auto_tab=True)
# 遊戲裡常用的鍵，設成擷取快捷鍵會衝突
GAME_KEYS = {"ctrl", "shift", "alt", "tab", "delete", "end", "page up", "page down", "insert", "home",
             "enter", "esc", "space", "up", "down", "left", "right"}

# 裝備分類 → 角色狀態的部位
PART_MAP = {"帽子": "頭盔", "頭盔": "頭盔", "上衣": "衣服", "套服": "衣服", "衣服": "衣服",
            "褲裙": "褲子", "褲子": "褲子", "鞋子": "鞋子", "手套": "手套", "披風": "披風",
            "耳環": "耳環", "戒指": "戒指", "墜飾": "墜飾", "項鍊": "墜飾", "腰帶": "腰帶",
            "臉飾": "臉飾", "眼飾": "眼鏡", "耳飾": "耳環", "眼鏡": "眼鏡", "勳章": "勳章",
            "盾牌": "副手", "箭矢": "副手", "飛鏢": "副手"}

KIND_RE = r"^[\[\(（［【]?(前|後)[\]\)）］】]?"
DETAIL_RE = re.compile(KIND_RE + r"(.*?)[Tt](\d{1,2})$")
# 詳細資訊的一行：[前]荊棘傷害：+(46~54)%  T6；特殊裝備沒有前後綴和 T 級，只有範圍
DETAIL_ANY = re.compile(r"^(?:[\[\(（［【]?(前|後)[\]\)）］】])?(.*?)(?:[Tt](\d{1,2}))?$")
RANGE_HINT = re.compile(r"\+\(|\(\s*-?[\d.]*\s*[~～]|[~～]\s*-?[\d.]+\s*\)")
RANGE_RE = re.compile(r"^(.*?)：?\+?\(?(-?[\d.]+)[~～](-?[\d.]+)\)?(%?)$")
VALUE_RE = re.compile(r"^(.*?)：?\+?(-?[\d.]+)(%?)$")
KIND_NAME = {"前": "前綴", "後": "後綴", "": "特殊", "傳": "傳奇"}

# 詞綴表的分類：(大類, [(中類或分類, [遊戲的「裝備分類／武器分類」寫法...] 或 None)])
# 主武器、副武器底下每種武器各自一類；其他的一個名稱就是一類
CATEGORY_TREE = [
    ("武器類（三武）", [
        ("主武器", ["單手劍", "雙手劍", "單手斧", "雙手斧", "單手棍", "雙手棍", "短劍", "槍矛", "長杖", "短杖",
                  "弓弩", "拳套", "指虎", "火槍"]),
        ("副武器", ["盾牌", "箭矢", "飛鏢", "羽毛筆", "寶珠", "魔法箭筒"]),
        ("能源（徽章）", None),
    ]),
    ("防具類", [("帽子", None), ("上衣", None), ("套服", None), ("褲子（下褲）", None), ("褲裙", None),
               ("鞋子", None), ("手套", None), ("披風", None), ("肩章", None)]),
    ("飾品類與其他", [("眼飾", None), ("臉飾", None), ("耳環", None), ("項鍊", None), ("腰帶", None), ("戒指", None),
                  ("口袋道具", None), ("勳章", None), ("稱號", None)]),
]
# 遊戲裡的其他寫法 → 分類
CATEGORY_ALIAS = {"頭盔": "帽子", "褲子": "褲子（下褲）", "下褲": "褲子（下褲）", "耳飾": "耳環", "眼鏡": "眼飾",
                  "墜飾": "項鍊", "徽章": "能源（徽章）", "能源": "能源（徽章）", "肩膀": "肩章", "弓": "弓弩", "弩": "弓弩"}
# 紀錄沒有分類時用部位推測
PART_CATEGORY = {"頭盔": "帽子", "衣服": "上衣", "褲子": "褲子（下褲）", "耳環": "耳環", "墜飾": "項鍊", "眼鏡": "眼飾"}


def category_leaves():
    """[(大類, 中類或 None, 分類)]"""
    out = []
    for big, subs in CATEGORY_TREE:
        for name, kinds in subs:
            if kinds:
                out += [(big, name, k) for k in kinds]
            else:
                out.append((big, None, name))
    return out


def canon_category(r):
    """紀錄的分類（去掉 OCR 雜字、換成統一的寫法；沒有分類就用部位推測）"""
    cat = re.sub(r"^[^一-鿿]+|[^一-鿿]+$", "", r.get("category") or "")
    cat = CATEGORY_ALIAS.get(cat, cat)
    leaves = {c for *_, c in category_leaves()}
    if cat in leaves:
        return cat
    part = r.get("part") or ""
    if cat and part in ("武器", "副手"):
        return cat                                  # 表上沒有的武器種類：照遊戲的寫法自己一類
    return PART_CATEGORY.get(part) or (part if part in leaves else "未分類")
TIERS = list(range(10, 0, -1)) + [0]        # 0 = 沒有 T 級的特殊詞綴


# ---------------------------------------------------------------- 設定、資料庫
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)


def affix_key(parts):
    return "＋".join(p["name"] + ("%" if p["pct"] else "") for p in parts)


def affix_label(parts):
    return "＋".join(p["name"] + ("（%）" if p["pct"] else "") for p in parts)


def range_text(parts):
    out = []
    for p in parts:
        if p.get("lo") is None:
            continue
        pct = "%" if p["pct"] else ""
        out.append(f"{p['lo']:g}~{p['hi']:g}{pct}")        # 固定數值也寫成範圍：7~7%、1~1
    return "，".join(out)


def add_records(records):
    db = load_json(DB_FILE, [])
    seen = {json.dumps([r["item"], r["key"], r["kind"], r["tier"], r["parts"]], ensure_ascii=False) for r in db}
    added = 0
    for r in records:
        sig = json.dumps([r["item"], r["key"], r["kind"], r["tier"], r["parts"]], ensure_ascii=False)
        if sig not in seen:
            db.append(r)
            seen.add(sig)
            added += 1
    save_json(DB_FILE, db)
    return added


# ---------------------------------------------------------------- OCR
_engine = None


def ocr_lines(gray, scale=2.0):
    """gray：白底黑字。回傳 [(文字, x0, y0, x1, y1)]，座標是原圖座標"""
    global _engine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    if _engine is None:
        _engine = OcrEngine.try_create_from_language(Language("zh-Hant-TW"))
    img = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    h, w = img.shape
    writer = DataWriter()
    writer.write_bytes(cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA).tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)

    async def run():
        return await _engine.recognize_async(bmp)
    out = []
    for line in asyncio.run(run()).lines:
        rs = [wd.bounding_rect for wd in line.words]
        if not rs:
            continue
        x0, y0 = min(r.x for r in rs), min(r.y for r in rs)
        x1, y1 = max(r.x + r.width for r in rs), max(r.y + r.height for r in rs)
        out.append((clean(line.text), x0 / scale, y0 / scale, x1 / scale, y1 / scale))
    return out


def clean(text):
    text = text.replace(" ", "").replace(":", "：").replace("＋", "+").replace("～", "~")
    text = text.replace("（", "(").replace("）", ")").replace(",", "，")
    # 小數點常被讀成「，·、」：夾在兩個數字中間的一律當小數點（分隔屬性的「，」後面接的是中文）
    return re.sub(r"(?<=\d)[，·、．.。](?=\d)", ".", text)


def prepare(img_bgr):
    """說明框的字有白、青、紫、紅色，取三色最大值再反轉成白底黑字"""
    return 255 - img_bgr.max(axis=2)


# ----- 屬性名稱字典：用來修正 OCR 讀錯的字
_vocab = None


def vocab():
    global _vocab
    if _vocab is None:
        from character_sheet import ELEMENTS
        from equipment import KNOWN_STATS
        words = set(KNOWN_STATS) | {"HP", "MP", "EP", "全能力", "荊棘傷害", "物理傷害", "武器攻擊力",
                                    "攻擊基礎暴擊率%", "攻擊基礎暴擊率", "迴避率", "防禦力",
                                    "最大移動速度", "跳躍力上限", "攻擊速度", "裝備防禦力", "最大生命",
                                    "每秒生命回復", "生命每秒回復", "每秒魔力回復", "魔力每秒回復", "最大魔力",
                                    "生命擊中恢復", "魔力擊中恢復", "生命擊殺恢復", "魔力擊殺恢復"}
        words |= {f"{e}屬性抗性" for e in ELEMENTS}
        try:
            tree = load_json(os.path.join(HERE, "talent_tree.json"), {})
            for n in tree.get("nodes", {}).values():
                for e in n["effects"]:
                    m = re.match(r"^(.*?)[+-]\d", e)
                    if m and len(m.group(1)) <= 12:
                        words.add(m.group(1))
        except Exception:
            pass
        for r in load_json(DB_FILE, []):
            words |= {p["name"] for p in r["parts"]}
        for it in load_json(os.path.join(HERE, "equipment.json"), {}).get("items", {}).values():
            for line in it.get("lines", []) + it.get("affixes", []):
                for seg in re.sub(KIND_RE, "", line).split("，"):
                    m = re.match(r"^(.*?)[：:]", seg)
                    if m and 2 <= len(m.group(1)) <= 12:
                        words.add(m.group(1))
        _vocab = {w for w in words if w}
    return _vocab


def fix_name(raw):
    """OCR 讀到的屬性名稱 → 最接近的已知名稱（找不到就原樣）"""
    import difflib
    raw = re.sub(r"^[^一-鿿A-Za-z]+", "", raw)
    raw = re.sub(r"[^一-鿿A-Za-z%]+$", "", raw)          # 「物理傷害」」尾巴的括號
    v = vocab()
    if raw in v:
        return raw
    tails = [w for w in v if raw.endswith(w) and len(w) >= 2 and len(raw) - len(w) <= 1]
    if tails:
        return max(tails, key=len)
    # 只往「更長」的名稱修正（OCR 常漏字），避免把「最大移動速度」改成「移動速度」
    close = [w for w in difflib.get_close_matches(raw, v, n=5, cutoff=0.6) if len(w) >= len(raw)]
    return close[0] if close else raw


JUNK_HEAD = re.compile(r"^[^一-鿿A-Za-z\[\(（［【]+")
JUNK_CJK = re.compile(r"^[一丨冖亠产乛亅]+(?=[一-鿿A-Za-z])")


def trim_junk(text):
    """去掉頭尾的雜字：「”裝備防禦力：+46%-亠，./冖「」→「裝備防禦力：+46%」"""
    text = JUNK_CJK.sub("", JUNK_HEAD.sub("", text))
    text = re.sub(r"\)[。/]+", ")%", text)                    # 範圍後面的 % 常被讀成「。」「/」
    text = re.sub(r"(?<=[\d%)])(?:[^\d%)Tt一-鿿]|[一丨冖亠产乛亅])*$", "", text)   # 數值後面的雜字
    # 「最大生命：一+30%」「最大生命：一：+30%」：名稱和數值中間的雜字
    return re.sub(r"[：:]\s*[一丨\-~]+\s*[：:]?\s*(?=\+)", "：", text)


def fix_line(text):
    """修正一行：前/後綴標記、屬性名稱、常見誤讀"""
    text = trim_junk(text)
    kind = ""
    head = text[:4]
    for ch, k in (("前", "前"), ("偂", "前"), ("後", "後")):
        if ch in head:
            kind = k
            text = text[head.index(ch) + 1:]
            text = re.sub(r"^[\]\)）］】〕亅丨1Ii\|]+", "", text)
            break
    text = re.sub(r"[。]$", "%", text)
    text = re.sub(r"：?[刊干十]\s*(?=[\d~])", "：+(", text)        # 「+(」常被讀成「刊」
    segs = []
    for seg in text.split("，"):
        m = re.match(r"^(.*?)([：:·、.,]?)(\+.*)$", seg)
        segs.append(f"{fix_name(m.group(1))}：{m.group(3)}" if m else seg)
    text = "，".join(segs)
    text = re.sub(r"[^\d)%Tt]*[Tt](\d{1,2})$", r" T\1", text) if kind else text
    return (f"[{kind}]" if kind else "") + text.replace(" T", "T")


HEADERS = ("秘藏能力", "裝備分類", "武器分類", "裝備等級")
LEVEL_RE = re.compile(r"^\W*.{0,2}等級[：:]?(\d+)")              # 「*備等級：20」也算
CAT_RE = re.compile(r"^\W*(裝.{0,3}|武.{0,3})[：·:.、，](.+)$")     # 「裝分：戒指一」「裝偏分類·手套」


def level_of(text):
    m = LEVEL_RE.match(text)
    return m.group(1) if m and "道具" not in text and "需要" not in text else None


def category_of(text):
    """回傳 (分類標題, 分類) 或 None"""
    m = CAT_RE.match(text)
    if not m or "等級" in text or "需要" in text:
        return None
    cat = re.sub(r"[一丨\-—_，。.]+$", "", m.group(2).lstrip("，。.·"))
    if re.search(r"[+\d%]", cat):
        return None
    known = next((k for k in PART_MAP if cat.startswith(k)), None)
    if m.group(1).startswith("武"):
        return "武器分類", cat
    if known or "分類" in m.group(1):
        return "裝備分類", known or cat
    return None


def is_affix_header(text):
    """「秘藏能力」常被讀成「秘能力」「藏能力」「能力」"""
    return not re.search(r"\d", text) and bool(re.fullmatch(r".{0,3}能力", text))


def score_line(text):
    t = fix_line(text)
    s = len(re.findall(r"[一-鿿]", t)) * 0.1
    if any(h in t for h in HEADERS) or is_affix_header(t):
        s += 5
    body = re.sub(KIND_RE, "", t)
    for seg in re.sub(r"[Tt]\d{1,2}$", "", body).split("，"):
        m = re.match(r"^(.*?)：", seg)
        if m and m.group(1) in vocab():
            s += 3
        mv = RANGE_RE.match(seg) or VALUE_RE.match(seg)
        if mv and re.search(r"[一-鿿A-Z]", mv.group(1)):      # 要有屬性名稱，單獨的數字是雜訊
            s += 2
    if re.search(r"[Tt]\d{1,2}$", t):
        s += 1
    if re.search(r"\d[~～]-?[\d.]+\)", t):
        s += 1
    if re.search(r"\(-?[\d.]+[~～]\)", t):           # 範圍後半沒讀到：(51~)
        s -= 2
    if re.search(r"\(\d{3,}\)", t):                 # 範圍漏了「~」：40~60 → 460
        s -= 3
    return s


def ocr_best(img_bgr):
    """用幾種前處理各讀一次，同一行挑分數最高的結果"""
    v = img_bgr.max(axis=2)
    t150 = np.where(v > 150, 0, 255).astype(np.uint8)
    t110 = np.where(v > 110, 0, 255).astype(np.uint8)
    lab_l = 255 - cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)[:, :, 0]
    green = 255 - img_bgr[:, :, 1]          # 紫色標題疊在紫色背景上時，綠色通道分得最開
    variants = [(t150, 3), (t150, 2), (t150, 4), (t110, 3), (255 - v, 3), (255 - v, 4), (lab_l, 3), (green, 3)]
    rows = []                       # [(y 中心, 高度, [候選行...])]
    for g, sc in variants:
        for l in ocr_lines(g, sc):
            yc, h = (l[2] + l[4]) / 2, l[4] - l[2]
            if h > 36:                      # 把一整排字直向串成一行的雜訊
                continue
            for r in rows:
                if abs(r[0] - yc) < max(5, min(h, r[1]) * 0.6) and \
                        min(l[3], r[2][0][3]) - max(l[1], r[2][0][1]) > -20:
                    r[2].append(l)
                    break
            else:
                rows.append((yc, h, [l]))
    out = []
    for yc, h, cands in rows:
        best = max(cands, key=lambda l: (score_line(l[0]), len(l[0])))
        # 分數最高的那行可能漏讀了 [前]/[後]：其他讀法有讀到就補回去
        if not re.match(KIND_RE, fix_line(best[0])):
            kinds = [m.group(1) for c in cands for m in [re.match(KIND_RE, fix_line(c[0]))] if m and m.group(1)]
            if kinds:
                best = (f"[{max(set(kinds), key=kinds.count)}]" + trim_junk(best[0]),) + tuple(best[1:])
        x0 = min(c[1] for c in cands); x1 = max(c[3] for c in cands)
        out.append((best[0], x0, best[2], x1, best[4]))
    return sorted(out, key=lambda l: l[2])


def locate(lines):
    """從一大塊畫面的 OCR 結果中找出裝備說明框的那幾行"""
    find = lambda *keys: next((l for l in lines if any(k in l[0] for k in keys)), None)
    hint, lv, sec = find("按住", "詳細資訊"), find("裝備等級"), find("秘藏能力")
    anchor = hint or sec or lv
    if not anchor or not (lv or sec):
        return None
    cx = (anchor[1] + anchor[3]) / 2 if anchor is not lv else anchor[1] + 20
    top = (lv or sec)[2] - 90
    bottom = hint[4] + 2 if hint else 1e9
    sel = [l for l in lines if abs((l[1] + l[3]) / 2 - cx) < 175 and top <= l[2] <= bottom]
    sel.sort(key=lambda l: l[2])
    return dict(lines=sel, cx=cx, top=top, bottom=min(bottom, max(l[4] for l in sel)), lv=lv)


def join_wrapped(lines):
    """兩個屬性的詞綴太長會換行：
    「[前]武器攻擊力：+(4~4)%，命中值：」＋「+(4~6)  T9」→ 接成一行"""
    out = []
    for t in lines:
        t = trim_junk(t)
        cont = bool(re.match(r"^[+\-(（~～]*\d", t)) and "：" not in re.split(r"\d", t, 1)[0]
        if out and (cont or re.search(r"[：，,]$", out[-1])):
            out[-1] += t
        else:
            out.append(t)
    return out


def parse(loc):
    """回傳 dict(name, cat_label, category, ilvl, base[], affix_lines[], detail)"""
    lines, lv = loc["lines"], loc["lv"]
    res = dict(name="", cat_label="", category="", ilvl="", base=[], affix_lines=[], detail=False)
    top_y = lv[2] + 2 if lv else (lines[0][4] + 30 if lines else 0)
    # 名稱：裝備等級上方、不含數字（排除旁邊道具欄的楓幣數字）、最靠近說明框中央、字最大
    titles = [l for l in lines if l[4] <= top_y and "鎖定" not in l[0] and "：" not in l[0]
              and not re.search(r"\d", l[0]) and len(re.findall(r"[一-鿿]", l[0])) >= 2]
    if titles:
        best = min(titles, key=lambda l: abs((l[1] + l[3]) / 2 - loc["cx"]) - 2 * (l[4] - l[2])
                   - 15 * len(re.findall(r"[一-鿿]", l[0])))       # 裝備名稱通常比雜字長
        res["name"] = re.sub(r"^[^一-鿿]*[一丨乛亠]?(?=[一-鿿]{2})", "", best[0])
        # 傳奇裝備的名稱「求生欲．褐色短皮靴」：中間的「．」常被讀成「、·.」
        res["name"] = re.sub(r"(?<=[一-鿿])[、，·•.。;；:：]+(?=[一-鿿])", "．", res["name"])
    in_affix = False
    raw_aff = []
    has_cat = any(category_of(l[0]) for l in lines)
    seen_cat = not has_cat
    for text, *_ in lines:
        if text == res["name"] or "鎖定" in text:
            continue
        if "秘藏能力" in text or is_affix_header(text):
            in_affix = True
            continue
        if "按住" in text or "詳細資訊" in text or "Tab" in text:
            break
        lvl = level_of(text)
        if lvl and not res["ilvl"]:
            res["ilvl"] = lvl
            continue
        if re.match(r"^(需要|裝備名聲|道具等級)", text):
            continue
        c = category_of(text)
        if c:
            res["cat_label"], res["category"] = c
            seen_cat = True
            continue
        if not seen_cat:                              # 分類之前是名稱、需求那一區
            continue
        if in_affix:
            raw_aff.append(text)                      # 先收集，最後再把換行的詞綴接回去
        elif re.search(r"[+\-]|快|慢", text):
            res["base"].append(fix_line(text))
    for text in join_wrapped(raw_aff):
        if not re.search(r"\d", text) or not re.search(r"[一-鿿A-Z]{2}", text) or re.search(r"\d\s*/\s*\d", text):
            continue                                  # 背景的雜字（聊天框的「發送」、HP/EP 條的 596/596）
        text = fix_line(text)
        if not re.search(r"[：+]", text):
            continue                                  # 特殊效果的說明文字（例：使用史詩變化卷軸時，有額外50%機率…）
        name = re.split(r"[：+(]", re.sub(KIND_RE, "", text))[0]
        if not (re.search(r"[一-鿿]", name) or name in vocab()):
            continue
        res["affix_lines"].append(text)
        res["ranges"] = res.get("ranges", 0) + bool(re.search(r"\d\s*[~～]\s*-?\d", text) or DETAIL_RE.match(text))
        res["detail"] = res["ranges"] * 2 >= len(res["affix_lines"])   # 大部分行都有範圍才算 Tab 畫面
    return res


def parse_detail_affix(text):
    m = DETAIL_ANY.match(text)
    if not m:
        return None
    kind, body, tier = m.group(1) or "", m.group(2), int(m.group(3)) if m.group(3) else None
    parts = []
    for seg in body.split("，"):
        r = RANGE_RE.match(seg)
        if r:
            parts.append(dict(name=r.group(1), pct=bool(r.group(4)), lo=float(r.group(2)), hi=float(r.group(3))))
        elif RANGE_HINT.search(seg):                  # 範圍有字沒讀到：留名稱，範圍等使用者補
            parts.append(dict(name=re.split(r"[：+(]", seg)[0], pct=seg.endswith("%"), lo=None, hi=None))
        else:
            v = VALUE_RE.match(seg)
            if v:
                parts.append(dict(name=v.group(1), pct=bool(v.group(3)), lo=float(v.group(2)), hi=float(v.group(2))))
    return dict(kind=kind, tier=tier, parts=parts)


def parse_value_affix(text):
    parts = []
    for seg in re.sub(KIND_RE, "", text).split("，"):
        v = VALUE_RE.match(seg)
        if v:
            parts.append(dict(name=v.group(1), pct=bool(v.group(3)), value=float(v.group(2))))
    return parts


def merge(normal, detail):
    """一般畫面的實際數值＋詳細資訊的前後綴/T 級/範圍 → [affix]"""
    vals = [parse_value_affix(t) for t in normal["affix_lines"]] if normal and not normal["detail"] else []
    dets = [parse_detail_affix(t) for t in detail["affix_lines"]] if detail else []    # 讀不出的保留 None，順序才不會錯位
    out = []
    same_count = len(vals) == len(dets)
    for i in range(max(len(vals), len(dets))):
        v = vals[i] if i < len(vals) else []
        d = dets[i] if i < len(dets) else None
        # 兩個畫面的名稱可能不同（最大移動速度＝移動速度上限），行數一樣就照順序配對
        if d and not d["parts"]:
            d = dict(d, parts=[dict(name=p["name"], pct=p["pct"], lo=None, hi=None) for p in v])
        if d and v and not same_count and v[0]["name"] != d["parts"][0]["name"]:
            v = next((x for x in vals if x and x[0]["name"] == d["parts"][0]["name"]), v)
        parts = d["parts"] if d else [dict(name=p["name"], pct=p["pct"], lo=None, hi=None) for p in v]
        for j, p in enumerate(parts):
            p["value"] = v[j]["value"] if j < len(v) else None
            if p["value"] is None and p.get("lo") is not None and p["lo"] == p["hi"] and not vals:
                p["value"] = p["lo"]                  # 只有詳細資訊有數值（傳奇的特殊能力）
            p["pct"] = p["pct"] or (j < len(v) and v[j]["pct"])
        out.append(dict(kind=d["kind"] if d else "", tier=d["tier"] if d else None, parts=parts))
    return out


def affix_to_line(a):
    """確認視窗裡的一行：每個數值後面接自己的範圍
    [前] 武器攻擊力：+4% (4~4%)，命中值：+5 (4~6)  T9"""
    segs = []
    for p in a["parts"]:
        pct = "%" if p["pct"] else ""
        seg = f"{p['name']}：" + (f"+{p['value']:g}{pct}" if p.get("value") is not None else "?")
        if p.get("lo") is not None:
            seg += f" ({p['lo']:g}~{p['hi']:g}{pct})"
        segs.append(seg)
    return (f"[{a['kind']}] " if a["kind"] else "") + "，".join(segs) + (f"  T{a['tier']}" if a["tier"] else "")


LINE_RE = re.compile(r"^\s*(?:[\[\(（［]?(前|後)[\]\)）］]\s*)?(.*?)\s*(?:[Tt](\d{1,2}))?\s*$")
SEG_RE = re.compile(r"^(.*?)：\s*(?:\+?(-?[\d.]+)(%?)|\?)\s*(?:\(\s*\+?(-?[\d.]+)\s*(?:~\s*(-?[\d.]+))?\s*(%?)\s*\))?$")


def line_to_affix(line):
    m = LINE_RE.match(line)
    if not m or not m.group(2):
        return None
    kind, body, tier = m.group(1) or "", m.group(2), m.group(3)
    parts = []
    for seg in body.split("，"):
        seg = seg.strip()
        if not seg:
            continue
        r = SEG_RE.match(clean(seg).replace("(", " (").replace("：", "：", 1))
        if r:
            p = dict(name=r.group(1), pct=bool(r.group(3) or r.group(6)),
                     value=float(r.group(2)) if r.group(2) else None,
                     lo=float(r.group(4)) if r.group(4) else None,
                     hi=float(r.group(5) or r.group(4)) if r.group(4) else None)
        else:
            p = dict(name=clean(seg).split("：")[0], pct=False, value=None, lo=None, hi=None)
        parts.append(p)
    return dict(kind=kind, tier=int(tier) if tier else None, parts=parts) if parts else None


# ---------------------------------------------------------------- 擷取
def monitor_at(x, y):
    with mss.MSS() as s:
        for m in s.monitors[1:]:
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                return m
        return s.monitors[1]


def grab_around(x, y):
    m = monitor_at(x, y)
    x0, x1 = max(m["left"], x - 470), min(m["left"] + m["width"], x + 470)
    y0, y1 = max(m["top"], y - 330), min(m["top"] + m["height"], y + 830)
    with mss.MSS() as s:
        img = np.array(s.grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}))[:, :, :3].copy()
    return img, (x0, y0)


def find_box(img, cursor):
    """說明框是半透明的深藍色：找滑鼠附近最大的一塊偏藍區域"""
    b, g, r = [c.astype(np.int16) for c in cv2.split(img)]
    mask = ((b - r > 18) & (b - g > 8)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    solid = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((25, 25), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(solid)
    cx, cy = cursor
    best = None
    for x, y, w, h, a in st[1:]:
        if not (180 <= w and h >= 150):
            continue
        # 左右要靠近滑鼠；上下放寬：上半部被切掉時，找到的藍色區塊會在滑鼠下方很遠
        near_x = max(x - cx, 0, cx - (x + w)) < 150
        near_y = y - cy < 520 and cy - (y + h) < 150
        if near_x and near_y and (best is None or a > best[4]):
            best = (x, y, w, h, a)
    if not best:
        return None
    x, y, w, h, _ = best

    def longest_run(flags):
        runs, start = [], None
        for i, f in enumerate(list(flags) + [False]):
            if f and start is None:
                start = i
            elif not f and start is not None:
                runs.append((start, i))
                start = None
        return max(runs, key=lambda r: r[1] - r[0]) if runs else None
    # 旁邊的視窗如果也偏藍會黏在一起：說明框是完整的長方形，整欄/整列都是藍的
    sub = mask[y:y + h, x:x + w]
    cols = longest_run(sub.mean(axis=0) > 0.55)
    if not cols:
        return None
    sub = sub[:, cols[0]:cols[1]]
    rows = longest_run(sub.mean(axis=1) > 0.45)
    if not rows:
        return None
    bx, by, bw, bh = x + cols[0], y + rows[0], cols[1] - cols[0], rows[1] - rows[0]
    if 0 < by - y < 80:              # 標題那幾列有大圖示、覆蓋率低，用整塊的上緣
        bh += by - y
        by = y
    return (bx, by, bw, bh) if 180 <= bw <= 480 and bh >= 120 else None


def extend_top(img, box, limit=320):
    """說明框是半透明的，會讓背後變暗：沿著左右兩邊往上，框內比框外暗的地方都還是說明框"""
    x, y, w, h = box
    L = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    W = L.shape[1]

    def inside(r):
        ok = 0
        for a0, a1, b0, b1 in ((x + 4, x + 12, x - 12, x - 4), (x + w - 12, x + w - 4, x + w + 4, x + w + 12)):
            if b0 < 0 or b1 > W:
                ok += 1                    # 貼著畫面邊緣，無法比較，當作符合
                continue
            i, o = L[r, a0:a1].mean(), L[r, b0:b1].mean()
            ok += i < o * 0.85 or (o < 45 and i < 45)
        return ok == 2
    top, miss = y, 0
    for r in range(y - 1, max(-1, y - limit), -1):
        if inside(r):
            top, miss = r, 0
        else:
            miss += 1
            if miss > 6:
                break
    return (x, top, w, h + (y - top))


def diff_box(img, base, cursor, near=None):
    """用「有說明框」和「沒有說明框」兩張圖比對，找出完整的說明框位置
    （說明框是半透明的，下半部疊在彩色圖示上時，只找藍色會切掉一半）
    near：用藍色找到的說明框（左右位置準，上下可能被切掉）"""
    d = (np.abs(img.astype(np.int16) - base.astype(np.int16)).max(axis=2) > 14).astype(np.uint8)
    cx, cy = cursor
    d[max(0, cy - 45):cy + 50, max(0, cx - 45):cx + 45] = 0      # 滑鼠游標、被指到的格子
    if near is not None:
        # 只看藍色框那幾欄：一整列大部分都有變化的地方就是說明框（旁邊畫面在動也不影響）
        x, y, w, h = near
        band = d[:, x + 8:x + w - 8]
        rows = band.mean(axis=1) > 0.6
        top, bottom = y, y + h
        miss = 0
        for r in range(y + h, len(rows)):           # 往下
            if rows[r]:
                bottom, miss = r + 1, 0
            else:
                miss += 1
                if miss > 12:
                    break
        miss = 0
        for r in range(y - 1, -1, -1):               # 往上
            if rows[r]:
                top, miss = r, 0
            else:
                miss += 1
                if miss > 12:
                    break
        return (int(x), int(top), int(w), int(bottom - top))
    d = cv2.morphologyEx(d, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    d = cv2.morphologyEx(d, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(d)
    best = None
    for x, y, w, h, area in st[1:]:
        if w < 180 or h < 120 or w > 520 or max(x - cx, 0, cx - (x + w)) > 160:
            continue
        if best is None or area > best[4]:
            best = (x, y, w, h, area)
    if not best:
        return None
    # 說明框是完整的長方形：在這一塊裡找「整欄都有變化」的最長一段當左右，再找「整列都有變化」的當上下
    x, y, w, h, _ = best
    sub = d[y:y + h, x:x + w]

    def longest(flags):
        runs, st = [], None
        for i, f in enumerate(list(flags) + [False]):
            if f and st is None:
                st = i
            elif not f and st is not None:
                runs.append((st, i))
                st = None
        return max(runs, key=lambda r: r[1] - r[0]) if runs else None
    cols = longest(sub.mean(axis=0) > 0.6)
    if not cols or cols[1] - cols[0] < 180:
        return (int(x), int(y), int(w), int(h))
    rows = longest(sub[:, cols[0]:cols[1]].mean(axis=1) > 0.6)
    if not rows:
        return (int(x), int(y), int(w), int(h))
    return (int(x + cols[0]), int(y + rows[0]), int(cols[1] - cols[0]), int(rows[1] - rows[0]))


def clean_tooltip(img, base, box):
    """扣掉半透明說明框後面的背景，只留文字（OCR 用）：框內 ≈ k·背景 + m，文字比這個亮很多"""
    x, y, w, h = box
    I, B = img[y:y + h, x:x + w].astype(np.float32), base[y:y + h, x:x + w].astype(np.float32)
    res = np.zeros(I.shape[:2], np.float32)
    for c in range(3):
        i, b = I[:, :, c].ravel(), B[:, :, c].ravel()
        k, m = np.polyfit(b, i, 1)
        for _ in range(3):
            r = i - (k * b + m)
            keep = np.abs(r) < 2 * np.median(np.abs(r)) + 3
            k, m = np.polyfit(b[keep], i[keep], 1)
        res = np.maximum(res, I[:, :, c] - k * B[:, :, c] - m)
    col = np.empty_like(I)
    col[:] = (90, 50, 40)
    mask = res > 40
    col[mask] = I[mask]
    out = img.copy()
    out[y:y + h, x:x + w] = col.astype(np.uint8)
    return out


def read_tooltip(img, cursor=None, force_box=None):
    """找出說明框位置，只對說明框做仔細的多重 OCR。回傳 (解析結果, 截圖, 說明框位置)"""
    box = force_box or (find_box(img, cursor) if cursor else None)
    if box:
        if not force_box:
            box = extend_top(img, box)
        x, y, w, h = box
        crop = img[y:y + h, x:x + w]
        lines = ocr_best(crop)
        lv = next((l for l in lines if level_of(l[0])), None)
        if not lv and y > 0:
            # 還是沒看到「裝備等級」：往上多截一段再讀
            y2 = max(0, y - 300)
            crop2 = img[y2:y + h, x:x + w]
            lines2 = ocr_best(crop2)
            lv2 = next((l for l in lines2 if level_of(l[0])), None)
            if lv2:
                top = max(0, int(lv2[2]) - 60)
                crop = crop2[top:]
                lines = [(t, a, b - top, c, d - top) for t, a, b, c, d in lines2 if b >= top]
                lv = next((l for l in lines if level_of(l[0])), None)
                box = (x, y2 + top, w, h + y - y2 - top)
        if not lines or not (lv or any("秘藏" in l[0] or "分類" in l[0] for l in lines)):
            return None, None, None
        return parse(dict(lines=lines, cx=w / 2, lv=lv)), crop, box
    loc = locate(ocr_lines(prepare(img)))
    if not loc:
        return None, None, None
    h, w = img.shape[:2]
    x0, x1 = int(max(0, loc["cx"] - 165)), int(min(w, loc["cx"] + 165))
    y0, y1 = int(max(0, loc["top"])), int(min(h, loc["bottom"] + 12))
    crop = img[y0:y1, x0:x1]
    fine = locate(ocr_best(crop))
    return parse(fine or loc), crop, (x0, y0, x1 - x0, y1 - y0)


class Capture:
    """擷取快捷鍵、擷取流程、確認視窗"""

    def __init__(self, app):
        self.app = app
        self.settings = dict(DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {}))
        self.q = queue.Queue()
        self.busy = False
        self.hk = None
        self.win = None
        self.status_text = "尚未擷取"
        self.register()
        app.root.after(150, self._poll)
        app.root.after(200, self.update_button)

    def update_button(self):
        btn = getattr(self.app, "capture_btn", None)
        if btn:
            btn.configure(text=f"裝備擷取設定（{self.settings['hotkey'].upper()}）")

    # ----- 快捷鍵
    def register(self):
        if self.hk is not None:
            try:
                keyboard.remove_hotkey(self.hk)
            except (KeyError, ValueError):
                pass
            self.hk = None
        try:
            self.hk = keyboard.add_hotkey(self.settings["hotkey"], self._trigger)
            return True
        except Exception as e:           # 快捷鍵寫法不對
            self.status(f"快捷鍵無法使用：{e}")
            return False

    def _trigger(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            res = self.capture()
        except Exception as e:
            res = dict(error=f"擷取失敗：{e}", cursor=win32api.GetCursorPos())
        self.q.put(res)

    def _poll(self):
        try:
            while True:
                res = self.q.get_nowait()
                self.busy = False
                try:
                    if "hotkey" in res:
                        self._hotkey_read(res["hotkey"])
                    elif "error" in res:
                        self.status(res["error"])
                        self.toast(res["error"], res.get("cursor"))
                    else:
                        self.toast("讀取完成，請確認結果", res.get("cursor"), ok=True)
                        self.confirm(res)
                except Exception as e:
                    import traceback
                    with open(os.path.join(HERE, "capture_error.log"), "a", encoding="utf-8") as fp:
                        fp.write(traceback.format_exc() + "\n")
                    self.toast(f"顯示結果時出錯：{e}", res.get("cursor"))
        except queue.Empty:
            pass
        self.app.root.after(150, self._poll)

    # ----- 視窗放在遊戲那個螢幕上（主程式縮到最小時，主視窗的位置是 -32000）
    def place(self, win, cursor, dx=40, dy=-60):
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x, y = cursor or win32api.GetCursorPos()
        m = monitor_at(x, y)
        x = min(max(m["left"], x + dx), m["left"] + m["width"] - w)
        y = min(max(m["top"], y + dy), m["top"] + m["height"] - h - 40)
        win.geometry(f"+{int(x)}+{int(y)}")
        win.deiconify()
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
        win.after(200, win.lift)

    def toast(self, text, cursor=None, ok=False):
        """在滑鼠旁邊跳一個小提示，2.5 秒後自動消失"""
        t = tk.Toplevel(self.app.root)
        t.overrideredirect(True)
        tk.Label(t, text=text, bg="#1c7a2c" if ok else "#a33", fg="white", font=(FONT, 10, "bold"),
                 padx=10, pady=6).pack()
        self.place(t, cursor, 20, 20)
        t.after(2500, t.destroy)
        if not ok:
            self.app.root.bell()

    def capture(self):
        keys = [k.strip() for k in self.settings["hotkey"].split("+")]
        for _ in range(40):                                    # 等快捷鍵放開
            if not any(keyboard.is_pressed(k) for k in keys if k):
                break
            time.sleep(0.03)
        cx, cy = win32api.GetCursorPos()
        img1, org = grab_around(cx, cy)
        cur = (cx - org[0], cy - org[1])
        normal, crop1, box1 = read_tooltip(img1, cur)
        if not normal:
            return dict(error="沒有找到裝備說明：請先把滑鼠停在裝備上，等說明框出現再按快捷鍵",
                        cursor=(cx, cy))
        detail, crop2 = None, None
        if self.settings["auto_tab"]:
            # 找到說明框就一定按 Tab，兩張都截；之後看哪張範圍（數字~數字）比較多，那張才是詳細資訊
            win32api.keybd_event(0x09, 0, 0, 0)
            try:
                time.sleep(0.5)
                img2, _ = grab_around(cx, cy)
            finally:
                win32api.keybd_event(0x09, 0, 2, 0)
            detail, crop2, box2 = read_tooltip(img2, cur)
            # 兩張的說明框在同一個位置：上緣取比較高的那張，另一張用同樣的範圍重讀
            if detail and box2 and box1 and abs(box2[1] - box1[1]) > 8:
                big = (box1[0], min(box1[1], box2[1]), box1[2],
                       max(box1[1] + box1[3], box2[1] + box2[3]) - min(box1[1], box2[1]))
                if box1[1] > box2[1]:
                    normal, crop1, _ = read_tooltip(img1, cur, big)
                else:
                    detail, crop2, _ = read_tooltip(img2, cur, big)
            if detail and normal.get("ranges", 0) > detail.get("ranges", 0):   # 使用者自己按著 Tab
                normal, detail, crop1, crop2 = detail, normal, crop2, crop1
            if detail and not detail.get("ranges") and detail["affix_lines"] == normal["affix_lines"]:
                detail, crop2 = None, None                     # Tab 沒有作用：兩張一樣
            normal["detail"] = False
        elif normal["detail"]:                                 # 沒開自動 Tab、使用者自己按著 Tab
            detail, normal, crop2, crop1 = normal, None, crop1, None
        os.makedirs(CAP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        imgs = []
        cv2.imencode(".jpg", img1, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tofile(
            os.path.join(CAP_DIR, f"{stamp}_full.jpg"))          # 完整畫面，出問題時除錯用
        for tag, c in (("normal", crop1), ("detail", crop2)):
            if c is not None and c.size:
                path = os.path.join(CAP_DIR, f"{stamp}_{tag}.png")
                cv2.imencode(".png", c)[1].tofile(path)
                imgs.append(path)
        return dict(normal=normal, detail=detail, imgs=imgs, time=stamp, cursor=(cx, cy))

    def status(self, text):
        self.status_text = text
        if self.win and self.win.winfo_exists():
            self.status_var.set(text)

    # ----- 設定視窗
    def open(self):
        if self.win and self.win.winfo_exists():
            self.win.lift()
            return
        w = self.win = tk.Toplevel(self.app.root, padx=14, pady=10)
        w.title("裝備擷取設定")
        w.transient(self.app.root)
        w.resizable(False, False)
        tk.Label(w, text="在遊戲裡把滑鼠停在裝備上，等說明框出現後按擷取快捷鍵。\n"
                         "會自動按住 Tab 讀取前綴/後綴與 T 級，讀完跳出確認視窗。",
                 font=(FONT, 10), justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(w, text="擷取快捷鍵", font=(FONT, 10, "bold")).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.hk_var = tk.StringVar(value=self.settings["hotkey"].upper())
        tk.Label(w, textvariable=self.hk_var, font=(FONT, 12, "bold"), fg="#2a5db0", width=16,
                 relief="groove").grid(row=1, column=1, pady=(10, 0), padx=6)
        self.hk_btn = tk.Button(w, text="設定快捷鍵", font=(FONT, 10), command=self._read_hotkey)
        self.hk_btn.grid(row=1, column=2, pady=(10, 0))
        self.tab_var = tk.BooleanVar(value=self.settings["auto_tab"])
        tk.Checkbutton(w, text="自動按住 Tab 讀取詳細資訊（前綴/後綴、T 級、範圍）", variable=self.tab_var,
                       font=(FONT, 10), command=self._save_settings).grid(row=2, column=0, columnspan=3,
                                                                          sticky="w", pady=(8, 0))
        row = tk.Frame(w)
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tk.Button(row, text="開啟詞綴表", font=(FONT, 10), command=lambda: AffixTable(self.app)).pack(side="left")
        tk.Button(row, text="開啟擷取圖片資料夾", font=(FONT, 10),
                  command=lambda: (os.makedirs(CAP_DIR, exist_ok=True), os.startfile(CAP_DIR))).pack(side="left", padx=6)
        self.status_var = tk.StringVar(value=self.status_text)
        tk.Label(w, textvariable=self.status_var, font=(FONT, 9), fg="#555", wraplength=460,
                 justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _save_settings(self):
        self.settings["auto_tab"] = self.tab_var.get()
        save_json(SETTINGS_FILE, self.settings)

    def _read_hotkey(self):
        self.hk_var.set("請按下新的按鍵…")
        self.hk_btn.configure(state="disabled")

        def worker():
            try:
                hk = keyboard.read_hotkey(suppress=False)
            except Exception:
                hk = ""
            self.q.put(dict(hotkey=hk))
        threading.Thread(target=worker, daemon=True).start()

    def _hotkey_read(self, hk):
        self.hk_btn.configure(state="normal")
        if not hk or hk == "esc":
            self.hk_var.set(self.settings["hotkey"].upper())
            return
        keys = hk.split("+")
        risky = [k for k in keys if k in GAME_KEYS] or (keys if len(keys) == 1 and len(keys[0]) == 1 else [])
        if risky and not messagebox.askyesno(
                "快捷鍵", f"「{hk.upper()}」包含遊戲可能會用到的按鍵（{'、'.join(risky)}），"
                         f"按下去時遊戲也會收到。確定要用這個嗎？", parent=self.win):
            self.hk_var.set(self.settings["hotkey"].upper())
            return
        old = self.settings["hotkey"]
        self.settings["hotkey"] = hk
        if self.register():
            save_json(SETTINGS_FILE, self.settings)
            self.status(f"擷取快捷鍵改成 {hk.upper()}")
            self.update_button()
        else:
            self.settings["hotkey"] = old
            self.register()
        self.hk_var.set(self.settings["hotkey"].upper())

    # ----- 確認視窗
    def confirm(self, res):
        from equipment import PART_TYPES
        normal, detail = res["normal"], res["detail"]
        info = dict(normal or detail)
        other = detail if normal else None
        # 兩張截圖互補：說明框上半部有時被聊天框蓋住，名稱、等級改用另一張讀到的
        for k in ("name", "ilvl", "category", "cat_label"):
            if not info.get(k) and other and other.get(k):
                info[k] = other[k]
        affixes = merge(normal, detail)
        base = (normal or detail)["base"]
        cat = info["category"]
        part = "武器" if info["cat_label"] == "武器分類" else PART_MAP.get(cat, "")

        d = tk.Toplevel(self.app.root, padx=10, pady=8)
        d.title("確認擷取結果")
        d.attributes("-topmost", True)
        imgs = tk.Frame(d)
        imgs.grid(row=0, column=0, rowspan=20, sticky="n", padx=(0, 10))
        d._photos = []
        for path in res["imgs"]:
            im = Image.open(path)
            k = min(1.0, 520 / im.height)
            ph = ImageTk.PhotoImage(im.resize((int(im.width * k), int(im.height * k))))
            d._photos.append(ph)
            tk.Label(imgs, image=ph).pack(side="left", padx=2)

        L = dict(font=(FONT, 10))
        tk.Label(d, text="OCR 可能讀錯，請對照左邊的截圖修正", fg="#b05000", **L).grid(row=0, column=1, columnspan=2, sticky="w")
        tk.Label(d, text="名稱", **L).grid(row=1, column=1, sticky="e")
        name = tk.StringVar(value=info["name"])
        tk.Entry(d, textvariable=name, width=26, **L).grid(row=1, column=2, sticky="w")
        tk.Label(d, text="部位", **L).grid(row=2, column=1, sticky="e")
        part_var = tk.StringVar(value=part if part in PART_TYPES else "")
        ttk.Combobox(d, textvariable=part_var, values=PART_TYPES, state="readonly", width=12).grid(row=2, column=2, sticky="w")
        tk.Label(d, text="分類", **L).grid(row=3, column=1, sticky="e")
        cat_var = tk.StringVar(value=cat)
        tk.Entry(d, textvariable=cat_var, width=14, **L).grid(row=3, column=2, sticky="w")
        tk.Label(d, text="裝備等級", **L).grid(row=4, column=1, sticky="e")
        lv_var = tk.StringVar(value=info["ilvl"])
        tk.Entry(d, textvariable=lv_var, width=8, **L).grid(row=4, column=2, sticky="w")
        tk.Label(d, text="基本屬性（一般畫面，一行一個）", **L).grid(row=5, column=1, columnspan=2, sticky="w", pady=(6, 0))
        base_txt = tk.Text(d, width=44, height=6, **L)
        base_txt.grid(row=6, column=1, columnspan=2)
        base_txt.insert("1.0", "\n".join(base))
        tk.Label(d, text="秘藏能力：[前/後] 名稱：實際數值 (T 級範圍)，下一個屬性…  T級", fg="#7a3fb0",
                 font=(FONT, 10, "bold")).grid(row=7, column=1, columnspan=2, sticky="w", pady=(6, 0))
        aff_txt = tk.Text(d, width=44, height=8, **L)
        aff_txt.grid(row=8, column=1, columnspan=2)
        aff_txt.insert("1.0", "\n".join(affix_to_line(a) for a in affixes))
        note = []
        if not detail:
            note.append("沒有讀到 Tab 詳細資訊，T 級和範圍要自己補（或重新擷取）")
        if not normal:
            note.append("只讀到 Tab 詳細資訊，沒有實際數值（擷取時不用自己按 Tab）")
        tk.Label(d, text="\n".join(note) or "例：[前] 荊棘傷害：+50% (46~54%)  T6　／　[前] 武器攻擊力：+4% (4~4%)，命中值：+5 (4~6)  T9\n"
                                     "傳奇裝備不寫 [前/後] 和 T 級：裝備防禦力：+46% (40~60%)",
                 fg="#b05000" if note else "#777", font=(FONT, 9), justify="left").grid(
            row=9, column=1, columnspan=2, sticky="w")
        add_bag = tk.BooleanVar(value=bool(normal))
        add_db = tk.BooleanVar(value=True)
        # 傳奇裝備：詳細資訊裡的詞綴都沒有前後綴、也沒有 T 級（這件裝備固定的能力，只有數值範圍）
        legend = tk.BooleanVar(value=bool(detail and affixes and all(not a["kind"] and not a["tier"] for a in affixes)))
        tk.Checkbutton(d, text="加入背包（角色狀態）", variable=add_bag, **L).grid(row=10, column=1, columnspan=2, sticky="w")
        tk.Checkbutton(d, text="記錄到詞綴表", variable=add_db, **L).grid(row=11, column=1, columnspan=2, sticky="w")
        tk.Checkbutton(d, text="傳奇裝備（固定能力，沒有前後綴和 T 級；詞綴表裡依裝備名稱分開記錄）",
                       variable=legend, fg="#b06000", **L).grid(row=12, column=1, columnspan=2, sticky="w")

        def save():
            affs = [a for a in (line_to_affix(x) for x in aff_txt.get("1.0", "end").splitlines() if x.strip()) if a]
            base_lines = [x.strip() for x in base_txt.get("1.0", "end").splitlines() if x.strip()]
            msg = []
            if add_db.get() and not name.get().strip():
                messagebox.showinfo("確認擷取結果", "沒有裝備名稱，不能記錄到詞綴表：請先填上名稱", parent=d)
                return
            if add_db.get():
                recs = [dict(key=affix_key(a["parts"]), kind=a["kind"], tier=a["tier"] or 0, parts=a["parts"],
                             item=name.get().strip(), part=part_var.get(), category=cat_var.get(),
                             ilvl=lv_var.get(), time=res["time"], imgs=res["imgs"],
                             **({"rarity": "傳奇"} if legend.get() else {}))
                        for a in affs if any(p.get("lo") is not None for p in a["parts"])]
                n = add_records(recs)
                msg.append(f"詞綴表新增 {n} 筆" + (f"（{len(recs) - n} 筆重複略過）" if len(recs) > n else ""))
            if add_bag.get():
                if not part_var.get():
                    messagebox.showinfo("確認擷取結果", "請選擇部位", parent=d)
                    return
                eq = self.app.equipment
                lines = [f"[{a['kind']}]" * bool(a["kind"]) +
                         "，".join(f"{p['name']}：+{p['value']:g}{'%' if p['pct'] else ''}"
                                  for p in a["parts"] if p.get("value") is not None) +
                         (f" T{a['tier']}" if a["tier"] else "") for a in affs]
                iid = eq._add(dict(name=name.get().strip() or "未命名", part=part_var.get(),
                                   category=cat_var.get() if part_var.get() in ("武器", "副手") else "",
                                   lines=base_lines, affixes=[x for x in lines if "：" in x]))
                eq.selected = iid
                eq.changed()
                msg.append("已加入背包")
            self.status(f"「{name.get()}」：" + "，".join(msg))
            d.destroy()

        row = tk.Frame(d)
        row.grid(row=13, column=1, columnspan=2, pady=(8, 0))
        tk.Button(row, text="儲存", width=10, command=save, **L).pack(side="left", padx=4)
        tk.Button(row, text="放棄", width=10, command=d.destroy, **L).pack(side="left", padx=4)
        self.place(d, res.get("cursor"), 60, -200)


# ---------------------------------------------------------------- 詞綴表
# 楓之谷介面的配色：淺藍外框、藍色標題列、深藍段落條、橘色按鈕、米白內頁
THEMES = {
    "light": dict(frame="#c8d6ea", title="#4d71a8", title2="#3b5b8c", bar="#5a6f93", paper="#fbfaf4",
                  btn="#f39c34", btn_hi="#ffb452", btn_bd="#b8661a", text="#2c3346", sel="#ffd98a",
                  seltext="#402000", navbg="#eef3fa", panel="white", border="#7a9cc6", headtext="#d6e4ff",
                  subtext="#44506a", entry="white", empty="#a8b0bd", big="#3b5b8c", mid="#8a4b12", grid="#c9d2df",
                  legend="#a0520a"),
    # 黑夜：像遊戲裡深藍色的裝備說明框
    "dark": dict(frame="#161c28", title="#2b3f63", title2="#1a2a45", bar="#34496e", paper="#1f2533",
                 btn="#d9801d", btn_hi="#f09a36", btn_bd="#8a4a0c", text="#e3e8f2", sel="#7a5a14",
                 seltext="#ffffff", navbg="#1b2230", panel="#1f2533", border="#3d5277", headtext="#aebfe2",
                 subtext="#9aa8c2", entry="#2a3242", empty="#56607a", big="#8fb3ff", mid="#f0a55a", grid="#38445c",
                 legend="#ffb36b"),
}
# 每種詞綴的（列底色, 分隔列顏色）：前綴藍、後綴綠、傳奇橘，和遊戲說明框的顏色一樣
KIND_COLORS = {
    "light": {"前": ("#eef5ff", "#4a6fa5"), "後": ("#f0f9ec", "#4f8a5b"), "": ("#f5f5f5", "#6b6b6b"),
              "傳": ("#fff3df", "#c26a1c")},
    "dark": {"前": ("#1f2c44", "#3b5f99"), "後": ("#1e3326", "#3f7a4c"), "": ("#262a33", "#555b66"),
             "傳": ("#3a2a17", "#b35f15")},
}
UI_FILE = os.path.join(HERE, "ui_settings.json")
MAPLE = dict(THEMES["light"])
KIND_COLOR = dict(KIND_COLORS["light"])


def set_theme(name):
    MAPLE.clear(); MAPLE.update(THEMES[name])
    KIND_COLOR.clear(); KIND_COLOR.update(KIND_COLORS[name])


def maple_style(root):
    """只建立一次；用自己的樣式名稱，不影響程式裡其他 ttk 元件"""
    st = ttk.Style(root)
    # Windows 預設主題不能改表頭顏色：表頭的元件改用 default 主題的（已經建立過會丟錯，略過）
    for el in ("cell", "border", "padding", "image", "text"):
        try:
            st.element_create(f"Maple.Treeheading.{el}", "from", "default")
        except tk.TclError:
            pass
    # 表格空白處的底色：Windows 主題的 field 元件不吃 fieldbackground，改用 default 主題的
    try:
        st.element_create("Maple.field", "from", "default")
    except tk.TclError:
        pass
    for name in ("Maple.Treeview", "MapleNav.Treeview"):
        st.layout(name, [("Maple.field", {"sticky": "nswe", "border": "1", "children": [
            ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
    st.layout("Maple.Treeview.Heading", [
        ("Maple.Treeheading.cell", {"sticky": "nswe"}),
        ("Maple.Treeheading.border", {"sticky": "nswe", "children": [
            ("Maple.Treeheading.padding", {"sticky": "nswe", "children": [
                ("Maple.Treeheading.image", {"side": "right", "sticky": ""}),
                ("Maple.Treeheading.text", {"sticky": "we"})]})]})])
    st.configure("Maple.Treeview", background=MAPLE["paper"], fieldbackground=MAPLE["paper"],
                 foreground=MAPLE["text"], rowheight=23, font=(FONT, 9), borderwidth=0)
    st.configure("Maple.Treeview.Heading", background=MAPLE["bar"], foreground="white",
                 font=(FONT, 9, "bold"), relief="flat", padding=(4, 4))
    st.map("Maple.Treeview.Heading", background=[("active", MAPLE["title"])])
    st.map("Maple.Treeview", background=[("selected", MAPLE["sel"])], foreground=[("selected", MAPLE["seltext"])])
    st.configure("MapleNav.Treeview", background=MAPLE["navbg"], fieldbackground=MAPLE["navbg"],
                 foreground=MAPLE["text"], rowheight=22, font=(FONT, 9), borderwidth=0)
    st.map("MapleNav.Treeview", background=[("selected", MAPLE["sel"])], foreground=[("selected", MAPLE["seltext"])])
    return st


def maple_button(parent, text, command):
    return tk.Button(parent, text=text, command=command, bg=MAPLE["btn"], fg="white", activebackground=MAPLE["btn_hi"],
                     activeforeground="white", font=(FONT, 10, "bold"), relief="flat", bd=0, padx=12, pady=3,
                     cursor="hand2", highlightthickness=1, highlightbackground=MAPLE["btn_bd"])


class AffixTable:
    def __init__(self, app):
        self.app = app
        self.db = load_json(DB_FILE, [])
        w = self.win = tk.Toplevel(app.root)
        w.title("詞綴表")
        w.geometry("1180x560")
        self.theme = load_json(UI_FILE, {}).get("affix_theme", "light")
        self._build()

    def toggle_theme(self):
        """日光 ⇄ 黑夜：換配色後把視窗內容重畫（篩選條件、選到的分類都保留）"""
        keep = dict(kind=self.kind.get(), q=self.q.get(), sel=self.nav.selection())
        self.theme = "dark" if self.theme == "light" else "light"
        ui = load_json(UI_FILE, {})
        ui["affix_theme"] = self.theme
        save_json(UI_FILE, ui)
        for c in self.win.winfo_children():
            if not isinstance(c, tk.Toplevel):
                c.destroy()
        self._build(keep)

    def _build(self, keep=None):
        set_theme(self.theme)
        w = self.win
        w.configure(bg=MAPLE["frame"])
        maple_style(w)
        # 標題列（像遊戲視窗上方的藍色標題）
        head = tk.Frame(w, bg=MAPLE["title"], highlightthickness=1, highlightbackground=MAPLE["title2"])
        head.pack(fill="x", padx=6, pady=(6, 0))
        tk.Label(head, text="詞綴表", bg=MAPLE["title"], fg="white", font=(FONT, 13, "bold"),
                 padx=10, pady=3).pack(side="left")
        tk.Label(head, text="秘藏能力 T 級紀錄", bg=MAPLE["title"], fg=MAPLE["headtext"], font=(FONT, 9)).pack(side="left")
        maple_button(head, "匯出 CSV（Excel）", self.export).pack(side="right", padx=4, pady=3)
        maple_button(head, "修改裝備名稱", self.rename).pack(side="right", padx=4, pady=3)
        tk.Button(head, text="☀ 日光模式" if self.theme == "dark" else "☾ 黑夜模式", command=self.toggle_theme,
                  bg=MAPLE["title2"], fg="white", activebackground=MAPLE["bar"], activeforeground="white",
                  font=(FONT, 10, "bold"), relief="flat", bd=0, padx=12, pady=3, cursor="hand2").pack(
            side="right", padx=(4, 10), pady=3)
        top = tk.Frame(w, bg=MAPLE["frame"], padx=8, pady=6)
        top.pack(fill="x")
        L = dict(font=(FONT, 10, "bold"), bg=MAPLE["frame"], fg=MAPLE["text"])
        tk.Label(top, text="類型", **L).pack(side="left", padx=(4, 0))
        self.kind = tk.StringVar(value="全部")
        kb = ttk.Combobox(top, textvariable=self.kind, values=["全部", "前綴", "後綴", "特殊", "傳奇"], state="readonly", width=6)
        kb.pack(side="left", padx=4)
        tk.Label(top, text="搜尋", **L).pack(side="left", padx=(10, 0))
        self.q = tk.StringVar()
        tk.Entry(top, textvariable=self.q, width=14, font=(FONT, 10), relief="solid", bd=1, bg=MAPLE["entry"],
                 fg=MAPLE["text"], insertbackground=MAPLE["text"]).pack(side="left", padx=4)
        for v in (self.kind, self.q):
            v.trace_add("write", lambda *_: self.fill())
        tk.Label(w, text="每格是該 T 級看過的數值範圍（T10 最差、T1 最好）。範圍來自遊戲 Tab 詳細資訊，"
                         "同一格有不同範圍時用「/」分開（可能和裝備等級或部位有關）。雙擊一列看所有紀錄。",
                 font=(FONT, 9), fg=MAPLE["subtext"], bg=MAPLE["frame"], anchor="w").pack(fill="x", padx=10)
        body = tk.PanedWindow(w, orient="horizontal", sashwidth=6, bg=MAPLE["frame"], bd=0)
        body.pack(fill="both", expand=True, padx=8, pady=6)
        # 左：分類樹（括號裡是紀錄筆數）
        nav = tk.Frame(body, bg=MAPLE["panel"], highlightthickness=1, highlightbackground=MAPLE["border"])
        tk.Label(nav, text="裝備分類", bg=MAPLE["bar"], fg="white", font=(FONT, 10, "bold"), pady=3).pack(fill="x")
        self.nav = ttk.Treeview(nav, show="tree", selectmode="browse", style="MapleNav.Treeview")
        self.nav.column("#0", width=210)
        self.nav.tag_configure("empty", foreground=MAPLE["empty"])
        self.nav.tag_configure("big", foreground=MAPLE["big"], font=(FONT, 10, "bold"))
        self.nav.tag_configure("mid", foreground=MAPLE["mid"], font=(FONT, 9, "bold"))
        nsb = ttk.Scrollbar(nav, command=self.nav.yview)
        self.nav.configure(yscrollcommand=nsb.set)
        nsb.pack(side="right", fill="y")
        self.nav.pack(side="left", fill="both", expand=True)
        body.add(nav, minsize=160)
        self.nav.bind("<<TreeviewSelect>>", lambda _: self.fill())
        right = tk.Frame(body, bg=MAPLE["panel"], highlightthickness=1, highlightbackground=MAPLE["border"])
        body.add(right, stretch="always")
        w = right
        cols = ["cat", "kind", "name"] + [f"T{t}" if t else "無T級" for t in TIERS] + ["n", "items"]
        self.tv = ttk.Treeview(w, columns=cols, show="headings", style="Maple.Treeview")
        heads = ["分類", "類型", "詞綴"] + [f"T{t}" if t else "無T級" for t in TIERS] + ["筆數", "裝備"]
        widths = [70, 45, 190] + [70] * len(TIERS) + [45, 150]
        for c, h, wd in zip(cols, heads, widths):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=wd, anchor="w" if c in ("name", "items") else "center")
        self.heads = dict(zip(cols, heads))
        sb = ttk.Scrollbar(w, command=self.tv.yview)
        hb = ttk.Scrollbar(w, orient="horizontal", command=self.tv.xview)   # 欄位比視窗寬時左右捲動
        self.tv.configure(yscrollcommand=lambda *a: (sb.set(*a), self._grid_later()),
                          xscrollcommand=lambda *a: (hb.set(*a), self._grid_later()))
        hb.pack(side="bottom", fill="x")
        sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        # 格線：Treeview 沒有格線，用 1px 的細條疊在表格上（像 Excel）
        self._lines, self._grid_job = [], None
        self.tv.bind("<Configure>", lambda _: self._grid_later(), add="+")
        self.tv.bind("<ButtonRelease-1>", lambda _: self._grid_later(), add="+")    # 拖拉欄寬之後
        self.tv.bind("<Double-Button-1>", self.details)
        self.tv.bind("<Button-3>", self._right_click)          # 右鍵：修改這一列的裝備名稱
        if keep:
            self.kind.set(keep["kind"])
            self.q.set(keep["q"])
        self.fill_nav()
        if keep and keep["sel"] and self.nav.exists(keep["sel"][0]):
            self.nav.selection_set(keep["sel"][0])
            self.nav.see(keep["sel"][0])
        self.fill()

    def fill_nav(self):
        """分類樹：大類 →（主武器／副武器）→ 分類；沒有紀錄的分類顯示成灰色"""
        count = {}
        for r in self.db:
            c = canon_category(r)
            count[c] = count.get(c, 0) + 1
        known = {c for *_, c in category_leaves()}
        nav = self.nav
        keep = nav.selection()
        nav.delete(*nav.get_children())
        self.nav_leaves = {"all": None}
        nav.insert("", "end", iid="all", text=f"全部（{len(self.db)}）", open=True, tags=("big",))
        for big, subs in CATEGORY_TREE:
            bid = "b:" + big
            nav.insert("", "end", iid=bid, text=big, open=True, tags=("big",))
            big_leaves = []
            for name, kinds in subs:
                if kinds:
                    sid = "s:" + name
                    # 表上沒有的武器種類放在主武器底下
                    extra = sorted(c for c in count if c not in known and c != "未分類") if name == "主武器" else []
                    leaves = kinds + extra
                    n = sum(count.get(k, 0) for k in leaves)
                    nav.insert(bid, "end", iid=sid, text=f"{name}（{n}）", open=True, tags=("mid",) if n else ("empty",))
                    for k in leaves:
                        nav.insert(sid, "end", iid="c:" + k, text=f"{k}（{count.get(k, 0)}）",
                                   tags=() if count.get(k) else ("empty",))
                        self.nav_leaves["c:" + k] = {k}
                    self.nav_leaves[sid] = set(leaves)
                    big_leaves += leaves
                else:
                    nav.insert(bid, "end", iid="c:" + name, text=f"{name}（{count.get(name, 0)}）",
                               tags=() if count.get(name) else ("empty",))
                    self.nav_leaves["c:" + name] = {name}
                    big_leaves.append(name)
            self.nav_leaves[bid] = set(big_leaves)
            n = sum(count.get(k, 0) for k in big_leaves)
            nav.item(bid, text=f"{big}（{n}）", tags=("big",) if n else ("empty",))
        if count.get("未分類"):
            nav.insert("", "end", iid="c:未分類", text=f"未分類（{count['未分類']}）")
            self.nav_leaves["c:未分類"] = {"未分類"}
        nav.selection_set(keep[0] if keep and nav.exists(keep[0]) else "all")

    def rows(self):
        kind, q = self.kind.get(), self.q.get().strip()
        sel = self.nav.selection()
        allow = self.nav_leaves.get(sel[0]) if sel else None
        groups = {}
        for r in self.db:
            cat = canon_category(r)
            if allow is not None and cat not in allow:
                continue
            leg = r.get("rarity") == "傳奇"
            k = "傳" if leg else r["kind"]
            if kind != "全部" and k != {"前綴": "前", "後綴": "後", "特殊": "", "傳奇": "傳"}[kind]:
                continue
            label = (f"【{r['item']}】" if leg else "") + affix_label(r["parts"])
            if q and q not in label:
                continue
            g = groups.setdefault((cat, k, (r["item"] if leg else "") + r["key"]),
                                  dict(label=label, tiers={}, recs=[], parts=set()))
            g["tiers"].setdefault(r["tier"], set()).add(range_text(r["parts"]))
            g["recs"].append(r)
            if r.get("item"):
                g["parts"].add(r["item"])                # 最後一欄：出現過的裝備名稱
        order = {"前": 0, "後": 1, "": 2, "傳": 3}
        cat_order = {c: i for i, (*_, c) in enumerate(category_leaves())}
        return sorted(groups.items(), key=lambda kv: (cat_order.get(kv[0][0], 99), kv[0][0],
                                                      order.get(kv[0][1], 9), kv[1]["label"]))

    def fill(self):
        self.tv.delete(*self.tv.get_children())
        self.keys = {}
        for k, (row_bg, sep_bg) in KIND_COLOR.items():
            self.tv.tag_configure("sep" + k, background=sep_bg, foreground="white", font=(FONT, 9, "bold"))
            self.tv.tag_configure("row" + k, background=row_bg)
        self.tv.tag_configure("legend", foreground=MAPLE["legend"])
        last = None
        for (cat, kind, key), g in self.rows():
            if (cat, kind) != last:                   # 分隔列：「戒指｜後綴」
                self.tv.insert("", "end", values=[cat, KIND_NAME.get(kind, "特殊"), f"◆ {cat}｜{KIND_NAME.get(kind, '特殊')}"],
                               tags=("sep" + kind,))
                last = (cat, kind)
            vals = [cat, KIND_NAME.get(kind, "特殊"), g["label"]]
            vals += [" / ".join(sorted(g["tiers"].get(t, []))) for t in TIERS]
            vals += [len(g["recs"]), "、".join(sorted(g["parts"]))]
            iid = self.tv.insert("", "end", values=vals,
                                 tags=("row" + kind,) + (("legend",) if kind == "傳" else ()))
            self.keys[iid] = g["recs"]
        if not self.keys:
            self.tv.insert("", "end", values=["", "還沒有紀錄：在遊戲裡用擷取快捷鍵讀裝備"])
        self.autosize()
        self._grid_later()

    def _grid_later(self):
        if self._grid_job is None:
            self._grid_job = self.tv.after_idle(self._draw_grid)

    def _draw_grid(self):
        """每一欄之間畫直線、每一列下面畫橫線"""
        self._grid_job = None
        tv = self.tv
        if not tv.winfo_exists():
            return
        rows = [b for b in (tv.bbox(i) for i in tv.get_children()) if b]
        n = 0

        def line(x, y, w, h):
            nonlocal n
            if n == len(self._lines):
                self._lines.append(tk.Frame(tv, bg=MAPLE["grid"], bd=0, highlightthickness=0))
            f = self._lines[n]
            f.configure(bg=MAPLE["grid"])
            f.place(x=x, y=y, width=w, height=h)
            n += 1
        if rows:
            top, bottom = rows[0][1], rows[-1][1] + rows[-1][3]
            cols = tv["columns"]
            widths = [tv.column(c, "width") for c in cols]
            right = sum(widths) - tv.xview()[0] * sum(widths)
            x = -tv.xview()[0] * sum(widths)
            for wd in widths:                          # 直線：每一欄的右邊
                x += wd
                if 0 < x < tv.winfo_width():
                    line(int(x) - 1, top, 1, bottom - top)
            span = int(min(right, tv.winfo_width()))
            for _, y, _, h in rows:                    # 橫線：每一列的下面
                line(0, y + h - 1, span, 1)
        for f in self._lines[n:]:
            f.place_forget()

    def autosize(self):
        """每一欄的寬度＝這一欄最長的內容（標題或數值），不用自己拉欄寬"""
        body = tkfont.nametofont("TkDefaultFont")
        head = tkfont.nametofont("TkHeadingFont")
        total = 0
        for c in self.tv["columns"]:
            wd = head.measure(self.heads[c]) + 18
            for iid in self.tv.get_children():
                wd = max(wd, body.measure(str(self.tv.set(iid, c))) + 18)
            wd = min(wd, 460)
            self.tv.column(c, width=wd, minwidth=wd, stretch=False)
            total += wd
        # 視窗第一次打開時，寬度跟著表格（不超過螢幕）
        if not getattr(self, "_sized", False):
            self._sized = True
            sw = self.win.winfo_screenwidth()
            self.win.geometry(f"{min(sw - 40, total + 40)}x600")
    def _right_click(self, e):
        iid = self.tv.identify_row(e.y)
        if iid:
            self.tv.selection_set(iid)
            recs = self.keys.get(iid)
            self.rename(recs[0]["item"] if recs else None)

    def rename(self, current=None):
        """把詞綴表裡某件裝備的名稱改掉（OCR 讀錯的名稱），同名的紀錄全部一起改"""
        names = sorted({r["item"] for r in self.db if r.get("item")})
        if not names:
            return
        if current is None:
            sel = self.tv.selection()
            recs = self.keys.get(sel[0]) if sel else None
            current = recs[0]["item"] if recs else names[0]
        d = tk.Toplevel(self.win, padx=12, pady=10)
        d.title("修改裝備名稱")
        d.transient(self.win)
        L = dict(font=(FONT, 10))
        tk.Label(d, text="原本的名稱", **L).grid(row=0, column=0, sticky="e", pady=3)
        old = tk.StringVar(value=current if current in names else names[0])
        ttk.Combobox(d, textvariable=old, values=names, state="readonly", width=24, **L).grid(row=0, column=1, sticky="w")
        tk.Label(d, text="新的名稱", **L).grid(row=1, column=0, sticky="e", pady=3)
        new = tk.StringVar(value=old.get())
        ent = tk.Entry(d, textvariable=new, width=26, **L)
        ent.grid(row=1, column=1, sticky="w")
        old.trace_add("write", lambda *_: new.set(old.get()))
        info = tk.Label(d, text="", fg="#666", font=(FONT, 9))
        info.grid(row=2, column=0, columnspan=2, sticky="w")

        def count(*_):
            n = sum(r["item"] == old.get() for r in self.db)
            info.configure(text=f"這件裝備有 {n} 筆詞綴紀錄，會全部一起改名")
        old.trace_add("write", count)
        count()

        def ok():
            o, n = old.get(), new.get().strip()
            if not n or n == o:
                d.destroy()
                return
            for r in self.db:
                if r["item"] == o:
                    r["item"] = n
            save_json(DB_FILE, self.db)
            d.destroy()
            self.fill()
        row = tk.Frame(d)
        row.grid(row=3, column=0, columnspan=2, pady=(8, 0))
        tk.Button(row, text="確定", width=8, command=ok, **L).pack(side="left", padx=4)
        tk.Button(row, text="取消", width=8, command=d.destroy, **L).pack(side="left", padx=4)
        ent.focus_set()
        ent.select_range(0, "end")
        d.bind("<Return>", lambda _: ok())

    def details(self, _):
        sel = self.tv.selection()
        recs = self.keys.get(sel[0]) if sel else None
        if not recs:
            return
        w = tk.Toplevel(self.win)
        w.title(affix_label(recs[0]["parts"]))
        cols = ("tier", "range", "value", "item", "part", "ilvl", "time")
        tv = ttk.Treeview(w, columns=cols, show="headings", height=14)
        for c, h, wd in zip(cols, ("T 級", "範圍", "實際數值", "裝備", "部位", "裝備等級", "擷取時間"),
                            (50, 120, 120, 150, 60, 70, 130)):
            tv.heading(c, text=h)
            tv.column(c, width=wd, anchor="center")
        tv.pack(fill="both", expand=True, padx=8, pady=8)
        for r in sorted(recs, key=lambda r: -r["tier"]):
            val = "，".join(f"{p['value']:g}{'%' if p['pct'] else ''}" for p in r["parts"] if p.get("value") is not None)
            tv.insert("", "end", values=(f"T{r['tier']}" if r["tier"] else "無T級", range_text(r["parts"]), val, r["item"],
                                         r.get("part", ""), r.get("ilvl", ""), r.get("time", "")))

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv", initialfile="詞綴表.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as fp:
            wr = csv.writer(fp)
            wr.writerow(["分類", "類型", "詞綴"] + [f"T{t}" if t else "無T級" for t in TIERS] + ["筆數", "裝備"])
            for (cat, kind, key), g in self.rows():
                wr.writerow([cat, KIND_NAME.get(kind, "特殊"), g["label"]] +
                            [" / ".join(sorted(g["tiers"].get(t, []))) for t in TIERS] +
                            [len(g["recs"]), "、".join(sorted(g["parts"]))])
