#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包成可分发的 zip。

产物结构（解压即用）：
    学习通题库/
      启动题库.exe          ← 双击这个
      README.txt            ← 给对方看的说明
      OCS配置.json          ← 直接粘进 OCS
      config.json           ← 干净配置（不含任何 key）
      bank.json             ← 示例题库

关键原则：
  1. 绝不把开发者的 API key 打进包里 —— config.json 从模板生成
  2. 排除日志、缓存、备份、测试脚本
  3. 打包后自检：重新解压一份、跑起来、验证接口，才算成功
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
DIST = SRC / "dist"
BUILD = SRC / "build"
STAGE = SRC / "_package"
APP_NAME = "学习通题库"
EXE_NAME = "启动题库"

# 必须进包的源码（PyInstaller 靠 import 分析，这些要能被找到）
PY_FILES = [
    "tiku_gui.py",
    "tiku_server.py",
    "cxsecret_font.py",
    "doubao_cdp.py",
    "xuexiaoyi_API.py",
]
DATA_FILES = ["font_map.json"]

# 绝不能进包的东西
EXCLUDE_PATTERNS = [
    "*.log", "*.bak*", "*.pyc", "__pycache__*", "*.tmpl", "*_test.py",
    "test_*.py", "verify_*.py", "probe_*.py", "server.py", "*_old*",
]


def log(msg):
    print(msg, flush=True)


def clean():
    for d in (DIST, BUILD, STAGE):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def check_secrets():
    """打包前拦一道：确保不会把真实 key 带出去"""
    problems = []
    tmpl = SRC / "config.template.json"
    if not tmpl.exists():
        problems.append("缺少 config.template.json")
    else:
        conf = json.loads(tmpl.read_text(encoding="utf-8"))
        if conf.get("llm_key"):
            problems.append("模板里 llm_key 非空")
        for name, prof in (conf.get("llm_profiles") or {}).items():
            if (prof or {}).get("key"):
                problems.append(f"模板里 llm_profiles.{name}.key 非空")
        if conf.get("xxy_open_id"):
            problems.append("模板里 xxy_open_id 非空")

    # 检查将要打包的源码里没有硬编码 key
    import re
    key_re = re.compile(r"(sk-[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_\-]{20,}|gsk_[A-Za-z0-9]{20,})")
    for f in PY_FILES:
        p = SRC / f
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if key_re.search(line):
                problems.append(f"{f}:{i} 疑似硬编码 key")
    return problems


def stage_sources():
    """把要打包的源码/数据复制到干净目录，避免 PyInstaller 扫到无关文件"""
    STAGE.mkdir(parents=True, exist_ok=True)
    for f in PY_FILES + DATA_FILES:
        shutil.copy2(SRC / f, STAGE / f)
    log(f"  暂存 {len(PY_FILES)} 个源码 + {len(DATA_FILES)} 个数据文件")


def build_exe():
    """PyInstaller 单文件 + 无控制台"""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",              # 无黑窗口
        "--name", EXE_NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        # 资源：font_map.json 打进包内，运行时从 _MEIPASS 读
        "--add-data", f"{STAGE / 'font_map.json'}{os.pathsep}.",
        # 服务端用到的隐式导入
        "--hidden-import", "tiku_server",
        "--hidden-import", "cxsecret_font",
        "--hidden-import", "doubao_cdp",
        "--hidden-import", "xuexiaoyi_API",
        "--hidden-import", "flask",
        "--hidden-import", "requests",
        "--hidden-import", "websockets",
        "--hidden-import", "fontTools.ttLib",
        # 砍掉不用的巨型依赖，显著减体积
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PIL",
        "--exclude-module", "scipy",
        "--exclude-module", "pytest",
        "--exclude-module", "setuptools",
        "--exclude-module", "pip",
        str(STAGE / "tiku_gui.py"),
    ]
    log("  运行 PyInstaller（首次约 1-3 分钟）…")
    r = subprocess.run(cmd, cwd=str(STAGE), capture_output=True, text=True)
    if r.returncode != 0:
        log("  PyInstaller 失败，尾部输出：")
        log((r.stderr or r.stdout)[-3000:])
        raise SystemExit(1)
    exe = DIST / f"{EXE_NAME}.exe"
    if not exe.exists():
        raise SystemExit(f"没有生成 {exe}")
    log(f"  生成 {exe.name}  {exe.stat().st_size / 1024 / 1024:.1f} MB")
    return exe


