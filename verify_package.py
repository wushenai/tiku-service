#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包产物完整性检查（修正版）。

PyInstaller 的结构要点，搞错了会得出完全相反的结论：
  - 入口脚本编译成 CArchive 里的顶层条目，名字就叫 tiku_gui
  - 其他纯 Python 模块压进 CArchive 里的 PYZ.pyz
  - .pyd / .dll / 数据文件是 CArchive 里的独立条目
所以三类地方都要查，只查一处必然漏判。
"""
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

SRC = Path(__file__).resolve().parent
exe = SRC / "dist" / "启动题库.exe"

print("=" * 62)
print("打包产物完整性检查")
print("=" * 62)

fails = []
carc = CArchiveReader(str(exe))
carc_names = list(carc.toc.keys())

# 抽 PYZ
pyz_tmp = SRC / "_pyz_check.pyz"
pyz_tmp.write_bytes(carc.extract("PYZ.pyz"))
pyz = ZlibArchiveReader(str(pyz_tmp))
pyz_names = list(pyz.toc.keys())

print(f"\nCArchive 条目: {len(carc_names)}    PYZ 模块: {len(pyz_names)}")

# 三类位置的查找
def find(name):
    """返回 (位置, 是否找到)"""
    if any(n == name or n.startswith(name + ".") for n in pyz_names):
        return "PYZ"
    if any(n == name or n.lower().startswith(name.lower()) for n in carc_names):
        return "CArchive"
    return None


print("\n[1] 纯 Python 模块（PYZ 或入口）")
checks = [
    ("tiku_gui", "控制面板（入口脚本）"),
    ("tiku_server", "服务端主体"),
    ("cxsecret_font", "字体解密"),
    ("doubao_cdp", "豆包 CDP"),
    ("xuexiaoyi_API", "学小易"),
    ("flask", "Web 框架"),
    ("requests", "HTTP 依赖"),
    ("websockets", "CDP 依赖"),
    ("fontTools", "字体解析"),
    ("tkinter", "GUI 基础"),
]
for name, desc in checks:
    where = find(name)
    if where:
        cnt = len([n for n in (pyz_names if where == "PYZ" else carc_names)
                   if n == name or n.startswith(name + ".")])
        print(f"  PASS  {name:16s} {desc:16s} [{where}] {cnt} 个子项")
    else:
        fails.append(name)
        print(f"  FAIL  {name:16s} {desc:16s} 未找到")

print("\n[2] 二进制与数据文件（CArchive 独立条目）")
bins = [
    ("font_map.json", "字体映射数据"),
    ("_tkinter.pyd", "Tk 绑定"),
    ("tcl86t.dll", "Tcl 运行时"),
    ("tk86t.dll", "Tk 运行时"),
    ("python312.dll", "Python 运行时"),
]
for name, desc in bins:
    hit = [n for n in carc_names if name.lower() in n.lower()]
    if hit:
        print(f"  PASS  {name:18s} {desc:12s} {hit[0]}")
    else:
        fails.append(name)
        print(f"  FAIL  {name:18s} {desc:12s} 未找到")

print("\n[3] 关键模块能否真的取出字节码")
for name in ("tiku_server", "cxsecret_font", "doubao_cdp", "xuexiaoyi_API"):
    try:
        code = pyz.extract(name)
        ok = code is not None and hasattr(code, "co_code")
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: 取到 code 对象，字节码 {len(code.co_code)} 字节")
        if not ok:
            fails.append(name)
    except Exception as e:
        fails.append(name)
        print(f"  FAIL  {name}: {e}")

# 入口脚本：CArchive 里存的是 marshal 序列化后的原始字节，要自己反序列化
try:
    entry = carc.extract("tiku_gui")
    if isinstance(entry, (bytes, bytearray)):
        import marshal
        entry = marshal.loads(entry)
    ok = entry is not None and hasattr(entry, "co_code")
    print(f"  {'PASS' if ok else 'FAIL'}  tiku_gui(入口): 字节码 {len(entry.co_code)} 字节")
    if not ok:
        fails.append("tiku_gui")
except Exception as e:
    fails.append("tiku_gui")
    print(f"  FAIL  tiku_gui(入口): {e}")

# 深入确认 tiku_server 里关键函数存在
print("\n[4] 服务端关键函数是否被编译进包")
try:
    code = pyz.extract("tiku_server")
    names = set()

    def walk(c, depth=0):
        if depth > 2:
            return
        for k in c.co_consts:
            if hasattr(k, "co_name"):
                names.add(k.co_name)
                walk(k, depth + 1)

    walk(code)
    for fn in ("main", "ocs_search", "font_decrypt", "_ocs_answer", "bank_lookup", "llm_lookup"):
        ok = fn in names
        print(f"  {'PASS' if ok else 'FAIL'}  {fn}")
        if not ok:
            fails.append(fn)
except Exception as e:
    print(f"  FAIL  深挖失败: {e}")
    fails.append("deep-walk")

pyz_tmp.unlink(missing_ok=True)

print("\n" + "=" * 62)
if fails:
    print(f"异常 {len(fails)} 项: {fails}")
else:
    print("PASS  打包内容完整：入口 + 服务端 + 全部依赖 + 数据文件")
print("=" * 62)
sys.exit(0 if not fails else 1)
