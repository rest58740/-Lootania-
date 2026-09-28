"""
角色狀態（裝備欄），仿遊戲的「角色狀態」視窗。

  背包裡的裝備拖到格子上＝穿上；格子上的裝備拖回背包（或按右鍵）＝脫下
  背包裡雙擊＝穿到第一個能穿的格子；格子上雙擊＝編輯那件裝備
  點空的格子＝新增一件該部位的裝備並直接穿上

裝備的屬性照遊戲說明寫（一行一個，例如「攻擊力：+17」「攻擊基礎暴擊率%：+5」），
穿上後會一起算進角色資料／詳細資料；武器分類和副手分類會決定天賦上「使用XX時」的效果是否生效。
秘藏能力另外填（可以在行尾加 T1～T10）。遊戲說明上紫色的數值已經含秘藏能力，
所以和基本屬性同名的秘藏能力、以及「武器攻擊力」不會重複計算（只記錄）。
所有裝備的秘藏能力會整理成「詞綴紀錄」表，慢慢累積出各 T 級的數值範圍。
資料存在同資料夾的 equipment.json。
"""
import json
import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

from character_sheet import (ALIASES, ELEMENTS, OFFHAND_TYPES, WEAPON_TYPES, AILMENTS,
                             normalize_item_line, split_item_line)

HERE = os.path.dirname(os.path.abspath(__file__))
ITEMS_FILE = os.path.join(HERE, "equipment.json")
OLD_SETTINGS = os.path.join(HERE, "character.json")
FONT = "Microsoft JhengHei"

# (格子 id, 部位, 列, 欄, 粉紅色) —— 位置和顏色照遊戲的角色狀態視窗
SLOTS = [
    ("頭盔", "頭盔", 0, 1, False),
    ("勳章", "勳章", 1, 0, True), ("臉飾", "臉飾", 1, 1, True), ("戒指1", "戒指", 1, 3, True), ("戒指2", "戒指", 1, 4, True),
    ("眼鏡", "眼鏡", 2, 2, True), ("耳環", "耳環", 2, 3, False),
    ("披風", "披風", 3, 0, False), ("衣服", "衣服", 3, 1, False), ("墜飾", "墜飾", 3, 2, True),
    ("武器", "武器", 3, 3, False), ("副手", "副手", 3, 4, False),
    ("手套", "手套", 4, 0, False), ("褲子", "褲子", 4, 1, False), ("腰帶", "腰帶", 4, 2, True),
    ("戒指3", "戒指", 4, 3, False), ("戒指4", "戒指", 4, 4, False),
    ("鞋子", "鞋子", 5, 2, False), ("寵物HP", "寵物HP", 5, 4, True),
    ("馴服怪物", "馴服怪物", 6, 0, True), ("馬鞍", "馬鞍", 6, 1, True), ("寵物裝備", "寵物裝備", 6, 2, False),
    ("寵物MP", "寵物MP", 6, 4, True),
]
SLOT_TYPE = {k: t for k, t, *_ in SLOTS}
PART_TYPES = list(dict.fromkeys(t for _, t, *_ in SLOTS))

# 角色資料表會用到的屬性名稱（用來標示裝備上哪幾行會被計算）
KNOWN_STATS = {"力量", "敏捷", "智力", "幸運", "HP", "MP", "EP", "攻擊力", "魔法攻擊力", "命中值",
               "暴擊傷害", "暴擊率增幅", "攻擊基礎暴擊率", "攻擊速度", "施法速度", "防禦力",
               "移動速度", "跳躍力", "迴避率", "格擋率", "物理減傷", "最大物理減傷",
               *ALIASES, *(f"{e}抗性" for e in ELEMENTS), *(f"最大{e}抗性" for e in ELEMENTS),
               *(a for pair in AILMENTS for a in pair)}

C = dict(frame="#8fa5bd", panel="#eeede6", blue="#c9d7e6", pink="#e8a6b2", blue_t="#7d8fa6",
         pink_t="#b5566a", worn="#fff6d8", worn_border="#c9a44a", tip="#3b3f7a", sel="#ffe9a8")


STAT_RE = re.compile(r"^(.*?)([+-]\d+(?:\.\d+)?)(%?)$")
TIER_RE = re.compile(r"\s*[Tt](10|[1-9])\s*$")
MERGED_AFFIXES = {"武器攻擊力"}     # 已經算進武器說明上紫色的攻擊力