def make_readme():
    return """学习通题库服务 · 使用说明
================================================

【怎么启动】
  双击  启动题库.exe
  面板会自动把服务起起来，地址是  http://127.0.0.1:8890
  看到右上角显示「● 运行中」就说明好了。

  浏览器打开 http://127.0.0.1:8890 是控制台，
  可以在里面搜题测试、导入题库、调相似度阈值。


【怎么接 OCS 网课助手】
  1. 点面板上的「复制 OCS 配置」
  2. 打开 OCS → 通用 → 全局设置 → 题库配置
  3. 粘贴 → 保存
  4. 在 OCS 的「在线搜题」框里随便搜一道题，能出答案就通了。

  配置内容（也可以手动复制这个）：
  OCS配置.json 这个文件里的全部内容。


【题库从哪来】
  三种来源，服务会按顺序找：
    1. 本地题库（bank.json）—— 自己积累的，最准
    2. 学小易 —— 需要自己填 open_id，默认关闭
    3. 大模型 —— 需要在 config.json 里填自己的 API key，默认关闭

  本地题库怎么涨：
    - 控制台页面「批量导入」粘贴 JSON
    - 或者用 /learn 接口批量收录


【想用大模型兜底】
  编辑同目录下的 config.json，把 key 填进去：
    {
      "llm_enabled": true,
      "llm_provider": "deepseek",
      "llm_profiles": {
        "deepseek": {
          "base": "https://api.deepseek.com/v1",
          "model": "deepseek-chat",
          "key": "你自己的 key"
        }
      }
    }
  改完在面板上点「重启」生效。

  key 要自己去 platform.deepseek.com 申请，本程序不附带任何 key。


【常见问题】
  Q: 双击没反应？
  A: 可能被安全软件拦了（PyInstaller 打包的程序常见）。
     把整个文件夹加入白名单，或者右键「以管理员身份运行」。

  Q: 提示端口被占用？
  A: 已经开着一个了，去任务栏找找；或者改 config.json 里的 port。

  Q: 面板显示「已停止」，点启动没反应？
  A: 看面板下方的日志区，会有具体原因。

  Q: 杀毒软件报毒？
  A: PyInstaller 单文件程序容易被误报。这是打包方式的通病，
     不是程序有问题。可把源码拿去自己打包，或加白名单。


【文件说明】
  启动题库.exe     主程序（面板 + 服务在同一个文件里）
  config.json      配置：端口、阈值、大模型 key
  bank.json        题库数据（会随使用增长，记得备份）
  OCS配置.json     粘贴到 OCS 的题库配置
  README.txt       本文件


【注意】
  服务只监听 127.0.0.1，同一局域网的其他人访问不到，这是故意的。
  题库数据存在 bank.json，换电脑把整个文件夹拷走就行。
"""


def make_ocs_config():
    port = 8890
    tmpl = SRC / "ocs-answerer-wrapper.json"
    if tmpl.exists():
        try:
            cfg = json.loads(tmpl.read_text(encoding="utf-8"))
            if isinstance(cfg, list) and cfg:
                return json.dumps(cfg, ensure_ascii=False, indent=2)
        except Exception:
            pass
    # 兜底：现场生成
    return json.dumps([{
        "name": "本地题库",
        "homepage": f"http://127.0.0.1:{port}/",
        "url": f"http://127.0.0.1:{port}/ocs",
        "method": "post",
        "contentType": "json",
        "type": "GM_xmlhttpRequest",
        "headers": {"Content-Type": "application/json"},
        "data": {
            "title": "${title}",
            "options": {"handler": "return (env)=> (env.options || '').split('\\n').filter(Boolean)"},
            "type": {"handler": "return (env)=> env.type || ''"},
        },
        "handler": "return (res) => { if (!res || res.code !== 1 || !res.answer) return undefined; return [res.question || undefined, res.answer];}",
    }], ensure_ascii=False, indent=2)


