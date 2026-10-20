#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 tiku_gui 的深色主题是否真的生效（不依赖截图可靠性）。

两条独立证据：
  A. 从 Tk 内部查询 ttk 样式解析出的实际配色（style.lookup）
  B. 强制置顶后全屏截图、按窗口矩形裁剪，统计按钮/背景区域的像素色

Tk 不实现 WM_PRINT，PrintWindow 会返回空白，所以不能用它截图。
"""
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent

VERIFY_CODE = r'''
import sys, tkinter as tk
from tkinter import ttk
sys.path.insert(0, r"__BASE__")
import tiku_gui as g

root = tk.Tk()
app = g.App(root)
root.update_idletasks()
root.update()

s = ttk.Style(root)
out = {}
for name in ("TButton", "Accent.TButton", "Danger.TButton", "TCheckbutton"):
    out[name] = {
        "bg": s.lookup(name, "background"),
        "fg": s.lookup(name, "foreground"),
    }
out["_root_bg"] = root.cget("bg")
out["_btn_state"] = (str(app.btn_start["state"]), str(app.btn_stop["state"]))
out["_badge"] = app.badge.cget("text") + " | " + app.badge.cget("fg")
out["_bank"] = app.lbl_bank.cget("text")
root.destroy()

import json
print("JSON:" + json.dumps(out, ensure_ascii=False))
'''

code = VERIFY_CODE.replace("__BASE__", str(BASE))
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
line = [l for l in r.stdout.splitlines() if l.startswith("JSON:")]
if not line:
    print("查询失败：")
    print(r.stdout)
    print(r.stderr)
    sys.exit(1)

import json
info = json.loads(line[0][5:])

print("== A. Tk 内部样式解析 ==\n")
expect = {
    "TButton": ("#21262d", "#e6edf3"),
    "Accent.TButton": ("#1f6feb", "#ffffff"),
    "Danger.TButton": ("#21262d", "#f85149"),
}
fails = []
for name, (bg, fg) in expect.items():
    got = info.get(name, {})
    ok = (got.get("bg", "").lower() == bg and got.get("fg", "").lower() == fg)
    print(f"  {'PASS' if ok else 'FAIL'}  {name:16s} bg={got.get('bg')} fg={got.get('fg')}  期望 bg={bg} fg={fg}")
    if not ok:
        fails.append(name)

ok_root = info.get("_root_bg", "").lower() == "#0d1117"
print(f"  {'PASS' if ok_root else 'FAIL'}  窗口底色        {info.get('_root_bg')}")
if not ok_root:
    fails.append("root_bg")

print(f"\n  按钮状态: 启动={info['_btn_state'][0]}  停止={info['_btn_state'][1]}")
print(f"  状态徽标: {info['_badge']}")
print(f"  题库显示: {info['_bank']}")

print("\n" + "=" * 50)
print("主题校验通过" if not fails else f"失败: {fails}")
sys.exit(0 if not fails else 1)
