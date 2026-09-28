"""
角色資料表（仿遊戲的「角色資料」＋「詳細資料」視窗），學習或取消天賦時自動刷新。

基礎數值來自遊戲截圖（弓手 1～10 等、沒穿裝備）和角色資料的滑鼠說明：
  HP = 38 + 12 × 等級 + 2 × 力量，MP = 44 + 6 × 等級 + 2 × 智力，EP = 1000（1～10 等全部吻合）
  命中值 = 敏捷，迴避率 = 幸運 ÷ 10 %
  沒拿武器時攻擊力、魔攻顯示 0~0（弓手之路的攻擊力+10 也不會顯示），暴擊率 0%，防禦力 0
  四項能力基礎各 5（弓手之路再給力量、敏捷 +10）
  暴擊傷害 150%，攻擊/施法速度 100%，移動速度/跳躍力 100%，抗性上限 75%
裝備提供的攻擊力、魔法攻擊力、暴擊率、防禦力無法從天賦推算，請在下方「基礎與裝備數值」自己填。
推測的規則：
  精準技藝：每 2 點力量、智力各再給 1 命中值；魔法射手：敏捷不再提供命中值
  暴擊率 = 武器暴擊率 ×（1 + 暴擊率增幅%）
  HP、MP、防禦力 =（基礎 + 固定值）×（1 + 百分比）
  攻擊力：最大 =（武器攻擊力 + 天賦固定攻擊力）× 1.6 ×（1 + 攻擊力%），最小 = 最大 × 0.8
  魔法攻擊力 =（武器魔攻 + 天賦固定魔攻）× 1.6 ×（1 + 魔法攻擊力%），沒有浮動
    （單手劍 攻擊力+17 → 34~43、魔攻 16~16 完全吻合；只驗證過這一把，×1.6 的來源還不確定）
"""
import json
import os
import re
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "character.json")
FONT = "Microsoft JhengHei"

# 預設＝沒穿任何裝備、沒拿武器（遊戲裡攻擊力、魔攻 0~0，暴擊率 0%，防禦力 0）
DEFAULTS = dict(name="", level=1)
EQUIP_FIELDS = [("level", "等級")]      # 武器、裝備改在「角色狀態」視窗穿戴
WEAPON_MULT = 1.6      # 面板攻擊力 =（武器 + 天賦固定值）× 1.6
ATK_MIN_RATIO = 0.8    # 最小攻擊力 = 最大 × 0.8

ELEMENTS = ["火焰", "冰冷", "閃電", "毒素", "神聖"]
AILMENTS = [("避免流血機率", "避免點燃機率"), ("避免冰緩機率", "避免感電機率"),
            ("避免中毒機率", "避免致盲機率"), ("避免暈眩機率", "詛咒效果減免")]

# 效果名稱 → 表上的欄位（一個效果可以加到好幾個欄位）
ALIASES = {
    "攻擊與施法速度": ["攻擊速度", "施法速度"],
    "迴避機率": ["迴避率"],
    "暴擊機率增幅": ["暴擊率增幅"],
    "全部屬性抗性": [f"{e}抗性" for e in ELEMENTS],
    "火焰、冰冷、閃電、神聖抗性": ["火焰抗性", "冰冷抗性", "閃電抗性", "神聖抗性"],
    "全能力": ["力量", "敏捷", "智力", "幸運"],
}