def recognized(line):
    """這行屬性會不會被算進角色資料表"""
    parts = [STAT_RE.match(x) for x in split_item_line(line)]
    return bool(parts) and all(m and m.group(1) in KNOWN_STATS for m in parts)


def split_tier(text):
    """「荊棘傷害：+50% T3」→ ("荊棘傷害：+50%", "T3")"""
    m = TIER_RE.search(text)
    return (text[:m.start()].strip(), f"T{m.group(1)}") if m else (text.strip(), "")


def affix_parts(item):
    """[(效果寫法, 是否計算, 原文, T級)]；和基本屬性同名的秘藏能力已含在紫色數值裡，不重複計算"""
    base_names = {STAT_RE.match(e).group(1) for x in item["lines"] for e in split_item_line(x)
                  if STAT_RE.match(e)}
    out = []
    for text in item.get("affixes", []):
        body, tier = split_tier(text)
        body = re.sub(r"^\[(前|後)\]\s*", "", body)
        for e in split_item_line(body):
            m = STAT_RE.match(e)
            name = m.group(1) if m else e
            counted = bool(m) and name not in base_names and name not in MERGED_AFFIXES
            out.append((e, counted, body, tier))
            # 遊戲的基本屬性只顯示整數（小數被捨去）：+6 其實是 5＋1.9＝6.9，把秘藏能力的小數部分補回來
            if m and name in base_names and name not in MERGED_AFFIXES:
                frac = round(abs(float(m.group(2))) % 1, 4)
                if frac:
                    out.append((f"{name}+{frac:g}{m.group(3)}", True, body + "（整數以外的小數）", tier))
    return out


