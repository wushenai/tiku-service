#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习通题库服务 · 启动/关闭控制面板

双击 tiku_gui.vbs（无黑窗口）或 python tiku_gui.py 打开本面板。

功能：
  - 启动 / 停止 / 重启 题库服务（127.0.0.1:8890）
  - 实时显示运行状态、PID、题库条数
  - 一键打开网页控制台、复制 OCS 题库配置
  - 查看运行日志（尾部）
  - 开机自启开关

设计要点：
  - 服务进程以 CREATE_NO_WINDOW 启动，不会弹出黑窗口
  - 面板关闭时**不会**默认杀掉服务（避免误关），可在设置里改为随面板退出
  - 已在外部启动的服务能被自动"接管"（按端口反查 PID），不用先手动关
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

IS_FROZEN = getattr(sys, "frozen", False)

BASE = Path(__file__).resolve().parent
if IS_FROZEN:
    # 打包后 __file__ 在 _MEIPASS 临时目录，数据必须落在 exe 旁边
    BASE = Path(sys.executable).resolve().parent
SERVER_SCRIPT = BASE / "tiku_server.py"
CONF_FILE = BASE / "config.json"
OUT_LOG = BASE / "run.out.log"
ERR_LOG = BASE / "run.err.log"

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

# 配色（深色，和网页控制台一致）
BG = "#0d1117"
CARD = "#161b22"
LINE = "#30363d"
FG = "#e6edf3"
DIM = "#8b98a5"
ACC = "#1f6feb"
OK = "#3fb950"
WARN = "#d29922"
ERR = "#f85149"


def load_conf():
    conf = {"port": 8890, "host": "127.0.0.1"}
    if CONF_FILE.exists():
        try:
            conf.update(json.loads(CONF_FILE.read_text(encoding="utf-8-sig")))
        except Exception:
            pass
    return conf