# 遊戲「角色資料」視窗裡每個欄位的滑鼠說明（2026-09 從遊戲 v0.7.7 擷取）
TOOLTIPS = {
    "名稱": "角色名稱。", "等級": "角色等級。", "公會": "所屬的公會名稱。",
    "HP": "生命，歸零即死亡。", "MP": "魔力，施放技能需要的資源。", "EP": "活力，使用藥水需要的資源。",
    "經驗值": "狩獵怪物累積至上限即可提升等級。", "名聲": "顯示其他玩家對你的評價。",
    "力量": "不提供傷害加乘。\n每1點力量提供2點HP。",
    "敏捷": "不提供傷害加乘。\n每1點敏捷提供1點命中值。",
    "智力": "不提供傷害加乘。\n每1點智力提供2點MP。",
    "幸運": "不提供傷害加乘。\n每10點幸運提供1%迴避率。",
    "物理減傷": "降低受到的物理傷害。",
    **{f"{e}抗性": f"降低受到的{e}傷害。" for e in ELEMENTS},
    "攻擊力": "攻擊力用於計算攻擊傷害。", "魔法攻擊力": "魔法攻擊力用於計算法術傷害。",
    "命中值": "用於計算攻擊與法術是否命中。",
    "暴擊傷害": "普攻發動暴擊時的傷害倍率。", "暴擊率": "普攻發動暴擊的機率。",
    "攻擊速度": "提高普攻與攻擊技能速度。", "施法速度": "提高法術技能施放速度。",
    "防禦力": "本身不參與傷害計算，會依照比例換算成物理減傷與避免暈眩機率。",
    "每秒HP恢復": "每秒恢復的HP。", "每秒MP恢復": "每秒恢復的MP。",
    "移動速度": "影響角色移動的速度。", "跳躍力": "影響角色跳躍的高度。",
    "迴避率": "成功躲避怪物攻擊的機率。", "格擋率": "成功擋住怪物攻擊的機率。",
    **{f"避免{a}機率": f"降低受到{a}的機率。" for a in ("流血", "點燃", "冰緩", "感電", "中毒", "致盲", "暈眩")},
    "詛咒效果減免": "降低受到詛咒的效果。",
}

C = dict(frame="#8fa5bd", panel="#eeede6", cell="#e6e3d4", pink="#d8517c", olive="#aebf3c",
         blue="#5873ad", head="#4e6a9e", gray="#6d6d6d", text="#222222", up="#1c8a2c",
         down="#c03030", maxc="#4a6fc0")


def parse(effects):
    """把效果文字加總成 {(名稱, 是否百分比): 數值}，另外回傳特殊規則"""
    tot, flags = {}, set()

    def add(key, pct, v):
        for k in ALIASES.get(key, [key]):
            tot[(k, pct)] = tot.get((k, pct), 0) + v

    for line in effects:
        m = re.match(r"^每秒(HP恢復|恢復MP)\+([\d.]+)%最大(HP|MP)$", line)
        if m:
            add("每秒HP恢復%" if m.group(3) == "HP" else "每秒MP恢復%", False, float(m.group(2)))
            continue
        # 裝備秘藏能力的寫法：生命每秒回復 +2% ＝ 每秒恢復 2% 最大HP
        m = re.match(r"^(生命每秒回復|每秒生命回復|魔力每秒回復|每秒魔力回復)\+([\d.]+)%$", line)
        if m:
            add("每秒HP恢復%" if "生命" in m.group(1) else "每秒MP恢復%", False, float(m.group(2)))
            continue
        if line.startswith("最大抗性+"):                      # 稜鏡護體（含物理）
            v = float(re.search(r"[\d.]+", line).group())
            for e in ELEMENTS:
                add(f"最大{e}抗性", True, v)
            add("最大物理減傷", True, v)
            continue
        if "每2點力量提供1命中值" in line:
            flags.add("str_hit")
        if "每2點智力提供1命中值" in line:
            flags.add("int_hit")
        if "敏捷不再提供命中值" in line:
            flags.add("no_dex_hit")
        if "力量不再提供HP" in line:
            flags.add("no_str_hp")
        if "智力不再提供MP" in line:
            flags.add("no_int_mp")
        if "幸運不再提供迴避率" in line:
            flags.add("no_luk_eva")
        m = re.match(r"^(.*?)([+-]\d+(?:\.\d+)?)(%?)$", line)
        if m:
            add(m.group(1), m.group(3) == "%", float(m.group(2)))
    return tot, flags


# 武器分類（和遊戲武器說明的「武器分類」一致）
ONE_HAND = ["單手劍", "單手斧", "單手棍", "短劍", "短杖", "拳套"]
TWO_HAND = ["雙手劍", "雙手斧", "雙手棍", "槍矛", "長杖", "弓弩"]
WEAPON_TYPES = ONE_HAND + TWO_HAND
OFFHAND_TYPES = ["盾牌", "箭矢", "飛鏢", "單手劍", "單手斧", "單手棍", "短劍", "短杖"]