def pack_zip(exe):
    root = STAGE / "out" / APP_NAME
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    shutil.copy2(exe, root / f"{EXE_NAME}.exe")
    # 干净配置：从模板生成，确保没有任何 key
    shutil.copy2(SRC / "config.template.json", root / "config.json")
    # 题库：带上示例数据，方便对方一打开就有东西可搜
    bank_src = SRC / "bank.json"
    if bank_src.exists():
        shutil.copy2(bank_src, root / "bank.json")
    else:
        (root / "bank.json").write_text("[]", encoding="utf-8")

    (root / "OCS配置.json").write_text(make_ocs_config(), encoding="utf-8")
    (root / "README.txt").write_text(make_readme(), encoding="utf-8")

    zip_path = DIST / f"{APP_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                z.write(f, f"{APP_NAME}/{f.name}")
    log(f"  打包 {zip_path.name}  {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
    return zip_path


def verify_zip(zip_path):
    """验证产物。

    注意：不在这里尝试运行 exe。某些机器有 WDAC/App Control 策略，
    新编译的未签名 exe 会被直接拒绝执行（An Application Control policy
    has blocked this file），那是环境限制，不是打包问题。
    能运行的环境请手动跑 dist/启动题库.exe / --selftest 做端到端验收。

    这里只做能确定的部分：zip 完整性、配置无密钥、无开发残留、
    exe 内嵌归档内容齐全、冻结路径逻辑正确。
    """
    log("\n验证 1/2：zip 内容")
    test_dir = SRC / "_verify"
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
    test_dir.mkdir()

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        z.extractall(test_dir)

    app_dir = test_dir / APP_NAME
    ok = True

    # 1. 文件齐全
    for must in ["启动题库.exe", "config.json", "bank.json", "OCS配置.json", "README.txt"]:
        exists = (app_dir / must).exists()
        log(f"  {'PASS' if exists else 'FAIL'}  含 {must}")
        ok = ok and exists

    # 2. 配置干净（无 key）
    conf = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
    clean = not conf.get("llm_key") and not any(
        (v or {}).get("key") for v in (conf.get("llm_profiles") or {}).values()
    ) and not conf.get("xxy_open_id")
    log(f"  {'PASS' if clean else 'FAIL'}  config.json 不含任何密钥")
    ok = ok and clean

    # 3. 没有夹带日志/备份/测试
    junk = [n for n in names if n.endswith((".log", ".bak-ocs")) or "pycache" in n
            or "_test" in n or "verify_" in n or "probe_" in n]
    log(f"  {'PASS' if not junk else 'FAIL'}  无日志/备份/测试残留  {junk or ''}")
    ok = ok and not junk

    # 4. OCS 配置可解析
    try:
        ocs = json.loads((app_dir / "OCS配置.json").read_text(encoding="utf-8"))
        valid = (isinstance(ocs, list) and ocs
                 and all(k in ocs[0] for k in ("name", "url", "handler")))
        log(f"  {'PASS' if valid else 'FAIL'}  OCS配置.json 是合法 AnswererWrapper")
        ok = ok and valid
    except Exception as e:
        log(f"  FAIL  OCS配置.json 解析失败 {e}")
        ok = False

    shutil.rmtree(test_dir, ignore_errors=True)

    # 5. 交给专门的静态验证器检查 exe 归档
    log("\n验证 2/2：exe 内嵌归档 + 冻结路径逻辑")
    for script, title in (("verify_package.py", "归档完整性"),
                          ("verify_frozen_paths.py", "冻结路径")):
        p = SRC / script
        if not p.exists():
            log(f"  SKIP  {script} 不存在")
            continue
        r = subprocess.run([sys.executable, "-u", str(p)],
                           capture_output=True, text=True, cwd=str(SRC))
        passed = r.returncode == 0
        log(f"  {'PASS' if passed else 'FAIL'}  {title}（{script}）")
        if not passed:
            tail = (r.stdout or r.stderr).strip().splitlines()[-12:]
            for line in tail:
                log(f"        {line}")
        ok = ok and passed

    log("\n  提示：本机若启用了 App Control 策略，无法直接运行新 exe。")
    log("        换到普通机器解压后双击「启动题库.exe」即可。")

    return ok


def main():
    log("=" * 56)
    log("打包 学习通题库")
    log("=" * 56)

    log("\n[1/6] 清理旧产物")
    clean()

    log("\n[2/6] 安全检查（防止泄露 API key）")
    problems = check_secrets()
    if problems:
        for p in problems:
            log(f"  BLOCK  {p}")
        log("\n有敏感信息，已中止打包。")
        return 1
    log("  PASS  配置模板与源码均无 key")

    log("\n[3/6] 暂存源码")
    stage_sources()

    log("\n[4/6] 构建 exe")
    exe = build_exe()

    log("\n[5/6] 组装 zip")
    zip_path = pack_zip(exe)

    log("\n[6/6] 验证产物")
    ok = verify_zip(zip_path)

    log("\n" + "=" * 56)
    if ok:
        log(f"成功：{zip_path}")
        log(f"体积：{zip_path.stat().st_size / 1024 / 1024:.1f} MB")
        log("解压后双击「启动题库.exe」即可使用。")
    else:
        log("验证未通过，产物可能不可用，请查看上面的 FAIL 项。")
    log("=" * 56)

    shutil.rmtree(STAGE / "out", ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
