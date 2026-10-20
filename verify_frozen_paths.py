#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「冻结环境」下的路径逻辑。

本机 WDAC 策略禁止执行新编译的 exe，所以没法真的双击测试。
但冻结相关的 bug 都集中在两处路径计算上，可以脱离 exe 单独验证：

  1. tiku_server.BASE   —— 冻结时应指向 exe 目录，而不是 _MEIPASS
  2. cxsecret_font.FontHashDAO._default_path() —— 冻结时应找到 font_map.json

做法：伪造 sys.frozen / sys._MEIPASS / sys.executable，importlib 重载模块，
看它算出来的路径对不对。这能覆盖"打包后数据存到临时目录，退出就丢"这类致命问题。
"""
import importlib
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


print("=" * 62)
print("冻结环境路径逻辑验证")
print("=" * 62)

# ---------- 造一个假的「exe 目录」和「_MEIPASS 目录」 ----------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    exe_dir = td / "app"
    meipass = td / "_MEI12345"
    exe_dir.mkdir()
    meipass.mkdir()

    # exe 目录放用户数据（模拟已有题库）
    (exe_dir / "bank.json").write_text(
        json.dumps([{"q": "测试题", "a": "A"}], ensure_ascii=False), encoding="utf-8")
    (exe_dir / "config.json").write_text(
        json.dumps({"port": 8890, "host": "127.0.0.1"}, ensure_ascii=False), encoding="utf-8")

    # _MEIPASS 放打包资源
    (meipass / "font_map.json").write_text(
        json.dumps({"一": "uni4E00"}, ensure_ascii=False), encoding="utf-8")

    fake_exe = exe_dir / "启动题库.exe"
    fake_exe.write_bytes(b"MZ fake")

    print(f"\n模拟环境：")
    print(f"  exe 目录  {exe_dir}")
    print(f"  _MEIPASS  {meipass}")

    # ---------- 1. tiku_server 的 BASE ----------
    print("\n[1] tiku_server.BASE 指向")
    # 保存原始状态
    saved = {k: getattr(sys, k, None) for k in ("frozen", "_MEIPASS", "executable")}
    had_frozen = hasattr(sys, "frozen")

    try:
        sys.frozen = True
        sys._MEIPASS = str(meipass)
        sys.executable = str(fake_exe)

        import tiku_server
        importlib.reload(tiku_server)

        check("BASE 指向 exe 目录（不是 _MEIPASS）",
              tiku_server.BASE == exe_dir, str(tiku_server.BASE))
        check("bank.json 从 exe 目录读",
              tiku_server.BANK_FILE == exe_dir / "bank.json", str(tiku_server.BANK_FILE))
        check("config.json 从 exe 目录读",
              tiku_server.CONF_FILE == exe_dir / "config.json", str(tiku_server.CONF_FILE))
        check("已存在的题库被正确加载",
              len(tiku_server.BANK) == 1, f"载入 {len(tiku_server.BANK)} 条")

        # resource_path 应该优先找 _MEIPASS
        rp = tiku_server.resource_path("font_map.json")
        check("resource_path 优先取 _MEIPASS 里的资源",
              rp == meipass / "font_map.json", str(rp))
    finally:
        # 还原
        for k, v in saved.items():
            if v is None:
                if hasattr(sys, k):
                    delattr(sys, k)
            else:
                setattr(sys, k, v)
        if not had_frozen and hasattr(sys, "frozen"):
            del sys.frozen

    # ---------- 2. cxsecret_font 的默认路径 ----------
    print("\n[2] cxsecret_font 字体映射路径")
    saved = {k: getattr(sys, k, None) for k in ("frozen", "_MEIPASS", "executable")}
    had_frozen = hasattr(sys, "frozen")
    try:
        sys.frozen = True
        sys._MEIPASS = str(meipass)
        sys.executable = str(fake_exe)

        import cxsecret_font
        importlib.reload(cxsecret_font)

        p = cxsecret_font.FontHashDAO._default_path()
        check("默认路径找到 _MEIPASS 里的 font_map.json",
              p == meipass / "font_map.json", str(p))

        # 懒加载：导入时不该读文件
        check("导入阶段不读 font_map.json（懒加载生效）",
              cxsecret_font._fonthash_dao is None,
              f"_fonthash_dao={cxsecret_font._fonthash_dao}")

        # 真正用到时才加载
        dao = cxsecret_font.get_fonthash_dao()
        check("按需加载成功", dao is not None and hasattr(dao, "char_map"))
        check("加载后缓存住", cxsecret_font._fonthash_dao is dao)
    finally:
        for k, v in saved.items():
            if v is None:
                if hasattr(sys, k):
                    delattr(sys, k)
            else:
                setattr(sys, k, v)
        if not had_frozen and hasattr(sys, "frozen"):
            del sys.frozen

    # ---------- 3. 源码模式不能被破坏 ----------
    print("\n[3] 源码模式（非冻结）回退正常")
    importlib.reload(sys.modules["tiku_server"])
    ts = sys.modules["tiku_server"]
    # 重新 import 纯净版本
    del sys.modules["tiku_server"]
    import tiku_server as ts2
    check("非冻结时 BASE 指向脚本目录",
          ts2.BASE == SRC, str(ts2.BASE))

    importlib.reload(sys.modules["cxsecret_font"])
    csf = sys.modules["cxsecret_font"]
    p2 = csf.FontHashDAO._default_path()
    check("非冻结时 font_map.json 与脚本同目录",
          p2 == SRC / "font_map.json", str(p2))
    check("脚本目录确实有这个文件", p2.exists())

# ---------- 4. --serve 参数分支 ----------
print("\n[4] --serve 分支识别")
src = (SRC / "tiku_gui.py").read_text(encoding="utf-8")
check("main() 里有 --serve 分支", '"--serve" in sys.argv' in src)
check("run_server_headless 存在", "def run_server_headless" in src)
check("run_server_headless 调用 tiku_server.main", "tiku_server.main()" in src)
srv = (SRC / "tiku_server.py").read_text(encoding="utf-8")
check("tiku_server 抽出 main() 函数", "def main():" in srv)
check("tiku_server 仍支持直接运行", 'if __name__ == "__main__":' in srv)

print("\n" + "=" * 62)
if fails:
    print(f"失败 {len(fails)} 项: {fails}")
else:
    print("PASS  冻结路径逻辑正确：数据落 exe 旁边，资源从 _MEIPASS 读")
print("=" * 62)
sys.exit(0 if not fails else 1)