class Equipment:
    def __init__(self, app):
        self.app = app
        self.items, self.equipped, self.next_id = {}, {}, 1
        self.win = None
        self.selected = None
        self.drag = None
        self.tip = None
        self._load()

    # ---------- 資料
    def _load(self):
        try:
            with open(ITEMS_FILE, encoding="utf-8") as fp:
                data = json.load(fp)
            self.items = data.get("items", {})
            self.equipped = {k: v for k, v in data.get("equipped", {}).items()
                             if k in SLOT_TYPE and v in self.items}
            self.next_id = data.get("next_id", len(self.items) + 1)
            for it in self.items.values():
                it.setdefault("affixes", [])
                if it.get("category") in ("弓", "弩"):      # 遊戲的分類叫「弓弩」
                    it["category"] = "弓弩"
        except (OSError, ValueError):
            self._migrate()

    def _migrate(self):
        """舊版在角色資料表手動填的武器數值 → 轉成一件裝備"""
        try:
            with open(OLD_SETTINGS, encoding="utf-8") as fp:
                old = json.load(fp)
        except (OSError, ValueError):
            return
        lines = []
        if old.get("atk"):
            lines.append(f"攻擊力：+{old['atk']:g}")
        if old.get("matk"):
            lines.append(f"魔法攻擊力：+{old['matk']:g}")
        if old.get("crit"):
            lines.append(f"攻擊基礎暴擊率%：+{old['crit']:g}")
        if lines:
            iid = self._add(dict(name="劍", part="武器", category="單手劍", lines=lines))
            self.equipped["武器"] = iid
            self.save()

    def _add(self, item):
        iid = str(self.next_id)
        self.next_id += 1
        self.items[iid] = item
        return iid

    def save(self):
        try:
            with open(ITEMS_FILE, "w", encoding="utf-8") as fp:
                json.dump(dict(items=self.items, equipped=self.equipped, next_id=self.next_id),
                          fp, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def effects(self):
        """(穿著的裝備的效果行, {"weapon": 武器分類, "offhand": 副手分類})"""
        lines = []
        for iid in self.equipped.values():
            it = self.items[iid]
            lines += [e for x in it["lines"] if x.strip() for e in split_item_line(x)]
            lines += [e for e, counted, *_ in affix_parts(it) if counted]
        gear = {}
        if "武器" in self.equipped:
            gear["weapon"] = self.items[self.equipped["武器"]].get("category") or "未分類"
        if "副手" in self.equipped:
            gear["offhand"] = self.items[self.equipped["副手"]].get("category") or "未分類"
        return lines, gear

    def changed(self):
        self.save()
        self.refresh()
        self.app.sheet.refresh()

    def worn_slot(self, iid):
        return next((k for k, v in self.equipped.items() if v == iid), None)

    def equip(self, iid, slot):
        item = self.items[iid]
        if SLOT_TYPE[slot] != item["part"]:
            self.status(f"「{item['name']}」是{item['part']}，不能穿在{SLOT_TYPE[slot]}格")
            self.win.bell()
            return
        old_slot = self.worn_slot(iid)
        if old_slot:
            del self.equipped[old_slot]
            if slot in self.equipped:                # 兩個戒指格互換
                self.equipped[old_slot] = self.equipped[slot]
        self.equipped[slot] = iid
        self.status(f"穿上「{item['name']}」")
        self.changed()

    def unequip(self, slot):
        iid = self.equipped.pop(slot, None)
        if iid:
            self.status(f"脫下「{self.items[iid]['name']}」")
            self.changed()

    def auto_equip(self, iid):
        slot = self.worn_slot(iid)
        if slot:
            self.unequip(slot)
            return
        part = self.items[iid]["part"]
        slots = [k for k, t, *_ in SLOTS if t == part]
        free = [k for k in slots if k not in self.equipped]
        self.equip(iid, (free or slots)[0])

    # ---------- 視窗
    def build(self, parent):
        """把整個角色狀態介面建在 parent 裡（主視窗的分頁或彈出的獨立視窗）"""
        w = self.win = tk.Frame(parent, bg=C["frame"])
        w.pack(fill="both", expand=True)

        left = tk.Frame(w, bg="white", bd=1, relief="solid")
        left.grid(row=0, column=0, sticky="n", padx=(6, 3), pady=6)
        tk.Label(left, text="角色狀態", bg="white", anchor="w", font=(FONT, 10, "bold"),
                 padx=6).pack(fill="x")
        grid = tk.Frame(left, bg="white", padx=6, pady=6)
        grid.pack()
        self.cells = {}
        for key, part, r, c, pink in SLOTS:
            cell = tk.Frame(grid, width=60, height=50, bg=C["pink"] if pink else C["blue"],
                            highlightthickness=1, highlightbackground="white")
            cell.grid(row=r, column=c, padx=1, pady=1)
            cell.pack_propagate(False)
            lab = tk.Label(cell, text=part, bg=cell["bg"], fg=C["pink_t"] if pink else C["blue_t"],
                           font=(FONT, 10, "bold"), wraplength=56)
            lab.pack(expand=True, fill="both")
            for wdg in (cell, lab):
                wdg.slot_key = key
                wdg.bind("<ButtonPress-1>", lambda e, k=key: self._press(e, ("slot", k)))
                wdg.bind("<B1-Motion>", self._motion)
                wdg.bind("<ButtonRelease-1>", self._release)
                wdg.bind("<Double-Button-1>", lambda e, k=key: self._edit_slot(k))
                wdg.bind("<Button-3>", lambda e, k=key: self.unequip(k))
                wdg.bind("<Enter>", lambda e, k=key: self._hover_slot(e, k))
                wdg.bind("<Leave>", lambda e: self._hide_tip())
            self.cells[key] = (cell, lab, pink)
        for r in range(7):
            for c in range(5):
                if not any(s[2] == r and s[3] == c for s in SLOTS):
                    tk.Frame(grid, width=60, height=50, bg="white").grid(row=r, column=c, padx=1, pady=1)

        right = tk.Frame(w, bg="white", bd=1, relief="solid")
        right.grid(row=0, column=1, sticky="ns", padx=(3, 6), pady=6)
        tk.Label(right, text="背包", bg="white", anchor="w", font=(FONT, 10, "bold"),
                 padx=6).pack(fill="x")
        btns = tk.Frame(right, bg="white")
        btns.pack(fill="x", padx=6)
        b = dict(font=(FONT, 9), relief="groove", padx=6)
        tk.Button(btns, text="新增裝備", command=lambda: self.editor(None), **b).pack(side="left")
        tk.Button(btns, text="編輯", command=lambda: self.selected and self.editor(self.selected), **b).pack(side="left", padx=4)
        tk.Button(btns, text="複製", command=self._copy, **b).pack(side="left")
        tk.Button(btns, text="刪除", command=self._delete, **b).pack(side="left", padx=4)
        tk.Button(btns, text="詞綴表", command=lambda: __import__("item_capture").AffixTable(self.app), **b).pack(side="left")
        wrap = tk.Frame(right, bg=C["panel"])
        wrap.pack(fill="both", expand=True, padx=6, pady=6)
        self.bag_canvas = tk.Canvas(wrap, width=230, height=330, bg=C["panel"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, command=self.bag_canvas.yview)
        self.bag_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.bag_canvas.pack(side="left", fill="both", expand=True)
        self.bag = tk.Frame(self.bag_canvas, bg=C["panel"])
        self.bag_canvas.create_window(0, 0, window=self.bag, anchor="nw")
        self.bag.bind("<Configure>", lambda e: self.bag_canvas.configure(scrollregion=self.bag_canvas.bbox("all")))
        for wdg in (wrap, self.bag_canvas, self.bag):
            wdg.bag_area = True
        self.bag_canvas.bind("<MouseWheel>", lambda e: (self.bag_canvas.yview_scroll(-e.delta // 120, "units"), "break")[1])

        self.status_var = tk.StringVar(value="把背包的裝備拖到格子穿上，拖回背包或按右鍵脫下")
        tk.Label(w, textvariable=self.status_var, bg=C["frame"], fg="white", font=(FONT, 9),
                 anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", padx=8)
        tk.Label(w, text="背包雙擊＝穿上/脫下，格子雙擊＝編輯，點空格子＝新增該部位的裝備",
                 bg=C["frame"], fg="#eef2f6", font=(FONT, 8), anchor="w").grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))

        self.refresh()

    def status(self, text):
        if self.win and self.win.winfo_exists():
            self.status_var.set(text)

    def refresh(self):
        if not (self.win and self.win.winfo_exists()):
            return
        for key, (cell, lab, pink) in self.cells.items():
            iid = self.equipped.get(key)
            if iid:
                it = self.items[iid]
                cell.configure(bg=C["worn"], highlightbackground=C["worn_border"], highlightthickness=2)
                lab.configure(text=it["name"], bg=C["worn"], fg="#222", font=(FONT, 9, "bold"))
            else:
                bg = C["pink"] if pink else C["blue"]
                cell.configure(bg=bg, highlightbackground="white", highlightthickness=1)
                lab.configure(text=SLOT_TYPE[key], bg=bg, fg=C["pink_t"] if pink else C["blue_t"],
                              font=(FONT, 10, "bold"))
        for child in self.bag.winfo_children():
            child.destroy()
        if not self.items:
            tk.Label(self.bag, text="背包是空的，按「新增裝備」\n或點左邊的空格子開始", bg=C["panel"],
                     fg="#777", font=(FONT, 9), justify="left").pack(anchor="w", padx=6, pady=8)
        order = sorted(self.items, key=lambda i: (PART_TYPES.index(self.items[i]["part"])
                                                  if self.items[i]["part"] in PART_TYPES else 99, int(i)))
        for iid in order:
            it = self.items[iid]
            worn = self.worn_slot(iid)
            bg = C["sel"] if iid == self.selected else C["worn"] if worn else "white"
            row = tk.Frame(self.bag, bg=bg, bd=1, relief="solid")
            row.pack(fill="x", padx=2, pady=1)
            sub = it["part"] + (f"・{it['category']}" if it.get("category") else "")
            l1 = tk.Label(row, text=it["name"] + ("　［穿著］" if worn else ""), bg=bg,
                          font=(FONT, 10, "bold"), anchor="w", width=24)
            l2 = tk.Label(row, text=sub, bg=bg, fg="#666", font=(FONT, 8), anchor="w")
            l1.pack(fill="x", padx=4)
            l2.pack(fill="x", padx=4)
            for wdg in (row, l1, l2):
                wdg.bag_area = True
                wdg.bind("<ButtonPress-1>", lambda e, i=iid: self._press(e, ("bag", i)))
                wdg.bind("<B1-Motion>", self._motion)
                wdg.bind("<ButtonRelease-1>", self._release)
                wdg.bind("<Double-Button-1>", lambda e, i=iid: self.auto_equip(i))
                wdg.bind("<Enter>", lambda e, i=iid: self._show_tip(e.widget, i))
                wdg.bind("<Leave>", lambda e: self._hide_tip())
                wdg.bind("<MouseWheel>", lambda e: (self.bag_canvas.yview_scroll(-e.delta // 120, "units"), "break")[1])

    # ---------- 拖放
    def _press(self, e, src):
        self._hide_tip()
        self.drag = dict(src=src, x=e.x_root, y=e.y_root, ghost=None)

    def _item_of(self, src):
        kind, key = src
        return key if kind == "bag" else self.equipped.get(key)

    def _motion(self, e):
        d = self.drag
        if not d:
            return
        iid = self._item_of(d["src"])
        if not iid:
            return
        if not d["ghost"] and abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) > 6:
            g = d["ghost"] = tk.Toplevel(self.win)
            g.overrideredirect(True)
            g.attributes("-topmost", True)
            try:
                g.attributes("-alpha", 0.85)
            except tk.TclError:
                pass
            tk.Label(g, text=self.items[iid]["name"], bg=C["worn"], font=(FONT, 10, "bold"),
                     bd=2, relief="solid", padx=8, pady=4).pack()
        if d["ghost"]:
            d["ghost"].geometry(f"+{e.x_root + 12}+{e.y_root + 8}")

    def _release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        kind, key = d["src"]
        if not d["ghost"]:                            # 沒拖動＝點一下
            if kind == "bag":
                self.selected = key
                self.refresh()
            elif key not in self.equipped:
                self.editor(None, part=SLOT_TYPE[key], slot=key)
            else:
                self.selected = self.equipped[key]
                self.refresh()
            return
        d["ghost"].destroy()
        iid = self._item_of(d["src"])
        target = self.win.winfo_containing(e.x_root, e.y_root)
        slot, in_bag = None, False
        while target is not None:
            slot = getattr(target, "slot_key", None)
            in_bag = getattr(target, "bag_area", False)
            if slot or in_bag:
                break
            target = target.master
        if slot:
            if kind == "slot" and slot == key:
                return
            self.equip(iid, slot)
        elif kind == "slot":                          # 格子上的裝備拖到背包或視窗外＝脫下
            self.unequip(key)

    # ---------- 說明框
    def _hover_slot(self, e, key):
        iid = self.equipped.get(key)
        if iid:
            self._show_tip(e.widget, iid)

    def _show_tip(self, widget, iid):
        if self.drag:
            return
        self._hide_tip()
        it = self.items[iid]
        tip = self.tip = tk.Toplevel(self.win)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        box = tk.Frame(tip, bg=C["tip"], padx=12, pady=8)
        box.pack()
        tk.Label(box, text=it["name"], bg=C["tip"], fg="white", font=(FONT, 12, "bold")).pack()
        tk.Frame(box, bg="white", height=1).pack(fill="x", pady=4)
        if it.get("category"):
            tk.Label(box, text=f"{'武器' if it['part'] == '武器' else '副手'}分類：{it['category']}",
                     bg=C["tip"], fg="white", font=(FONT, 10)).pack()
        for line in it["lines"]:
            if line.strip():
                tk.Label(box, text=line.strip(), bg=C["tip"], font=(FONT, 10),
                         fg="white" if recognized(line) else "#b8bcd8").pack()
        if it.get("affixes"):
            tk.Frame(box, bg="white", height=1).pack(fill="x", pady=4)
            tk.Label(box, text="秘藏能力", bg=C["tip"], fg="#d9a8ff", font=(FONT, 10, "bold")).pack()
            for text in it["affixes"]:
                body, tier = split_tier(text)
                tk.Label(box, text=body + (f"　{tier}" if tier else ""), bg=C["tip"], font=(FONT, 10),
                         fg="white").pack()
        tip.geometry(f"+{widget.winfo_rootx() + widget.winfo_width() + 6}+{widget.winfo_rooty()}")

    def _hide_tip(self):
        if self.tip:
            self.tip.destroy()
            self.tip = None

    # ---------- 新增 / 編輯 / 刪除
    def _edit_slot(self, key):
        if key in self.equipped:
            self.editor(self.equipped[key])

    def _copy(self):
        if self.selected:
            src = self.items[self.selected]
            it = dict(src, lines=list(src["lines"]), affixes=list(src.get("affixes", [])))
            it["name"] += "（複製）"
            self.selected = self._add(it)
            self.save()
            self.refresh()

    def _delete(self):
        if not self.selected:
            return
        it = self.items[self.selected]
        if not messagebox.askyesno("刪除裝備", f"確定要刪除「{it['name']}」？", parent=self.win):
            return
        slot = self.worn_slot(self.selected)
        if slot:
            del self.equipped[slot]
        del self.items[self.selected]
        self.selected = None
        self.changed()

    def editor(self, iid, part=None, slot=None):
        self._hide_tip()
        it = self.items.get(iid) or dict(name="", part=part or "武器", category="", lines=[], affixes=[])
        d = tk.Toplevel(self.win, bg=C["panel"], padx=12, pady=10)
        d.title("編輯裝備" if iid else "新增裝備")
        d.transient(self.win.winfo_toplevel())
        d.resizable(False, False)
        L = dict(bg=C["panel"], font=(FONT, 10))
        tk.Label(d, text="名稱", **L).grid(row=0, column=0, sticky="e")
        name = tk.StringVar(value=it["name"])
        tk.Entry(d, textvariable=name, width=24, font=(FONT, 10)).grid(row=0, column=1, sticky="w", pady=2)
        tk.Label(d, text="部位", **L).grid(row=1, column=0, sticky="e")
        part_var = tk.StringVar(value=it["part"])
        ttk.Combobox(d, textvariable=part_var, values=PART_TYPES, state="readonly", width=12).grid(
            row=1, column=1, sticky="w", pady=2)
        tk.Label(d, text="分類", **L).grid(row=2, column=0, sticky="e")
        cat_var = tk.StringVar(value=it.get("category", ""))
        cat_box = ttk.Combobox(d, textvariable=cat_var, width=12)
        cat_box.grid(row=2, column=1, sticky="w", pady=2)

        def on_part(*_):
            p = part_var.get()
            vals = WEAPON_TYPES if p == "武器" else OFFHAND_TYPES if p == "副手" else []
            cat_box.configure(values=vals, state="readonly" if vals else "disabled")
            if cat_var.get() not in vals:
                cat_var.set(vals[0] if vals else "")
        part_var.trace_add("write", on_part)
        on_part()

        tk.Label(d, text="基本屬性（照裝備說明，一行一個）", **L).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
        txt = tk.Text(d, width=38, height=7, font=(FONT, 10))
        txt.grid(row=4, column=0, columnspan=2)
        txt.insert("1.0", "\n".join(it["lines"]))
        check = tk.Label(d, text="", bg=C["panel"], font=(FONT, 9), justify="left", anchor="w")
        check.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        tk.Label(d, text="例：攻擊力：+64　攻擊基礎暴擊率%：+6　迴避率：+10%　防禦力：+6\n"
                         "　　冰冷屬性抗性：+17%　攻擊速度：快(5)（只記錄，不計算）",
                 bg=C["panel"], fg="#777", font=(FONT, 8), justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(2, 4))
        tk.Label(d, text="秘藏能力（一行一個，知道 T 級就在行尾加 T1～T10）", bg=C["panel"], fg="#7a3fb0",
                 font=(FONT, 10, "bold")).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 2))
        aff = tk.Text(d, width=38, height=7, font=(FONT, 10))
        aff.grid(row=8, column=0, columnspan=2)
        aff.insert("1.0", "\n".join(it.get("affixes", [])))
        acheck = tk.Label(d, text="", bg=C["panel"], font=(FONT, 9), justify="left", anchor="w")
        acheck.grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))
        tk.Label(d, text="例：荊棘傷害：+50% T3　武器攻擊力：+3%，命中值：+3 T8　全能力：+5\n"
                         "和基本屬性同名的（例：冰冷屬性抗性）已含在紫色數值裡，不重複計算",
                 bg=C["panel"], fg="#777", font=(FONT, 8), justify="left").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(2, 6))

        def lines():
            return [x.strip() for x in txt.get("1.0", "end").splitlines() if x.strip()]

        def affixes():
            return [x.strip() for x in aff.get("1.0", "end").splitlines() if x.strip()]

        def update_check(*_):
            ls = lines()
            ok = sum(recognized(x) for x in ls)
            check.configure(text=f"{ok} 行會算進角色資料" + (f"，{len(ls) - ok} 行只記錄" if len(ls) > ok else ""),
                            fg="#1c7a2c")
            parts = affix_parts(dict(lines=ls, affixes=affixes()))
            n = sum(bool(c and recognized(e)) for e, c, *_ in parts)
            acheck.configure(text=f"{n} 項會算進角色資料" +
                             (f"，{len(parts) - n} 項只記錄（已含在紫色數值／表上沒有這個欄位）"
                              if len(parts) > n else ""), fg="#1c7a2c")
        txt.bind("<KeyRelease>", update_check)
        aff.bind("<KeyRelease>", update_check)
        update_check()

        def save():
            if not name.get().strip():
                messagebox.showinfo("新增裝備", "請輸入名稱", parent=d)
                return
            item = dict(name=name.get().strip(), part=part_var.get(), category=cat_var.get(), lines=lines(),
                        affixes=affixes())
            nonlocal iid
            if iid:
                old_slot = self.worn_slot(iid)
                self.items[iid] = item
                if old_slot and SLOT_TYPE[old_slot] != item["part"]:
                    del self.equipped[old_slot]          # 改了部位就先脫下
            else:
                iid = self._add(item)
                if slot and SLOT_TYPE[slot] == item["part"]:
                    self.equipped[slot] = iid
            self.selected = iid
            d.destroy()
            self.changed()

        row = tk.Frame(d, bg=C["panel"])
        row.grid(row=11, column=0, columnspan=2)
        tk.Button(row, text="儲存", command=save, width=8, font=(FONT, 10)).pack(side="left", padx=4)
        tk.Button(row, text="取消", command=d.destroy, width=8, font=(FONT, 10)).pack(side="left", padx=4)
        d.update_idletasks()
        d.geometry(f"+{self.win.winfo_rootx() + 40}+{self.win.winfo_rooty() + 40}")
        d.grab_set()

    # ---------- 詞綴紀錄（官方沒有詞綴表，從自己的裝備累積）
    def affix_table(self):
        rows = {}
        for it in self.items.values():
            for e, counted, body, tier in affix_parts(it):
                m = STAT_RE.match(e)
                if not m:
                    continue
                key = (m.group(1), m.group(3))
                rows.setdefault(key, []).append((float(m.group(2)), tier, it["name"], it["part"]))
        w = tk.Toplevel(self.win)
        w.title("詞綴紀錄")
        w.transient(self.win.winfo_toplevel())
        tk.Label(w, text="從背包裡所有裝備的秘藏能力整理。每一種詞綴列出各 T 級看過的數值範圍，"
                         "裝備越多越準。T10 最差、T1 最好；沒標 T 級的歸在「未標」。",
                 font=(FONT, 9), wraplength=640, justify="left").pack(anchor="w", padx=8, pady=6)
        cols = ("tier", "range", "count", "items")
        tv = ttk.Treeview(w, columns=cols, height=18)
        tv.heading("#0", text="詞綴")
        for c, t, wd in zip(cols, ("T 級", "數值範圍", "筆數", "出現在"), (60, 130, 50, 260)):
            tv.heading(c, text=t)
            tv.column(c, width=wd, anchor="w")
        tv.column("#0", width=170)
        tv.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        tier_no = lambda t: int(t[1:]) if t else 99
        for (name, pct), vals in sorted(rows.items()):
            lo, hi = min(v for v, *_ in vals), max(v for v, *_ in vals)
            parent = tv.insert("", "end", text=f"{name}{'（%）' if pct else ''}", open=True,
                               values=("全部", f"{lo:+g}{pct} ~ {hi:+g}{pct}", len(vals), ""))
            by_tier = {}
            for v, tier, iname, part in vals:
                by_tier.setdefault(tier, []).append((v, iname, part))
            for tier in sorted(by_tier, key=tier_no):
                vs = by_tier[tier]
                a, b = min(v for v, *_ in vs), max(v for v, *_ in vs)
                rng = f"{a:+g}{pct}" if a == b else f"{a:+g}{pct} ~ {b:+g}{pct}"
                where = "、".join(dict.fromkeys(f"{n}（{p}）" for _, n, p in vs))
                tv.insert(parent, "end", text="", values=(tier or "未標", rng, len(vs), where))
        if not rows:
            tv.insert("", "end", text="還沒有任何秘藏能力紀錄")
