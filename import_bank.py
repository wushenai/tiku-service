#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 tiku.json（OCS/其他格式的题库）导入正在运行的 tiku_server。

支持三种输入格式：
  1. [{"q": "...", "a": "..."}]                     本地格式
  2. [{"question": "...", "answer": ["A","C"]}]     tiku.json 格式，数组会被拼成 A#C
  3. {"题干": "答案"}                                键值式

用法：
  python import_bank.py tiku.json
  python import_bank.py tiku.json --url http://127.0.0.1:8890
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path


def normalize(items):
    out = []
    if isinstance(items, dict):
        items = [{"q": k, "a": v} for k, v in items.items()]
    for it in items:
        if not isinstance(it, dict):
            continue
        q = it.get("q") or it.get("question") or it.get("title") or it.get("题目")
        a = it.get("a") if it.get("a") is not None else it.get("answer", it.get("答案"))
        if not q or a in (None, ""):
            continue
        if isinstance(a, (list, tuple)):
            a = "#".join(str(x).strip() for x in a if str(x).strip())
        out.append({"q": str(q).strip(), "a": str(a).strip()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--url", default="http://127.0.0.1:8890")
    args = ap.parse_args()

    path = Path(args.file)
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    items = normalize(raw)
    if not items:
        print("没有可导入的条目")
        return 1

    body = json.dumps(items, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        args.url.rstrip("/") + "/bank",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.read().decode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
