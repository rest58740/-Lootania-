"""裝備截圖工具：滑鼠停在遊戲裡的裝備上按快捷鍵，只截下裝備說明框，自動用裝備名稱命名存檔

- 截圖內容：一般畫面／Tab 詳細資訊／兩張並排／兩張分開存
- 檔名格式可自訂：{名稱} {分類} {等級} {日期} {時間}
- 可以依裝備分類建立子資料夾（帽子、戒指、弓弩…）
- 設定存在 screenshot_settings.json
"""
import datetime
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# 打包成 exe 時 __file__ 在暫存資料夾裡：設定檔改放在 exe 旁邊
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE
os.chdir(APP_DIR)

import cv2                      # noqa: E402
import keyboard                 # noqa: E402
import numpy as np              # noqa: E402
import win32api                 # noqa: E402
from PIL import Image, ImageTk  # noqa: E402

import item_capture as ic       # noqa: E402

SETTINGS_FILE = os.path.join(APP_DIR, "screenshot_settings.json")


def desktop_dir():
    """桌面的實際位置（有 OneDrive 時桌面會被搬到 OneDrive 底下）"""
    try:
        from win32com.shell import shell, shellcon
        return shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")
FONT = "Microsoft JhengHei"
MODES = [("normal", "一般畫面"), ("detail", "Tab 詳細資訊"), ("both", "兩張並排（一張圖）"), ("split", "兩張分開存")]
OLD_DEFAULT_FOLDER = os.path.join(HERE, "裝備截圖")
DEFAULTS = dict(folder=os.path.join(desktop_dir(), "裝備截圖"), hotkey="f8", mode="both",
                pattern="{名稱}_{日期}_{時間}", by_category=True, preview=True)
C = ic.THEMES["light"]