# 天賦上「使用XX時」的條件 → 目前裝備是否符合
CONDITIONS = [
    ("使用單手劍/斧或雙手劍/斧時", lambda w, o: w in ("單手劍", "雙手劍", "單手斧", "雙手斧")),
    ("使用單手劍或雙手劍時", lambda w, o: w in ("單手劍", "雙手劍")),
    ("使用單手斧或雙手斧時", lambda w, o: w in ("單手斧", "雙手斧")),
    ("使用單手棍或雙手棍時", lambda w, o: w in ("單手棍", "雙手棍")),
    ("使用單手劍或短劍時", lambda w, o: w in ("單手劍", "短劍")),
    ("使用單手武器時", lambda w, o: w in ONE_HAND),
    ("使用雙手武器時", lambda w, o: w in TWO_HAND),
    ("單手武器", lambda w, o: w in ONE_HAND),
    ("雙手武器", lambda w, o: w in TWO_HAND),
    ("使用槍矛時", lambda w, o: w == "槍矛"),
    ("使用短劍時", lambda w, o: w == "短劍"),
    ("使用短杖時", lambda w, o: w == "短杖"),
    ("使用長杖時", lambda w, o: w == "長杖"),
    ("使用弓弩時", lambda w, o: w == "弓弩"),
    ("使用拳套時", lambda w, o: w == "拳套"),
    ("不使用盾牌時", lambda w, o: o != "盾牌"),
    ("使用盾牌時", lambda w, o: o == "盾牌"),
    ("持盾時", lambda w, o: o == "盾牌"),
    ("雙持武器時", lambda w, o: o in ONE_HAND),
    ("裝備箭矢或飛鏢時", lambda w, o: o in ("箭矢", "飛鏢")),
] + [(f"副手使用{t}時", (lambda t: lambda w, o: o == t)(t)) for t in OFFHAND_TYPES]
RENAME = {"格檔率": "格擋率", "物理攻擊力": "攻擊力", "攻擊傷害": "傷害"}


def resolve(effects, gear):
    """把「使用XX時」的效果依目前的武器/副手換成一般效果，不符合的拿掉"""
    w, o = (gear or {}).get("weapon"), (gear or {}).get("offhand")
    out = []
    for line in effects:
        if line.startswith("使用弓弩時若沒有裝備箭矢"):          # 無形之箭
            if w == "弓弩" and o != "箭矢":
                out.append("攻擊基礎暴擊率+3%")
            continue
        for prefix, ok in CONDITIONS:
            if line.startswith(prefix):
                if ok(w, o):
                    rest = line[len(prefix):].lstrip("，,")
                    for a, b in RENAME.items():
                        if rest.startswith(a):
                            rest = b + rest[len(a):]
                    out.append(rest)
                break
        else:
            out.append(line)
    return out


def split_item_line(line):
    """一行裡有好幾個屬性（武器攻擊力：+3%，命中值：+3）→ 拆開並轉成效果寫法"""
    return [normalize_item_line(x) for x in re.split(r"[，,、]", line) if x.strip()]


def normalize_item_line(line):
    """裝備說明的寫法（攻擊力：+17、攻擊基礎暴擊率%：+5、冰冷屬性抗性：+17%）
    → 效果寫法（攻擊力+17、攻擊基礎暴擊率+5%、冰冷抗性+17%）"""
    line = line.strip().replace("：", "").replace(":", "").replace(" ", "").replace("屬性抗性", "抗性")
    m = re.match(r"^(.*?)%([+-][\d.]+)$", line)
    if m:
        return f"{m.group(1)}{m.group(2)}%"
    return line