def listener_pid(port):
    """按端口反查监听进程 PID（用于接管外部启动的实例 / 兜底关闭）"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def kill_pid(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


class App:
    def __init__(self, root):
        self.root = root
        self.conf = load_conf()
        self.port = int(self.conf.get("port", 8890))
        self.proc = None          # 本面板启动的子进程
        self.adopted_pid = None   # 接管的外部进程
        self.busy = False
        self._pending_start = False
        # 后台线程不能直接碰 Tk（会报 "main thread is not in main loop"），
        # 一律把回调丢进队列，由主线程的 _tick 取出执行。
        self._ui_queue = queue.Queue()

        self._build_ui()
        self.refresh()
        self._tick()
        # 打开面板即自动拉起服务（可在面板上取消勾选）
        self.root.after(600, self._auto_start)

    def _auto_start(self):
        if self.var_autostart_svc.get() and not self.busy and self.health() is None:
            self.start()

    # ---------------- UI ----------------
    def _build_ui(self):
        self.root.title("学习通题库服务")
        self.root.configure(bg=BG)
        self.root.geometry("680x620")
        self.root.minsize(640, 560)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 9))
        style.configure("Dim.TLabel", background=BG, foreground=DIM, font=("Microsoft YaHei UI", 9))
        style.configure("H1.TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("TCheckbutton", background=BG, foreground=DIM, font=("Microsoft YaHei UI", 9))
        style.map("TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("active", FG)])

        # 深色按钮：clam 主题默认是浅灰底，必须逐态覆盖，否则和背景撞色
        style.configure(
            "TButton",
            background="#21262d",
            foreground=FG,
            bordercolor=LINE,
            darkcolor="#21262d",
            lightcolor="#21262d",
            focuscolor=ACC,
            relief="flat",
            font=("Microsoft YaHei UI", 9),
            padding=(10, 7),
        )
        style.map(
            "TButton",
            background=[("disabled", "#161b22"), ("pressed", "#30363d"), ("active", "#30363d")],
            foreground=[("disabled", "#484f58"), ("active", "#ffffff")],
            bordercolor=[("active", "#484f58")],
        )
        # 主按钮（启动）用蓝色强调
        style.configure(
            "Accent.TButton",
            background=ACC,
            foreground="#ffffff",
            bordercolor=ACC,
            darkcolor=ACC,
            lightcolor=ACC,
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", "#1a2b42"), ("pressed", "#1a5fd0"), ("active", "#388bfd")],
            foreground=[("disabled", "#5a7ba6")],
            bordercolor=[("active", "#388bfd")],
        )
        # 危险按钮（停止）用红色描边
        style.configure(
            "Danger.TButton",
            background="#21262d",
            foreground="#f85149",
            bordercolor="#5c2c2c",
            darkcolor="#21262d",
            lightcolor="#21262d",
        )
        style.map(
            "Danger.TButton",
            background=[("disabled", "#161b22"), ("active", "#2d1a1a")],
            foreground=[("disabled", "#4a2a2a"), ("active", "#ff6b6b")],
        )

        # 顶部
        head = ttk.Frame(self.root, padding=(16, 14, 16, 6))
        head.pack(fill="x")
        ttk.Label(head, text="学习通题库服务", style="H1.TLabel").pack(side="left")
        self.badge = tk.Label(
            head, text="● 检测中…", bg=BG, fg=DIM, font=("Microsoft YaHei UI", 10, "bold")
        )
        self.badge.pack(side="right")

        # 状态卡片
        card = tk.Frame(self.root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", padx=16, pady=(6, 10))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)

        self.lbl_addr = self._kv(inner, "地址", f"http://127.0.0.1:{self.port}")
        self.lbl_bank = self._kv(inner, "题库", "—")
        self.lbl_pid = self._kv(inner, "进程", "—")
        self.lbl_src = self._kv(inner, "接入", "OCS → 题库配置")

        # 按钮
        bar = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        bar.pack(fill="x")
        self.btn_start = ttk.Button(bar, text="▶  启动", command=self.start, width=11,
                                    style="Accent.TButton")
        self.btn_stop = ttk.Button(bar, text="■  停止", command=self.stop, width=11,
                                   style="Danger.TButton")
        self.btn_restart = ttk.Button(bar, text="↻  重启", command=self.restart, width=11)
        for b in (self.btn_start, self.btn_stop, self.btn_restart):
            b.pack(side="left", padx=(0, 8))

        bar2 = ttk.Frame(self.root, padding=(16, 0, 16, 10))
        bar2.pack(fill="x")
        ttk.Button(bar2, text="打开控制台", command=self.open_console, width=13).pack(side="left", padx=(0, 8))
        ttk.Button(bar2, text="复制 OCS 配置", command=self.copy_ocs, width=15).pack(side="left", padx=(0, 8))
        ttk.Button(bar2, text="题库配置说明", command=self.open_doc, width=15).pack(side="left", padx=(0, 8))
        ttk.Button(bar2, text="打开目录", command=self.open_dir, width=11).pack(side="left")

        # 选项
        opt = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        opt.pack(fill="x")
        self.var_autostart = tk.BooleanVar(value=self.has_startup())
        ttk.Checkbutton(
            opt, text="开机自启", variable=self.var_autostart, command=self.toggle_startup
        ).pack(side="left")
        self.var_kill = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="关闭面板时同时停止服务", variable=self.var_kill
        ).pack(side="left", padx=(18, 0))
        self.var_autostart_svc = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt, text="打开面板即启动服务", variable=self.var_autostart_svc
        ).pack(side="left", padx=(18, 0))

        # 日志
        logbox = ttk.Frame(self.root, padding=(16, 4, 16, 12))
        logbox.pack(fill="both", expand=True)
        ttk.Label(logbox, text="运行日志（尾部）", style="Dim.TLabel").pack(anchor="w", pady=(0, 4))
        wrap = tk.Frame(logbox, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.log = tk.Text(
            wrap, bg=CARD, fg=DIM, insertbackground=FG, relief="flat", wrap="none",
            font=("Consolas", 9), padx=10, pady=8, height=12,
        )
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set, state="disabled")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _kv(self, parent, key, val):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=key, bg=CARD, fg=DIM, width=6, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        lbl = tk.Label(row, text=val, bg=CARD, fg=FG, anchor="w",
                       font=("Consolas", 9))
        lbl.pack(side="left", fill="x", expand=True)
        return lbl

    # ---------------- 状态 ----------------
    def health(self, timeout=1.2):
        try:
            url = f"http://127.0.0.1:{self.port}/health"
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def refresh(self):
        """刷新状态（主线程调用；health 走短超时，卡顿可忽略）"""
        h = self.health()
        running = h is not None
        pid = None
        if running:
            if self.proc and self.proc.poll() is None:
                pid = self.proc.pid
            else:
                pid = listener_pid(self.port)
                self.adopted_pid = pid

        if running:
            self.badge.config(text="● 运行中", fg=OK)
            self.lbl_bank.config(text=f"{h.get('bank', 0)} 条", fg=OK)
            self.lbl_pid.config(text=str(pid) if pid else "外部启动", fg=FG)
        else:
            self.badge.config(text="● 已停止", fg=ERR)
            self.lbl_bank.config(text="—", fg=DIM)
            self.lbl_pid.config(text="—", fg=DIM)
            self.adopted_pid = None

        self._set_buttons(running)
        self._load_log()
        return running

    def _set_buttons(self, running):
        state_start = "disabled" if (running or self.busy) else "normal"
        state_stop = "disabled" if (not running or self.busy) else "normal"
        state_restart = "disabled" if self.busy else "normal"
        self.btn_start.config(state=state_start)
        self.btn_stop.config(state=state_stop)
        self.btn_restart.config(state=state_restart)

    def _tick(self):
        # 先清空后台线程排队的 UI 回调，再刷新状态
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                print(f"[gui] UI 回调异常: {e}")
        if not self.busy:
            self.refresh()
        self.root.after(2500, self._tick)

    def _post(self, fn):
        """从任意线程安全地把回调排到主线程执行"""
        self._ui_queue.put(fn)

    def _load_log(self):
        text = ""
        for f in (OUT_LOG, ERR_LOG):
            if f.exists():
                try:
                    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                    lines = [l for l in lines if l.strip()]
                    text += "\n".join(lines[-18:]) + "\n"
                except Exception:
                    pass
        if not text:
            text = "（暂无日志）"
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------------- 操作 ----------------
    def _server_cmd(self):
        """服务的启动命令。

        源码运行：python tiku_server.py
        打包运行：同一个 exe 加 --serve 参数，走内置分支启动服务，
                  否则会再开一个面板（无限套娃）。
        """
        if IS_FROZEN:
            return [sys.executable, "--serve"]
        return [sys.executable, str(SERVER_SCRIPT)]

    def start(self, _retry=0):
        if self.busy:
            if _retry < 60:
                self.root.after(200, lambda: self.start(_retry + 1))
            return
        if self.health() is not None:
            messagebox.showinfo("已经在运行", f"服务已经在 127.0.0.1:{self.port} 运行。")
            self.refresh()
            return
        if not IS_FROZEN and not SERVER_SCRIPT.exists():
            messagebox.showerror("文件缺失", f"找不到 {SERVER_SCRIPT}")
            return

        self.busy = True
        self._set_buttons(False)
        self.badge.config(text="● 启动中…", fg=WARN)

        def work():
            err = None
            try:
                OUT_LOG.parent.mkdir(parents=True, exist_ok=True)
                out = open(OUT_LOG, "ab")
                errf = open(ERR_LOG, "ab")
                self.proc = subprocess.Popen(
                    self._server_cmd(),
                    cwd=str(BASE),
                    stdout=out,
                    stderr=errf,
                    stdin=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
                for _ in range(40):           # 最多等 8 秒
                    if self.health(0.5) is not None:
                        break
                    if self.proc.poll() is not None:
                        err = "服务进程启动后立即退出，详见下方日志"
                        break
                    threading.Event().wait(0.2)
                else:
                    err = "启动超时，端口可能被占用"
            except Exception as e:
                err = f"启动失败：{e}"
            self._post(lambda err=err: self._after_start(err))

        threading.Thread(target=work, daemon=True).start()

    def _after_start(self, err):
        self.busy = False
        if err:
            self.badge.config(text="● 已停止", fg=ERR)
            messagebox.showerror("启动失败", err)
        self.refresh()

    def stop(self, _retry=0):
        # 上一次操作还没收尾：排队稍后重试，而不是静默丢弃点击
        if self.busy:
            if _retry < 60:
                self.root.after(200, lambda: self.stop(_retry + 1))
            return
        self.busy = True
        self._set_buttons(True)
        self.badge.config(text="● 停止中…", fg=WARN)

        def work():
            ok = False
            proc = self.proc
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    ok = True
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                        ok = True
                    except Exception:
                        pass
            # terminate 不一定拿得住（服务以 pythonw 跑时尤其如此），
            # 只要端口还在监听就按 PID 强杀，直到真的停掉。
            pid = self.adopted_pid or listener_pid(self.port)
            if pid and self.health(0.6) is not None:
                ok = kill_pid(pid) or ok
            elif pid and not ok:
                ok = kill_pid(pid) or ok
            for _ in range(30):
                if self.health(0.4) is None:
                    ok = True
                    break
                threading.Event().wait(0.2)
            self._post(lambda ok=ok: self._after_stop(ok))

        threading.Thread(target=work, daemon=True).start()

    def _after_stop(self, ok):
        self.busy = False
        self.proc = None
        self.adopted_pid = None
        if not ok and self.health() is not None:
            messagebox.showwarning("停止失败", "进程仍在运行，请尝试用任务管理器结束 python 进程。")
        self.refresh()
        # 重启：等停止真正收尾后，在主线程里再启动
        if self._pending_start:
            self._pending_start = False
            self.start()

    def restart(self, _retry=0):
        if self.busy:
            if _retry < 60:
                self.root.after(200, lambda: self.restart(_retry + 1))
            return
        self._pending_start = True
        self.stop()

    # ---------------- 快捷入口 ----------------
    def open_console(self):
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def copy_ocs(self):
        try:
            url = f"http://127.0.0.1:{self.port}/ocs/config"
            with urllib.request.urlopen(url, timeout=4) as r:
                text = r.read().decode("utf-8")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            messagebox.showinfo(
                "已复制",
                "OCS 题库配置已复制到剪贴板。\n\n"
                "粘贴位置：OCS → 通用 → 全局设置 → 题库配置 → 保存",
            )
        except Exception as e:
            messagebox.showerror("复制失败", f"服务可能未启动：{e}")

    def open_doc(self):
        doc = BASE / "OCS对接说明.md"
        if doc.exists():
            os.startfile(str(doc))
        else:
            messagebox.showinfo("文件缺失", f"找不到 {doc}")

    def open_dir(self):
        os.startfile(str(BASE))

    # ---------------- 开机自启 ----------------
    def startup_path(self):
        d = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
        return d / "学习通题库服务.lnk"

    def has_startup(self):
        try:
            return self.startup_path().exists()
        except Exception:
            return False

    def toggle_startup(self):
        lnk = self.startup_path()
        try:
            if self.var_autostart.get():
                target = BASE / "tiku_gui.vbs"
                if not target.exists():
                    target = BASE / "tiku_gui.py"
                ps = (
                    f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
                    f"$s.TargetPath='{(BASE / 'tiku_gui.vbs') if (BASE / 'tiku_gui.vbs').exists() else sys.executable}';"
                    f"$s.Arguments='\"{target}\"';"
                    f"$s.WorkingDirectory='{BASE}';"
                    f"$s.Save()"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
                messagebox.showinfo("已设置", f"已加入开机自启：\n{lnk}")
            else:
                if lnk.exists():
                    lnk.unlink()
                messagebox.showinfo("已取消", "已移除开机自启。")
        except Exception as e:
            messagebox.showerror("设置失败", str(e))
            self.var_autostart.set(self.has_startup())

    # ---------------- 关闭 ----------------
    def on_close(self):
        if self.var_kill.get() and self.health() is not None:
            if not messagebox.askyesno("确认", "关闭面板并停止题库服务？"):
                return
            self.stop()
            for _ in range(40):
                if not self.busy:
                    break
                self.root.update()
                threading.Event().wait(0.1)
        self.root.destroy()


def run_selftest(app, root):
    """在真实 mainloop 里跑一遍启动→停止，用于无人值守验收。

    Tk 的按钮是自绘的，没有原生 HWND，外部发不了 BM_CLICK，
    所以这里用 widget.invoke() 走和鼠标点击完全相同的回调路径。
    """
    steps = []

    def log(msg):
        print(msg, flush=True)
        steps.append(msg)

    def step1():
        log("[selftest] 点击 启动")
        app.btn_start.invoke()

    def step2():
        if app.busy:
            root.after(400, step2)
            return
        h = app.health()
        log(f"[selftest] 启动后 health={h}")
        steps.append("START_OK" if h else "START_FAIL")
        log("[selftest] 点击 停止")
        app.btn_stop.invoke()

    def step3():
        if app.busy:
            root.after(400, step3)
            return
        h = app.health()
        log(f"[selftest] 停止后 health={h}")
        steps.append("STOP_OK" if h is None else "STOP_FAIL")
        ok = "START_OK" in steps and "STOP_OK" in steps
        log("[selftest] 结果: " + ("PASS" if ok else "FAIL"))
        root.after(300, root.destroy)
        root._selftest_result = ok

    root.after(1200, step1)
    root.after(4000, step2)
    root.after(8000, step3)


def run_server_headless():
    """--serve：在同一个 exe 里跑服务（打包后 GUI 用它拉子进程）。"""
    import runpy
    server = BASE / "tiku_server.py"
    if server.exists():
        # 源码模式：直接跑脚本
        runpy.run_path(str(server), run_name="__main__")
        return
    # 打包模式：tiku_server 已被 PyInstaller 收进包，按模块执行
    import tiku_server
    tiku_server.main()


def main():
    if "--serve" in sys.argv:
        run_server_headless()
        return

    root = tk.Tk()
    app = App(root)
    if "--selftest" in sys.argv:
        run_selftest(app, root)
    root.mainloop()
    if "--selftest" in sys.argv:
        sys.exit(0 if getattr(root, "_selftest_result", False) else 1)


if __name__ == "__main__":
    main()
