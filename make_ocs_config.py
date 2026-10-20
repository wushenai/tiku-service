#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成可直接粘贴到 OCS 网课助手「题库配置」的 AnswererWrapper JSON。

为什么需要它：
  OCS 的 handler 是字符串丢给 Function() 构造的，手写极易踩三个坑：
    1. 忘记 return 一个函数（必须 `return (res) => ...`）
    2. 多选答案返回数组（OCS 只认字符串，随后用 # ; | 拆）
    3. 题型/选项没按 OCS 的 env 约定转换

用法：
  python make_ocs_config.py                       # 默认 127.0.0.1:8890
  python make_ocs_config.py --host 127.0.0.1 --port 8890 --miss
  python make_ocs_config.py -o ocs-answerer-wrapper.json
"""
import argparse
import json
import sys
from pathlib import Path

HANDLER = (
    "return (res) => {"
    " if (!res || res.code !== 1 || !res.answer) return undefined;"
    " return [res.question || undefined, res.answer];"
    "}"
)

HANDLER_MISS = (
    "return (res) => {"
    " if (!res) return undefined;"
    " if (res.code !== 1 || !res.answer) return [res.message || res.source || '未命中', undefined];"
    " return [res.question || undefined, res.answer];"
    "}"
)


def build(host: str, port: int, show_miss: bool):
    base = f"http://{host}:{port}"
    return [{
        "name": "本地题库(8890)" if port == 8890 else f"本地题库({port})",
        "homepage": base + "/",
        "url": base + "/ocs",
        "method": "post",
        "contentType": "json",
        # 用 GM_xmlhttpRequest：油猴/脚本猫里可跨域，不受 https 页面的混合内容限制
        "type": "GM_xmlhttpRequest",
        "headers": {"Content-Type": "application/json"},
        "data": {
            "title": "${title}",
            # env.options 是换行分隔的字符串，转成数组交给服务端
            "options": {"handler": "return (env)=> (env.options || '').split('\\n').filter(Boolean)"},
            "type": {"handler": "return (env)=> env.type || ''"},
        },
        "handler": HANDLER_MISS if show_miss else HANDLER,
    }]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8890)
    ap.add_argument("--miss", action="store_true", help="未命中时返回提示文案而不是 undefined")
    ap.add_argument("-o", "--out", default="ocs-answerer-wrapper.json")
    args = ap.parse_args()

    wrappers = build(args.host, args.port, args.miss)
    text = json.dumps(wrappers, ensure_ascii=False, indent=2)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已写入 {args.out}（{len(text)} 字符）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