def compute(effects, s, gear=None):
    """依天賦＋裝備效果算出表上每一格的數值。gear = {"weapon": 武器分類, "offhand": 副手分類}"""
    t, flags = parse(resolve(effects, gear))
    f = lambda k: t.get((k, False), 0)       # 固定值
    p = lambda k: t.get((k, True), 0)        # 百分比
    lv = max(1, int(s["level"]))
    v = {}
    for stat in ("力量", "敏捷", "智力", "幸運"):
        v[stat] = 5 + f(stat)
    # 遊戲說明：每 1 點力量提供 2 HP、每 1 點智力提供 2 MP（1～10 等的截圖全部吻合）
    str_hp = 0 if "no_str_hp" in flags else 2 * v["力量"]
    int_mp = 0 if "no_int_mp" in flags else 2 * v["智力"]
    v["HP"] = (38 + 12 * lv + str_hp + f("HP")) * (1 + p("HP") / 100)
    v["MP"] = (44 + 6 * lv + int_mp + f("MP")) * (1 + p("MP") / 100)
    v["EP"] = 1000 + f("EP")
    armed = bool((gear or {}).get("weapon"))     # 沒拿武器時遊戲顯示 0~0
    hi = (f("攻擊力")) * WEAPON_MULT * (1 + p("攻擊力") / 100)      # 武器的攻擊力也算在固定值裡
    v["攻擊力"] = (hi * ATK_MIN_RATIO, hi) if armed else (0, 0)
    m = (f("魔法攻擊力")) * WEAPON_MULT * (1 + p("魔法攻擊力") / 100)
    v["魔法攻擊力"] = (m, m) if armed else (0, 0)
    hit = (0 if "no_dex_hit" in flags else v["敏捷"]) + f("命中值")
    hit += (v["力量"] // 2 if "str_hit" in flags else 0) + (v["智力"] // 2 if "int_hit" in flags else 0)
    v["命中值"] = hit * (1 + p("命中值") / 100)
    v["暴擊傷害"] = 150 + p("暴擊傷害")
    v["暴擊率"] = (p("攻擊基礎暴擊率") if armed else 0) * (1 + p("暴擊率增幅") / 100)
    v["攻擊速度"] = 100 + p("攻擊速度")
    v["施法速度"] = 100 + p("施法速度")
    v["防禦力"] = f("防禦力") * (1 + p("防禦力") / 100)
    v["每秒HP恢復"] = v["HP"] * f("每秒HP恢復%") / 100
    v["每秒MP恢復"] = v["MP"] * f("每秒MP恢復%") / 100
    v["移動速度"] = 100 + p("移動速度")
    v["跳躍力"] = 100 + p("跳躍力")
    # 遊戲說明：每 10 點幸運提供 1% 迴避率（遊戲只顯示整數，幸運 5 顯示 0%）
    v["迴避率"] = (0 if "no_luk_eva" in flags else v["幸運"] / 10) + p("迴避率")
    v["格擋率"] = p("格擋率")
    for a, b in AILMENTS:
        v[a] = p(a)
        v[b] = p(b)
    v["物理減傷"] = (p("物理減傷"), 75 + p("最大物理減傷"))
    for e in ELEMENTS:
        v[f"{e}抗性"] = (p(f"{e}抗性"), 75 + p(f"最大{e}抗性"))
    return v


def fmt(key, val):
    if key in ("攻擊力", "魔法攻擊力"):
        return f"{int(val[0])}~{int(val[1])}"
    if key in ("HP", "MP", "EP"):
        return f"{int(val)}/{int(val)}"
    if key in ("力量", "敏捷", "智力", "幸運", "防禦力", "命中值", "每秒HP恢復", "每秒MP恢復"):
        return f"{int(val)}"
    if key == "迴避率":
        return f"{int(val)}%"
    if key == "暴擊率":                         # 遊戲只顯示到小數第一位（無條件捨去）：34.65 → 34.6
        return f"{int(val * 10 + 1e-6) / 10:g}%"
    if key == "詛咒效果減免":
        return f"{val:g}%" if val else "-"
    return f"{val:g}%"


class CharacterSheet:
    def __init__(self, app):
        self.app = app
        self.settings = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fp:
                self.settings.update(json.load(fp))
        except (OSError, ValueError):
            pass
        self.win = None
        self.cells = {}

    # ---------- 視窗
    def build(self, parent):
        """把整個角色資料介面建在 parent 裡（主視窗的分頁或彈出的獨立視窗）"""
        w = self.win = tk.Frame(parent, bg=C["frame"])
        w.pack(fill="both", expand=True)
        self.cells = {}
        left = self._window(w, "角色資料", 0)
        right = self._window(w, "詳細資料", 1)
        self._build_basic(left)
        self._build_detail(right)
        self._build_equip(w)
        self.refresh()

    def _window(self, parent, title, col):
        outer = tk.Frame(parent, bg="white", bd=1, relief="solid")
        outer.grid(row=0, column=col, sticky="n", padx=(6, 3) if col == 0 else (3, 6), pady=6)
        tk.Label(outer, text=title, bg="white", anchor="w", font=(FONT, 10, "bold"),
                 padx=6).pack(fill="x")
        body = tk.Frame(outer, bg=C["panel"], padx=6, pady=6)
        body.pack(fill="both")
        return body

    def _label(self, parent, text, color, width=7):
        lab = tk.Label(parent, text=text, bg=color, fg="white", width=width,
                       font=(FONT, 9, "bold"), pady=1)
        if text in TOOLTIPS:
            lab.bind("<Enter>", lambda e, t=text: self._show_tip(e.widget, t))
            lab.bind("<Leave>", lambda e: self._hide_tip())
        return lab

    def _show_tip(self, widget, key):
        """和遊戲一樣：深藍半透明框，粗體標題＋說明"""
        self._hide_tip()
        tip = self.tip = tk.Toplevel(self.win)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        try:
            tip.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        box = tk.Frame(tip, bg="#3b3f7a", padx=14, pady=8)
        box.pack()
        tk.Label(box, text=key, bg="#3b3f7a", fg="white", font=(FONT, 12, "bold")).pack()
        tk.Label(box, text=TOOLTIPS[key], bg="#3b3f7a", fg="white", font=(FONT, 10),
                 justify="left", wraplength=190).pack(anchor="w", pady=(4, 0))
        tip.geometry(f"+{widget.winfo_rootx() + 10}+{widget.winfo_rooty() + widget.winfo_height() + 4}")

    def _hide_tip(self):
        if getattr(self, "tip", None):
            self.tip.destroy()
            self.tip = None

    def _value(self, parent, key, width=14, anchor="w"):
        lab = tk.Label(parent, text="", bg=C["cell"], fg=C["text"], width=width, anchor=anchor,
                       font=(FONT, 9), padx=4, pady=1)
        self.cells[key] = lab
        return lab

    def _build_basic(self, b):
        rows = [("名稱", C["pink"]), ("等級", C["pink"]), ("HP", C["pink"]), ("MP", C["pink"]),
                ("EP", C["pink"]), ("力量", C["olive"]), ("敏捷", C["olive"]),
                ("智力", C["olive"]), ("幸運", C["olive"])]
        for i, (key, color) in enumerate(rows):
            self._label(b, key, color).grid(row=i, column=0, sticky="ew", pady=1)
            self._value(b, key, 16).grid(row=i, column=1, sticky="ew", padx=(3, 0), pady=1)
        res = tk.Frame(b, bg=C["panel"])
        res.grid(row=len(rows), column=0, columnspan=2, pady=(6, 0))
        for i, key in enumerate(["物理減傷"] + [f"{e}抗性" for e in ELEMENTS]):
            r, c = divmod(i, 3)
            box = tk.Frame(res, bg=C["panel"])
            box.grid(row=r, column=c, padx=2, pady=2)
            self._label(box, key, C["gray"], 8).pack(fill="x")
            val = tk.Frame(box, bg="white", bd=1, relief="solid")
            val.pack(fill="x")
            a = tk.Label(val, text="", bg="white", font=(FONT, 10))
            m = tk.Label(val, text="", bg="white", fg=C["maxc"], font=(FONT, 10))
            a.pack(side="left", expand=True, anchor="e")
            m.pack(side="left", expand=True, anchor="w")
            self.cells[key] = (a, m)

    def _section(self, b, row, title):
        tk.Label(b, text=f"▼        {title}        ▼", bg=C["head"], fg="white",
                 font=(FONT, 9, "bold")).grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 2))

    def _pair(self, b, row, k1, k2):
        self._label(b, k1, C["blue"], 9).grid(row=row, column=0, sticky="ew", pady=1)
        self._value(b, k1, 6, "center").grid(row=row, column=1, sticky="ew", padx=2, pady=1)
        self._label(b, k2, C["blue"], 9).grid(row=row, column=2, sticky="ew", pady=1)
        self._value(b, k2, 6, "center").grid(row=row, column=3, sticky="ew", padx=(2, 0), pady=1)

    def _wide(self, b, row, key):
        self._label(b, key, C["blue"], 9).grid(row=row, column=0, sticky="ew", pady=1)
        self._value(b, key, 6, "center").grid(row=row, column=1, columnspan=3, sticky="ew",
                                             padx=(2, 0), pady=1)

    def _build_detail(self, b):
        r = 0
        self._section(b, r, "攻擊屬性"); r += 1
        for key in ("攻擊力", "魔法攻擊力", "命中值"):
            self._wide(b, r, key); r += 1
        self._pair(b, r, "暴擊傷害", "暴擊率"); r += 1
        self._pair(b, r, "攻擊速度", "施法速度"); r += 1
        self._section(b, r, "防禦屬性"); r += 1
        self._wide(b, r, "防禦力"); r += 1
        self._pair(b, r, "每秒HP恢復", "每秒MP恢復"); r += 1
        self._pair(b, r, "移動速度", "跳躍力"); r += 1
        self._pair(b, r, "迴避率", "格擋率"); r += 1
        self._section(b, r, "異常狀態"); r += 1
        for a, c in AILMENTS:
            self._pair(b, r, a, c); r += 1

    def _build_equip(self, w):
        box = tk.LabelFrame(w, text=" 基本設定 ", bg=C["frame"],
                            fg="white", font=(FONT, 9, "bold"), padx=6, pady=4)
        box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        tk.Label(box, text="名稱", bg=C["frame"], fg="white", font=(FONT, 9)).grid(row=0, column=0, sticky="e")
        self.name_var = tk.StringVar(value=self.settings["name"])
        tk.Entry(box, textvariable=self.name_var, width=12).grid(row=0, column=1, sticky="w", padx=4, pady=1)
        self.vars = {}
        for i, (key, text) in enumerate(EQUIP_FIELDS, start=1):
            r, c = divmod(i, 2)
            tk.Label(box, text=text, bg=C["frame"], fg="white", font=(FONT, 9)).grid(
                row=r, column=c * 2, sticky="e")
            val = self.settings[key]
            var = tk.StringVar(value=f"{val:g}" if isinstance(val, float) else str(val))
            tk.Spinbox(box, textvariable=var, from_=0, to=9999, width=7).grid(
                row=r, column=c * 2 + 1, sticky="w", padx=4, pady=1)
            self.vars[key] = var
        tk.Label(box, text="綠色＝天賦讓數值提高，紅色＝降低（和只穿目前裝備、沒點天賦時比較）。武器與裝備請在「角色狀態」視窗穿戴。",
                 bg=C["frame"], fg="#f4f4f4", font=(FONT, 8), wraplength=440, justify="left").grid(
            row=9, column=0, columnspan=4, sticky="w", pady=(4, 0))
        # 建好之後才掛上監聽，避免建立時就一直存檔
        self.name_var.trace_add("write", lambda *_: self._on_edit())
        for var in self.vars.values():
            var.trace_add("write", lambda *_: self._on_edit())

    def _on_edit(self):
        for key, var in self.vars.items():
            try:
                self.settings[key] = float(var.get())
            except ValueError:
                pass
        self.settings["name"] = self.name_var.get()
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fp:
                json.dump(self.settings, fp, ensure_ascii=False)
        except OSError:
            pass
        self.refresh()

    # ---------- 刷新
    def refresh(self):
        if not (self.win and self.win.winfo_exists()):
            return
        app = self.app
        gear_fx, gear = app.equipment.effects()
        effects = [e for k in app.allocated for e in app.effects_of(k)] + gear_fx
        buffs = app.skills_page.active_buffs() if hasattr(app, "skills_page") else []
        for _, lines, _ in buffs:                  # 開啟中的增益技能
            effects += lines
        reserve = min(100.0, sum(r for *_, r in buffs))
        cur = compute(effects, self.settings, gear)
        # 只有起點＋目前裝備時的數值，用來標示天賦的影響
        base = compute(list(app.effects_of(app.start)) + gear_fx, self.settings, gear)

        def color(a, b):
            return C["up"] if a > b + 1e-9 else C["down"] if a < b - 1e-9 else C["text"]

        self.cells["名稱"].configure(text=self.settings["name"])
        self.cells["等級"].configure(text=str(int(self.settings["level"])))
        for key, cell in self.cells.items():
            if key in ("名稱", "等級"):
                continue
            if isinstance(cell, tuple):
                (v, mx), (bv, bmx) = cur[key], base[key]
                cell[0].configure(text=f"{v:g}%", fg=color(v, bv))
                cell[1].configure(text=f"({mx:g}%)", fg=C["up"] if mx > bmx else C["maxc"])
            else:
                a, b = cur[key], base[key]
                if isinstance(a, tuple):
                    a, b = a[0], b[0]
                txt = fmt(key, cur[key])
                if key == "MP" and reserve:           # 保留的 MP：目前 MP = 最大 × (1 − 保留%)
                    txt = f"{int(cur[key] * (1 - reserve / 100) + 0.5)}/{int(cur[key])}"
                cell.configure(text=txt, fg=color(a, b))
