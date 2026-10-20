#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证 tiku_gui 的启动/停止/重启按钮，真实拉起并杀进程。

不弹窗、不进 mainloop：直接构造 App，调用它的方法，等异步线程收尾。
"""
import json
import sys
import time
import tkinter as tk
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiku_gui as g

fails = []
PORT = g.load_conf().get("port", 8890)


def health():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1.5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def pump():
    """驱动 Tk 事件循环：_tick 里的队列只在主循环中才会被清空"""
    try:
        root.update()
    except tk.TclError:
        pass


def wait_until(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        pump()
        if fn():
            return True
        time.sleep(0.1)
    pump()
    return False


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


# 屏蔽 messagebox，避免测试中途弹窗卡住
g.messagebox.showinfo = lambda *a, **k: None
g.messagebox.showerror = lambda *a, **k: print("    [error]", a)
g.messagebox.showwarning = lambda *a, **k: print("    [warn]", a)
g.messagebox.askyesno = lambda *a, **k: True

root = tk.Tk()
root.withdraw()
app = g.App(root)

print("== 初始状态 ==")
h = health()
print(f"  服务当前: {'运行中' if h else '已停止'}  {h}")

# 确保从停止态开始
if h:
    print("  先停止已有实例…")
    app.stop()
    wait_until(lambda: not app.busy, 15)
    wait_until(lambda: health() is None, 15)

print("\n== 1. 启动 ==")
app.start()
ok = wait_until(lambda: not app.busy, 20) and wait_until(lambda: health() is not None, 10)
h = health()
check("启动后服务可访问", ok and h is not None, str(h))
check("题库条数可读", h is not None and "bank" in h, f"bank={h.get('bank') if h else None}")

app.refresh()
check("刷新后徽标=运行中", "运行中" in app.badge.cget("text"), app.badge.cget("text"))
check("启动按钮已禁用", str(app.btn_start["state"]) == "disabled")
check("停止按钮已启用", str(app.btn_stop["state"]) == "normal")
pid_from_ui = app.lbl_pid.cget("text")
check("界面显示 PID", pid_from_ui.isdigit(), pid_from_ui)

print("\n== 2. 重复启动应被拦截 ==")
before = health()
app.start()
wait_until(lambda: False, 1.5)
check("重复启动不破坏服务", health() is not None)

print("\n== 3. 停止 ==")
app.stop()
stopped = wait_until(lambda: not app.busy, 20) and wait_until(lambda: health() is None, 15)
check("停止后服务不可访问", stopped and health() is None)
app.refresh()
check("刷新后徽标=已停止", "已停止" in app.badge.cget("text"), app.badge.cget("text"))
check("启动按钮恢复可用", str(app.btn_start["state"]) == "normal", app.btn_start["state"])
check("端口已释放", g.listener_pid(PORT) is None, str(g.listener_pid(PORT)))

print("\n== 4. 重启 ==")
app.start()
wait_until(lambda: not app.busy, 20)
wait_until(lambda: health() is not None, 10)
pid1 = g.listener_pid(PORT)
check("重启前已运行", pid1 is not None, f"pid={pid1}")

app.restart()
wait_until(lambda: False, 0.8)
ok = wait_until(lambda: health() is not None, 30)
pid2 = g.listener_pid(PORT)
check("重启后服务可用", ok and health() is not None)
check("PID 已更换（真的重启了）", pid2 is not None and pid2 != pid1, f"{pid1} -> {pid2}")

print("\n== 5. 接管外部实例 ==")
app.stop()
wait_until(lambda: health() is None, 15)
import subprocess
ext = subprocess.Popen([sys.executable, "tiku_server.py"], cwd=str(g.BASE),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=g.CREATE_NO_WINDOW)
wait_until(lambda: health() is not None, 15)
check("外部实例已起来", health() is not None, f"pid={ext.pid}")

app.proc = None
app.adopted_pid = None
app.refresh()
check("界面识别为外部启动", app.lbl_pid.cget("text") in ("外部启动", str(ext.pid)),
      app.lbl_pid.cget("text"))

app.stop()
killed = wait_until(lambda: health() is None, 20)
check("能停止非本面板启动的实例", killed and health() is None)

# 收尾
if ext.poll() is None:
    ext.kill()

root.destroy()
print("\n" + "=" * 52)
print("全部通过" if not fails else f"失败 {len(fails)} 项: {fails}")
sys.exit(0 if not fails else 1)