def safe_name(text):
    """Windows 檔名不能有的字元換掉"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", text).strip(" .")
    return text or "裝備"


class Shooter:
    def __init__(self, root):
        self.root = root
        self.s = dict(DEFAULTS, **ic.load_json(SETTINGS_FILE, {}))
        if os.path.normcase(self.s["folder"]) == os.path.normcase(OLD_DEFAULT_FOLDER):
            self.s["folder"] = DEFAULTS["folder"]          # 舊的預設位置 → 改成桌面
        self.q = queue.Queue()
        self.busy = False
        self.hk = None
        self.recent = []                     # [檔案路徑]
        self._photo = None
        self.build()
        self.register()
        root.after(150, self.poll)

    # ---------- 介面
    def build(self):
        r = self.root
        r.title("裝備截圖")
        r.configure(bg=C["frame"])
        r.geometry("700x600")
        head = tk.Frame(r, bg=C["title"])
        head.pack(fill="x", padx=6, pady=(6, 0))
        tk.Label(head, text="裝備截圖", bg=C["title"], fg="white", font=(FONT, 13, "bold"), padx=10, pady=4).pack(side="left")
        self.key_lbl = tk.Label(head, bg=C["title"], fg=C["headtext"], font=(FONT, 10))
        self.key_lbl.pack(side="left")

        body = tk.Frame(r, bg=C["frame"], padx=10, pady=8)
        body.pack(fill="x")
        L = dict(bg=C["frame"], fg=C["text"], font=(FONT, 10, "bold"))

        tk.Label(body, text="儲存位置", **L).grid(row=0, column=0, sticky="e", pady=3)
        self.folder = tk.StringVar(value=self.s["folder"])
        tk.Entry(body, textvariable=self.folder, width=44, font=(FONT, 9)).grid(row=0, column=1, sticky="we", padx=4)
        bf = tk.Frame(body, bg=C["frame"])
        bf.grid(row=0, column=2, sticky="w")
        ic.maple_button(bf, "選擇…", self.pick_folder).pack(side="left", padx=2)
        ic.maple_button(bf, "開啟", self.open_folder).pack(side="left", padx=2)

        tk.Label(body, text="快捷鍵", **L).grid(row=1, column=0, sticky="e", pady=3)
        kc = tk.Frame(body, bg=C["frame"])
        kc.grid(row=1, column=1, columnspan=2, sticky="w", padx=4)
        kf = tk.Frame(kc, bg=C["frame"])
        kf.pack(anchor="w")
        self.key_var = tk.StringVar(value=self.s["hotkey"])
        ke = tk.Entry(kf, textvariable=self.key_var, width=16, font=(FONT, 10, "bold"), justify="center")
        ke.pack(side="left")
        ke.bind("<Return>", lambda _: self.apply_hotkey(self.key_var.get()))
        ic.maple_button(kf, "套用", lambda: self.apply_hotkey(self.key_var.get())).pack(side="left", padx=(4, 2))
        self.key_btn = ic.maple_button(kf, "錄製…", self.set_hotkey)
        self.key_btn.pack(side="left", padx=2)
        tk.Label(kc, text="直接輸入後按套用（例：f8、ctrl+shift+s、alt+q），或按「錄製…」再按鍵盤；"
                          "不要用遊戲會用到的鍵", bg=C["frame"], fg=C["subtext"], font=(FONT, 9)).pack(anchor="w")

        tk.Label(body, text="截圖內容", **L).grid(row=2, column=0, sticky="ne", pady=3)
        mf = tk.Frame(body, bg=C["frame"])
        mf.grid(row=2, column=1, columnspan=2, sticky="w", padx=4)
        self.mode = tk.StringVar(value=self.s["mode"])
        for i, (k, t) in enumerate(MODES):
            tk.Radiobutton(mf, text=t, value=k, variable=self.mode, bg=C["frame"], fg=C["text"], font=(FONT, 10),
                           activebackground=C["frame"], command=self.save).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 16))

        tk.Label(body, text="檔名格式", **L).grid(row=3, column=0, sticky="e", pady=3)
        self.pattern = tk.StringVar(value=self.s["pattern"])
        tk.Entry(body, textvariable=self.pattern, width=30, font=(FONT, 10)).grid(row=3, column=1, sticky="w", padx=4)
        tk.Label(body, text="可用：{名稱} {分類} {等級} {日期} {時間}", bg=C["frame"], fg=C["subtext"],
                 font=(FONT, 9)).grid(row=4, column=1, columnspan=2, sticky="w", padx=4)
        self.by_cat = tk.BooleanVar(value=self.s["by_category"])
        tk.Checkbutton(body, text="依裝備分類放進子資料夾（例：裝備截圖\\戒指\\紅寶石戒指_….png）", variable=self.by_cat,
                       bg=C["frame"], fg=C["text"], font=(FONT, 10), activebackground=C["frame"],
                       command=self.save).grid(row=5, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.ask_first = tk.BooleanVar(value=self.s["preview"])
        tk.Checkbutton(body, text="儲存前先預覽（可以改檔名、選擇要不要存）", variable=self.ask_first,
                       bg=C["frame"], fg=C["text"], font=(FONT, 10), activebackground=C["frame"],
                       command=self.save).grid(row=6, column=1, columnspan=2, sticky="w")
        body.columnconfigure(1, weight=1)
        for v in (self.folder, self.pattern):
            v.trace_add("write", lambda *_: self.save())

        self.status = tk.Label(r, text="滑鼠停在遊戲裡的裝備上，等說明框出現再按快捷鍵", bg=C["bar"], fg="white",
                               font=(FONT, 10, "bold"), anchor="w", padx=8, pady=4)
        self.status.pack(fill="x", padx=6)

        low = tk.Frame(r, bg=C["frame"])
        low.pack(fill="both", expand=True, padx=6, pady=6)
        lf = tk.Frame(low, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
        lf.pack(side="left", fill="both", expand=True)
        tk.Label(lf, text="最近的截圖（雙擊開啟）", bg=C["bar"], fg="white", font=(FONT, 9, "bold")).pack(fill="x")
        self.lst = tk.Listbox(lf, font=(FONT, 9), bd=0, highlightthickness=0, activestyle="none",
                              selectbackground=C["sel"], selectforeground=C["seltext"])
        self.lst.pack(fill="both", expand=True)
        self.lst.bind("<Double-Button-1>", self.open_selected)
        self.lst.bind("<<ListboxSelect>>", lambda _: self.show_preview(self.selected()))
        pf = tk.Frame(low, bg=C["panel"], width=250, highlightthickness=1, highlightbackground=C["border"])
        pf.pack(side="left", fill="y", padx=(6, 0))
        pf.pack_propagate(False)
        tk.Label(pf, text="預覽", bg=C["bar"], fg="white", font=(FONT, 9, "bold")).pack(fill="x")
        self.preview = tk.Label(pf, bg=C["panel"])
        self.preview.pack(fill="both", expand=True)
        self.update_key()

    def update_key(self):
        k = self.s["hotkey"]
        self.key_var.set(k)
        self.key_btn.configure(text="錄製…")
        self.key_lbl.configure(text=f"快捷鍵：{k.upper()}")

    def apply_hotkey(self, text):
        """檢查快捷鍵寫法，可以用就換上去"""
        hk = re.sub(r"\s*\+\s*", "+", (text or "").strip().lower())
        if not hk:
            self.update_key()
            return
        try:
            keyboard.parse_hotkey(hk)
        except Exception:
            self.set_status(f"看不懂「{text}」：請用像 f8、ctrl+shift+s 這樣的寫法", bad=True)
            self.update_key()
            return
        if "+" not in hk and hk in ic.GAME_KEYS:
            self.set_status(f"「{hk}」是遊戲會用到的鍵，請換一個（可以加 ctrl／alt 組合）", bad=True)
            self.update_key()
            return
        old = self.s["hotkey"]
        self.s["hotkey"] = hk
        if self.register():
            self.save()
            self.update_key()
            self.set_status(f"快捷鍵改成 {hk.upper()}")
        else:
            self.s["hotkey"] = old                          # 換不上去：還原
            self.register()
            self.update_key()

    def save(self):
        self.s.update(folder=self.folder.get().strip(), mode=self.mode.get(), pattern=self.pattern.get(),
                      by_category=self.by_cat.get(), preview=self.ask_first.get())
        ic.save_json(SETTINGS_FILE, self.s)

    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or HERE, parent=self.root)
        if d:
            self.folder.set(os.path.normpath(d))

    def open_folder(self):
        d = self.folder.get()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def selected(self):
        sel = self.lst.curselection()
        return self.recent[sel[0]] if sel else None

    def open_selected(self, _):
        f = self.selected()
        if f and os.path.exists(f):
            os.startfile(f)

    def show_preview(self, path):
        if not path or not os.path.exists(path):
            return
        im = Image.open(path)
        k = min(240 / im.width, 380 / im.height, 1.0)
        self._photo = ImageTk.PhotoImage(im.resize((max(1, int(im.width * k)), max(1, int(im.height * k)))))
        self.preview.configure(image=self._photo)

    # ---------- 快捷鍵
    def register(self):
        if self.hk is not None:
            try:
                keyboard.remove_hotkey(self.hk)
            except (KeyError, ValueError):
                pass
        try:
            self.hk = keyboard.add_hotkey(self.s["hotkey"], self.trigger)
            return True
        except Exception as e:
            self.hk = None
            self.set_status(f"快捷鍵無法使用：{e}", bad=True)
            return False

    def set_hotkey(self):
        self.key_btn.configure(text="請按鍵…")
        self.set_status("請在鍵盤上按想用的快捷鍵")

        def worker():
            hk = keyboard.read_hotkey(suppress=False)
            self.q.put(("hotkey", hk))
        threading.Thread(target=worker, daemon=True).start()

    def trigger(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            self.q.put(("done", self.capture()))
        except Exception as e:
            self.q.put(("error", f"截圖失敗：{e}"))

    # ---------- 截圖
    def capture(self):
        keys = [k.strip() for k in self.s["hotkey"].split("+") if k.strip()]
        for _ in range(40):                                    # 等快捷鍵放開
            if not any(keyboard.is_pressed(k) for k in keys):
                break
            time.sleep(0.03)
        cx, cy = win32api.GetCursorPos()
        mode = self.s["mode"]
        img1, org = ic.grab_around(cx, cy)
        cur = (cx - org[0], cy - org[1])
        img2 = None
        if mode != "normal":                                   # 按住 Tab 看詳細資訊
            win32api.keybd_event(0x09, 0, 0, 0)
            try:
                time.sleep(0.5)
                img2, _ = ic.grab_around(cx, cy)
            finally:
                win32api.keybd_event(0x09, 0, 2, 0)
        # 背景：滑鼠暫時移到螢幕角落讓說明框消失，拍一張同範圍的畫面，再移回來
        mon = ic.monitor_at(cx, cy)
        win32api.SetCursorPos((mon["left"] + 3, mon["top"] + 3))
        try:
            time.sleep(0.35)
            base, _ = ic.grab_around(cx, cy)
        finally:
            win32api.SetCursorPos((cx, cy))

        def locate(img):
            """先用藍色找大概位置，再和背景比對找出完整的說明框（下半部疊在彩色東西上也不會被切掉）"""
            blue = ic.find_box(img, cur)
            blue = ic.extend_top(img, blue) if blue else None
            return ic.diff_box(img, base, cur, near=blue) or blue
        box1 = locate(img1)
        if not box1:
            raise RuntimeError("沒有找到裝備說明框：滑鼠要停在裝備上、等說明框出現再按")
        box2 = locate(img2) if img2 is not None else None
        # 兩張的說明框在同一個位置：上下取比較大的範圍，兩張切一樣大
        if box2:
            x, w = box1[0], box1[2]
            top = min(box1[1], box2[1])
            bottom = max(box1[1] + box1[3], box2[1] + box2[3])
            box1 = box2 = (x, top, w, bottom - top)
        crop = lambda img, b: img[b[1]:b[1] + b[3], b[0]:b[0] + b[2]]
        normal = crop(img1, box1)
        detail = crop(img2, box2) if box2 else None
        # 名稱、分類、等級：OCR 一般畫面（讀不到再用詳細資訊）
        # OCR 前先扣掉半透明說明框後面的背景，名稱比較不會讀錯
        def read(img, box):
            try:
                return ic.read_tooltip(ic.clean_tooltip(img, base, box), force_box=box)[0] or {}
            except Exception:           # 這台電腦沒有 Windows 中文 OCR：名稱讀不到，截圖照存
                return {}
        info = read(img1, box1)
        if (not info.get("name") or not info.get("category")) and img2 is not None and box2:
            info2 = read(img2, box2)
            info = {k: info.get(k) or info2.get(k) for k in ("name", "category", "ilvl")}
        return dict(normal=normal, detail=detail, name=info.get("name") or "裝備",
                    category=info.get("category") or "", ilvl=info.get("ilvl") or "", cursor=(cx, cy))

    def compose(self, res):
        """依截圖內容設定組出要存的圖：[(檔名後綴, 圖)]"""
        mode = self.s["mode"]
        n, d = res["normal"], res["detail"]
        if mode == "detail" and d is not None:
            return [("", d)]
        if mode == "both" and d is not None:
            gap = np.full((n.shape[0], 8, 3), 255, np.uint8)
            return [("", np.hstack([n, gap, d]))]
        if mode == "split" and d is not None:
            return [("_一般", n), ("_詳細", d)]
        return [("", n)]

    def names(self, res):
        """（檔名, 分類資料夾）"""
        now = datetime.datetime.now()
        fields = {"名稱": res["name"], "分類": res["category"] or "未分類", "等級": res["ilvl"] or "",
                  "日期": now.strftime("%Y%m%d"), "時間": now.strftime("%H%M%S")}
        base = self.s["pattern"] or DEFAULTS["pattern"]
        for k, v in fields.items():
            base = base.replace("{" + k + "}", v)
        return safe_name(base), (safe_name(fields["分類"]) if self.s["by_category"] else "")

    def write(self, res, base=None, sub=None):
        if base is None:
            base, sub = self.names(res)
        folder = self.s["folder"] or DEFAULTS["folder"]
        if sub:
            folder = os.path.join(folder, safe_name(sub))
        os.makedirs(folder, exist_ok=True)
        outs = self.compose(res)
        saved = []
        for suffix, img in outs:
            path = os.path.join(folder, base + suffix + ".png")
            i = 2
            while os.path.exists(path):                           # 同名就加編號
                path = os.path.join(folder, f"{base}{suffix}_{i}.png")
                i += 1
            cv2.imencode(".png", img)[1].tofile(path)             # 路徑有中文：用 tofile
            saved.append(path)
        return saved

    # ---------- 結果
    def poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "hotkey":
                    self.apply_hotkey(data)
                    continue
                self.busy = False
                if kind == "error":
                    self.set_status(data, bad=True)
                    self.toast(data, win32api.GetCursorPos(), ok=False)
                    continue
                if self.s.get("preview"):
                    self.busy = True                          # 預覽視窗關掉之前不再截圖
                    self.ask(data)
                    continue
                self.store(data)
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def store(self, data, base=None, sub=None):
        try:
            files = self.write(data, base, sub)
        except Exception as e:
            self.set_status(f"存檔失敗：{e}", bad=True)
            return
        for f in files:
            self.recent.insert(0, f)
            self.lst.insert(0, os.path.relpath(f, self.s["folder"]) if f.startswith(self.s["folder"]) else f)
        del self.recent[50:]
        self.lst.delete(50, "end")
        self.show_preview(files[0])
        msg = f"已儲存：{os.path.basename(files[0])}" + (f" 等 {len(files)} 張" if len(files) > 1 else "")
        self.set_status(msg)
        self.toast(msg, data["cursor"], ok=True)

    def ask(self, data):
        """預覽視窗：看截圖、改檔名／資料夾，再決定要不要存（Enter＝儲存、Esc＝不要）"""
        base, sub = self.names(data)
        d = tk.Toplevel(self.root, bg=C["frame"])
        d.title("預覽截圖")
        d.attributes("-topmost", True)
        head = tk.Frame(d, bg=C["title"])
        head.pack(fill="x")
        tk.Label(head, text="要儲存這張截圖嗎？", bg=C["title"], fg="white", font=(FONT, 12, "bold"),
                 padx=10, pady=4).pack(side="left")
        # 圖：放得下螢幕就原尺寸，太大就縮小
        imgs = self.compose(data)
        pics = tk.Frame(d, bg=C["frame"])
        pics.pack(padx=10, pady=8)
        mon = ic.monitor_at(*data["cursor"])
        max_w, max_h = mon["width"] * 0.8 / len(imgs), mon["height"] * 0.6
        d._photos = []
        for _, im in imgs:
            k = min(1.0, max_w / im.shape[1], max_h / im.shape[0])
            pil = Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
            pil = pil.resize((max(1, int(pil.width * k)), max(1, int(pil.height * k))))
            ph = ImageTk.PhotoImage(pil)
            d._photos.append(ph)
            tk.Label(pics, image=ph, bg=C["frame"], bd=1, relief="solid").pack(side="left", padx=3)
        form = tk.Frame(d, bg=C["frame"], padx=10)
        form.pack(fill="x")
        L = dict(bg=C["frame"], fg=C["text"], font=(FONT, 10, "bold"))
        tk.Label(form, text="檔名", **L).grid(row=0, column=0, sticky="e", pady=2)
        name_v = tk.StringVar(value=base)
        ent = tk.Entry(form, textvariable=name_v, width=40, font=(FONT, 10))
        ent.grid(row=0, column=1, sticky="w", padx=4)
        tk.Label(form, text=".png", bg=C["frame"], fg=C["subtext"], font=(FONT, 10)).grid(row=0, column=2, sticky="w")
        tk.Label(form, text="資料夾", **L).grid(row=1, column=0, sticky="e", pady=2)
        sub_v = tk.StringVar(value=sub)
        cats = sorted({c for *_, c in ic.category_leaves()} | {"未分類"})
        ttk.Combobox(form, textvariable=sub_v, values=[""] + cats, width=16, font=(FONT, 10)).grid(
            row=1, column=1, sticky="w", padx=4)
        tk.Label(form, text=f"（在 {self.s['folder']} 底下；空白＝直接放在這裡）", bg=C["frame"], fg=C["subtext"],
                 font=(FONT, 9)).grid(row=2, column=1, columnspan=2, sticky="w", padx=4)

        def close():
            self.busy = False
            d.destroy()

        def ok(_=None):
            n = safe_name(name_v.get())
            close()
            self.store(data, n, sub_v.get().strip())

        def no(_=None):
            close()
            self.set_status("沒有儲存這張截圖")
        bar = tk.Frame(d, bg=C["frame"], pady=8)
        bar.pack()
        ic.maple_button(bar, "儲存（Enter）", ok).pack(side="left", padx=6)
        tk.Button(bar, text="不要（Esc）", command=no, bg=C["bar"], fg="white", activebackground=C["title"],
                  activeforeground="white", font=(FONT, 10, "bold"), relief="flat", bd=0, padx=12, pady=3,
                  cursor="hand2").pack(side="left", padx=6)
        d.bind("<Return>", ok)
        d.bind("<Escape>", no)
        d.protocol("WM_DELETE_WINDOW", no)
        # 放在滑鼠所在的螢幕中間，搶到鍵盤焦點才能直接按 Enter／Esc
        d.update_idletasks()
        x = mon["left"] + (mon["width"] - d.winfo_width()) // 2
        y = mon["top"] + (mon["height"] - d.winfo_height()) // 2
        d.geometry(f"+{max(mon['left'], x)}+{max(mon['top'], y)}")
        d.lift()
        d.focus_force()
        ent.focus_set()
        ent.icursor("end")

    def set_status(self, text, bad=False):
        self.status.configure(text=text, bg="#b03a2e" if bad else C["bar"])

    def toast(self, text, cursor, ok=True):
        """遊戲在前景時看不到這個視窗：在滑鼠旁邊跳一個小提示"""
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        tk.Label(t, text=("✔ " if ok else "✖ ") + text, bg="#1c7a2c" if ok else "#b03a2e", fg="white",
                 font=(FONT, 10, "bold"), padx=10, pady=5).pack()
        t.update_idletasks()
        x, y = cursor
        t.geometry(f"+{x + 20}+{max(0, y - 50)}")
        t.after(1800, t.destroy)


def main():
    root = tk.Tk()
    Shooter(root)
    root.mainloop()


if __name__ == "__main__":
    main()
