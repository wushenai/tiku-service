#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学习通题库本地服务 (tiku_server)
--------------------------------
接口:
  GET  /health                健康检查
  POST /v1/cx                 搜题  form: question=xxx  (也接受 json)
  POST /decrypt               字体解密  json: {"secFont": "...", "dstText": "..."}
  POST /learn                 收录一条问答  json: {"q": "...", "a": "A"}
  GET  /bank                  导出题库
  POST /bank                  导入题库  json: [...] 或 {"q": "...", "a": "..."}
  GET  /stats                 统计

题库匹配: bigram Dice 相似度, 默认阈值 0.5
所有响应带 CORS + Private Network Access 头, 允许 https 页面直接 fetch。
"""

import json
import os
import re
import sys
import time
import threading
from pathlib import Path

from flask import Flask, request, jsonify

BASE = Path(__file__).resolve().parent
# PyInstaller 打包后 __file__ 指向 _MEIPASS 临时目录，而用户数据（bank/config/log）
# 必须落在 exe 旁边，否则每次退出就丢。冻结时改用 exe 所在目录。
if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
BANK_FILE = BASE / "bank.json"
CONF_FILE = BASE / "config.json"
LOG_FILE = BASE / "query.log"

DEFAULT_CONF = {
    "host": "127.0.0.1",
    "port": 8890,
    "fuzzy": 0.5,
    "xxy_open_id": "",
    "llm_enabled": False,
    "llm_provider": "doubao",
    "llm_base": "",
    "llm_key": "",
    "llm_model": "",
    "llm_cache": True,
    "llm_first": False,
    "llm_fallback": True,
    "llm_profiles": {}
}

# 各厂商预设：label 显示名，base 接口地址，model 推荐模型，note 备注
# 全部走 OpenAI 兼容的 /chat/completions，无需额外 SDK
LLM_PRESETS = {
    "doubao_cdp": {
        "label": "豆包客户端 (本机 CDP 免 key)",
        "base": "cdp://127.0.0.1:9334",
        "model": "doubao-client",
        "note": "直接用已登录的豆包客户端答题；客户端需带 --remote-debugging-port=9334 启动",
    },
    "doubao": {
        "label": "豆包 (火山方舟)",
        "base": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-250615",
        "note": "console.volcengine.com/ark 开通后建 API Key；模型名也可填 ep- 接入点",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "platform.deepseek.com 充值后建 key",
    },
    "qwen": {
        "label": "通义千问 (阿里百炼)",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "bailian.console.aliyun.com 获取 DashScope API Key",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "note": "open.bigmodel.cn 有免费额度，glm-4-flash 便宜",
    },
    "moonshot": {
        "label": "Kimi (Moonshot)",
        "base": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "note": "platform.moonshot.cn 建 key",
    },
    "baichuan": {
        "label": "百川智能",
        "base": "https://api.baichuan-ai.com/v1",
        "model": "Baichuan4-Air",
        "note": "platform.baichuan-ai.com",
    },
    "hunyuan": {
        "label": "腾讯混元",
        "base": "https://api.hunyuan.cloud.tencent.com/v1",
        "model": "hunyuan-lite",
        "note": "hunyuan.cloud.tencent.com 控制台建 key",
    },
    "spark": {
        "label": "讯飞星火",
        "base": "https://spark-api-open.xf-yun.com/v1",
        "model": "generalv3.5",
        "note": "console.xfyun.cn 用 APIPassword 作 key",
    },
    "ernie": {
        "label": "百度文心",
        "base": "https://qianfan.baidubce.com/v2",
        "model": "ernie-4.0-turbo-8k",
        "note": "千帆平台建应用拿 API Key；新版走 v2",
    },
    "minimax": {
        "label": "MiniMax",
        "base": "https://api.minimax.chat/v1",
        "model": "abab6.5s-chat",
        "note": "platform.minimaxi.com",
    },
    "openai": {
        "label": "OpenAI",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "国内需代理，访问不了就别选",
    },
    "openrouter": {
        "label": "OpenRouter (聚合)",
        "base": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.0-flash-001",
        "note": "一个 key 通多家模型，按量计费",
    },
    "llm7": {
        "label": "LLM7.io (免 key)",
        "base": "https://api.llm7.io/v1",
        "model": "gpt-oss:20b",
        "note": "不用申请 key，key 留空自动填 unused；有速率限制(429)，answers 慢，只当兜底",
    },
    "pollinations": {
        "label": "Pollinations (免 key)",
        "base": "https://text.pollinations.ai/openai",
        "model": "openai",
        "note": "完全免 key，直接 POST 到 base 本身(已含 /openai)，不用再加 /v1",
    },
    "ollama": {
        "label": "Ollama (本地)",
        "base": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "note": "本地跑，免费无限制；key 随便填非空即可",
    },
    "lmstudio": {
        "label": "LM Studio (本地)",
        "base": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "note": "图形界面本地模型，启动时开启 Local Server",
    },
    "custom": {
        "label": "自定义 (任意 OpenAI 兼容)",
        "base": "",
        "model": "",
        "note": "填完整的 base（要含 /v1 或 /v3 之类前缀）",
    },
}

lock = threading.Lock()
app = Flask(__name__)


# ---------------- 配置 / 题库 ----------------
def load_conf():
    conf = dict(DEFAULT_CONF)
    if CONF_FILE.exists():
        try:
            conf.update(json.loads(CONF_FILE.read_text(encoding="utf-8-sig")))
        except Exception as e:
            print(f"[tiku] config.json 解析失败({e})，使用默认值")
    # 环境变量可覆盖(方便临时给 key)
    if os.getenv("TIKU_LLM_KEY"):
        conf["llm_key"] = os.getenv("TIKU_LLM_KEY")
        conf["llm_enabled"] = True
    if os.getenv("TIKU_LLM_BASE"):
        conf["llm_base"] = os.getenv("TIKU_LLM_BASE")
    if os.getenv("TIKU_LLM_MODEL"):
        conf["llm_model"] = os.getenv("TIKU_LLM_MODEL")
    return conf


CONF = load_conf()


def load_bank():
    if BANK_FILE.exists():
        try:
            data = json.loads(BANK_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [{"q": k, "a": v} for k, v in data.items()]
        except Exception:
            pass
    return []


BANK = load_bank()
print(f"[tiku] 题库已载入 {len(BANK)} 条")


def save_bank():
    tmp = BANK_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(BANK, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(BANK_FILE)


# ---------------- 归一化 / 相似度 ----------------
PUNCT = re.compile(r"[\s\u3000（）()【】\[\]「」《》\"'’‘“”、，,。.：:；;！!？?—\-_~·…]")


def norm(s):
    return PUNCT.sub("", str(s or "")).lower()


def grams(s):
    return {s[i:i + 2]: 0 for i in range(len(s) - 1)}


def dice(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    ga, gb = grams(a), grams(b)
    inter = len(set(ga) & set(gb))
    total = len(ga) + len(gb)
    return (2 * inter) / total if total else 0.0


def bank_lookup(question):
    n = norm(question)
    if not n or not BANK:
        return None
    best, best_s = None, 0.0
    for item in BANK:
        s = dice(n, norm(item.get("q", "")))
        if s > best_s:
            best_s, best = s, item
            if s >= 0.999:
                break
    if best and best_s >= CONF.get("fuzzy", 0.5):
        return best.get("a"), round(best_s, 3)
    return None


# ---------------- 字体解密 ----------------
_font_dao = None


def resource_path(name):
    """打包后资源在 sys._MEIPASS，源码运行在脚本同目录"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = Path(base) / name
        if p.exists():
            return p
    return BASE / name


def font_decrypt(sec_font_b64, dst_text):
    global _font_dao
    import cxsecret_font
    # cxsecret_font 在导入时就按 __file__ 读 font_map.json，
    # 冻结环境下那个路径不存在，所以这里显式传入真实路径。
    if _font_dao is None:
        _font_dao = cxsecret_font.FontHashDAO(str(resource_path("font_map.json")))
    fmap = cxsecret_font.font2map(sec_font_b64)
    return cxsecret_font.decrypt(fmap, dst_text)


# ---------------- 远程兜底 ----------------
def xxy_lookup(question):
    oid = (CONF.get("xxy_open_id") or "").strip()
    if not oid:
        return None
    try:
        from xuexiaoyi_API import XxyWxAPI
        api = XxyWxAPI(oid)
        if not api.search(question):
            return None
        q_text, a_text = api.get(0)
        if "登录已过期" in a_text or "请打开微信" in a_text:
            print("[tiku] 学小易 open_id 失效")
            return None
        return a_text
    except Exception as e:
        print("[tiku] 学小易异常:", e)
        return None


_QTYPE_NAME = {0: "single", 1: "multi", 2: "blank", 3: "judge", 4: "essay",
               "0": "single", "1": "multi", "2": "blank", "3": "judge", "4": "essay"}


def build_prompt(question, options, qtype):
    qtype = _QTYPE_NAME.get(qtype, qtype) if not isinstance(qtype, str) or qtype.isdigit() else qtype
    prompt = question.strip()
    if options:
        prompt += "\n\n选项：\n" + "\n".join(options)
    type_hint = {
        "single": "这是一道单选题，只输出一个选项字母。",
        "multi": "这是一道多选题，按字母顺序连写输出，如 ACD。",
        "judge": "这是一道判断题，只输出 对 或 错。",
        "blank": "这是填空题，直接输出答案内容。",
        "essay": "这是简答题，直接输出答案要点。",
    }.get(qtype or "", "")
    sys_prompt = (
        "你是严谨的答题助手。只输出最终答案，不要解释、不要复述题目。\n"
        "规则：\n"
        "1. 有选项的题，只输出选项字母（如 A 或 ACD），不要输出选项内容。\n"
        "2. 判断题输出 对 或 错。\n"
        "3. 填空题输出要填的内容，多个空用 ; 分隔。\n"
        "4. 简答题输出简洁要点。\n"
        + type_hint
    )
    return sys_prompt, prompt


def resolve_provider(name):
    """返回 (base, model, key, provider) —— 空值回退到预设"""
    name = (name or CONF.get("llm_provider") or "doubao").lower()
    profiles = CONF.get("llm_profiles") or {}
    prof = profiles.get(name) or {}
    preset = LLM_PRESETS.get(name, {})

    base = (prof.get("base") or CONF.get("llm_base") or preset.get("base") or "").rstrip("/")
    model = prof.get("model") or CONF.get("llm_model") or preset.get("model") or ""
    key = prof.get("key") or CONF.get("llm_key") or ""
    return base, model, key, name


def _clean_answer_text(text):
    """把模型/豆包返回的原始文本压成一行答案"""
    t = (text or "").strip()
    t = t.replace("**", "").replace("`", "")
    t = re.sub(r"^\s*(答案|正确答案|参考答案|故选|选)\s*[:：]?\s*", "", t)
    t = re.sub(r"^(my )?answer\s*[:：]\s*", "", t, flags=re.I)
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    if not lines:
        return ""
    first = lines[0]
    # "B. 艰苦奋斗" / "B、艰苦奋斗" -> 取字母
    m = re.match(r"^([A-Ha-h])(?:\s*[.、,，:：)）]\s*|\s+)\S", first)
    if m:
        return m.group(1).upper()
    # "ACD 解析：..." -> 取字母串
    m = re.match(r"^([A-H]{1,8})\b", first)
    if m:
        return m.group(1)
    return first


def _call_doubao_cdp(question, options, qtype):
    """走本机豆包客户端的 CDP 界面答题，不需要任何 API key"""
    sys_prompt, prompt = build_prompt(question, options, qtype)
    ask_text = f"{sys_prompt}\n\n{prompt}"
    try:
        import asyncio
        import doubao_cdp
        res = asyncio.run(doubao_cdp.ask(
            ask_text,
            timeout=float(CONF.get("cdp_timeout", 90)),
            new_chat=bool(CONF.get("cdp_new_chat", True)),
        ))
    except Exception as e:
        print(f"[tiku] 豆包 CDP 失败: {e}")
        return None, "cdp-error"
    ans = _clean_answer_text(res.get("answer"))
    if ans:
        print(f"[tiku] 豆包CDP 命中 {res.get('cost')}s -> {ans[:30]}")
    return (ans or None), ("ok" if ans else "empty")


# 免 key 平台：key 留空时自动填占位值，不当作"未配置"
NO_KEY_PROVIDERS = {"llm7", "pollinations"}


def _call_one(provider, question, options, qtype, timeout=45):
    if (provider or "").lower() in ("doubao_cdp", "doubao-client", "cdp"):
        return _call_doubao_cdp(question, options, qtype)
    import requests
    base, model, key, name = resolve_provider(provider)
    if not key and name not in NO_KEY_PROVIDERS:
        return None, "no-key"
    if not base:
        return None, "no-base"

    sys_prompt, prompt = build_prompt(question, options, qtype)
    # 推理型模型会先吐一大段 reasoning_content，max_tokens 给小了会 finish_reason=length 且 content 为空
    max_tokens = 1600 if name in NO_KEY_PROVIDERS else 400

    # 有些平台的 base 已经带完整端点（如 pollinations 的 .../openai），不能再拼 /chat/completions
    url = base if base.rstrip("/").endswith(("/chat/completions", "/openai")) else base + "/chat/completions"

    try:
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + (key or "unused"), "Content-Type": "application/json"},
            json={
                "model": model or "default",
                "temperature": 0,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=timeout,
        )
    except Exception as e:
        print(f"[tiku] {name} 请求异常: {e}")
        return None, "timeout"

    if r.status_code != 200:
        print(f"[tiku] {name} HTTP {r.status_code}: {r.text[:160]}")
        return None, f"http-{r.status_code}"
    j = r.json()
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text and msg.get("reasoning_content"):
        # 只有思维链没有正文：从思维链里兜一个答案出来
        text = msg["reasoning_content"] or ""
    text = _clean_answer_text(text)
    return (text or None), ("ok" if text else "empty")


def llm_lookup(question, options=None, qtype=None):
    """options: ["A. xxx", "B. yy"]；按当前 provider 调用，失败可按顺序回退"""
    primary_name = (CONF.get("llm_provider") or "doubao").lower()
    if not CONF.get("llm_enabled") and primary_name not in ("doubao_cdp", "cdp", "doubao-client"):
        return None
    try:
        primary = (CONF.get("llm_provider") or "doubao").lower()
        order = [primary]
        if CONF.get("llm_fallback"):
            # 已配置 key 的其他平台 + 免 key 平台作为后备，按配置文件顺序
            for p, prof in (CONF.get("llm_profiles") or {}).items():
                if p != primary and (prof or {}).get("key"):
                    order.append(p)
            for p in NO_KEY_PROVIDERS:
                if p != primary and p not in order:
                    order.append(p)

        for p in order:
            ans, status = _call_one(p, question, options, qtype)
            if ans:
                if p != primary:
                    print(f"[tiku] {primary} 失败，回退到 {p} 成功")
                return ans
            if status == "no-key":
                continue
        return None
    except Exception as e:
        print("[tiku] LLM 异常:", e)
        return None


# ---------------- Flask 钩子 ----------------
@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.route("/v1/cx", methods=["POST", "OPTIONS", "GET"])
def search():
    if request.method == "OPTIONS":
        return ("", 204)

    q = ""
    options = []
    qtype = ""
    if request.method == "GET":
        q = request.args.get("question", "")
        qtype = request.args.get("type", "")
    elif request.is_json:
        payload = request.get_json(silent=True) or {}
        q = payload.get("question", "")
        options = payload.get("options") or []
        qtype = payload.get("type", "")
    else:
        q = request.form.get("question", "")
        raw = request.form.get("options", "")
        if raw:
            try:
                options = json.loads(raw)
            except Exception:
                options = [x.strip() for x in raw.split("|") if x.strip()]
        qtype = request.form.get("type", "")

    if isinstance(options, str):
        options = [x.strip() for x in options.split("|") if x.strip()]

    q = re.sub(r"【(单选题|多选题|判断题|填空题|简答题)】", "", q or "").strip()
    if not q:
        return jsonify({"code": -1, "message": "question 为空", "data": ""})

    t0 = time.time()
    ans, hit, score = "", False, 0.0

    with lock:
        got = bank_lookup(q)
    if got:
        ans, score = got
        hit = True
        src = "本地"
    else:
        ans = xxy_lookup(q) or llm_lookup(q, options, qtype) or ""
        src = "大模型" if ans else "未命中"
        if ans and (CONF.get("llm_cache", True)):
            with lock:
                BANK.append({"q": q, "a": ans, "t": time.strftime("%Y-%m-%d")})
                save_bank()

    line = f"{time.strftime('%H:%M:%S')} [{src}] {q[:40]} -> {str(ans)[:40]}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    return jsonify({
        "code": 1 if ans else 0,
        "message": "",
        "data": ans,
        "hit": hit,
        "score": score,
        "source": src,
        "cost": round(time.time() - t0, 3)
    })


@app.route("/decrypt", methods=["POST", "OPTIONS"])
def decrypt():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        args = request.get_json(force=True, silent=True) or {}
        src = font_decrypt(args.get("secFont", ""), args.get("dstText", ""))
        return jsonify({"srcText": src})
    except Exception as e:
        return jsonify({"srcText": "", "error": str(e)})


@app.route("/learn", methods=["POST", "OPTIONS"])
def learn():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        items = request.get_json(force=True, silent=True) or {}
        if isinstance(items, dict):
            items = [items]
        added = 0
        with lock:
            for it in items:
                q, a = it.get("q"), it.get("a")
                if not q or a in (None, ""):
                    continue
                n = norm(q)
                if any(norm(x.get("q", "")) == n for x in BANK):
                    continue
                BANK.append({"q": q, "a": a, "t": time.strftime("%Y-%m-%d")})
                added += 1
            if added:
                save_bank()
        print(f"[tiku] 收录 {added} 条, 现有 {len(BANK)}")
        return jsonify({"code": 1, "added": added, "total": len(BANK)})
    except Exception as e:
        return jsonify({"code": -1, "message": str(e)})


@app.route("/bank", methods=["GET", "POST", "OPTIONS"])
def bank():
    if request.method == "OPTIONS":
        return ("", 204)
    if request.method == "GET":
        return jsonify(BANK)
    try:
        data = request.get_json(force=True, silent=True)
        if isinstance(data, dict):
            data = [{"q": k, "a": v} for k, v in data.items()]
        n = 0
        with lock:
            for it in data or []:
                q = it.get("q") or it.get("question")
                a = it.get("a") or it.get("answer")
                if q and a not in (None, ""):
                    BANK.append({"q": q, "a": a, "t": time.strftime("%Y-%m-%d")})
                    n += 1
            save_bank()
        return jsonify({"code": 1, "added": n, "total": len(BANK)})
    except Exception as e:
        return jsonify({"code": -1, "message": str(e)})


@app.route("/bank/delete", methods=["POST", "OPTIONS"])
def bank_delete():
    """按题干删除条目  json: {"q": "..."}  或 {"idx": 3}"""
    if request.method == "OPTIONS":
        return ("", 204)
    args = request.get_json(force=True, silent=True) or {}
    with lock:
        before = len(BANK)
        if "idx" in args:
            i = int(args["idx"])
            if 0 <= i < len(BANK):
                BANK.pop(i)
        elif args.get("q"):
            target = norm(args["q"])
            BANK[:] = [x for x in BANK if norm(x.get("q", "")) != target]
        if len(BANK) != before:
            save_bank()
    return jsonify({"code": 1, "total": len(BANK), "removed": before - len(BANK)})


@app.route("/llm/providers", methods=["GET"])
def llm_providers():
    """列出所有预设 + 每个平台是否已配 key"""
    profiles = CONF.get("llm_profiles") or {}
    out = []
    for k, v in LLM_PRESETS.items():
        prof = profiles.get(k) or {}
        out.append({
            "id": k,
            "label": v.get("label", k),
            "base": prof.get("base") or v.get("base", ""),
            "model": prof.get("model") or v.get("model", ""),
            "note": v.get("note", ""),
            "has_key": bool(prof.get("key")),
        })
    return jsonify({"current": CONF.get("llm_provider"), "providers": out})


@app.route("/llm/test", methods=["POST", "OPTIONS"])
def llm_test():
    """不落库地测连通性；可指定 provider，不传则测当前"""
    if request.method == "OPTIONS":
        return ("", 204)
    args = request.get_json(force=True, silent=True) or {}
    q = args.get("question") or "中华人民共和国的首都是哪座城市？"
    opts = args.get("options") or ["A.上海", "B.北京", "C.广州", "D.西安"]
    provider = (args.get("provider") or "").lower() or None
    t0 = time.time()

    try:
        ans, status = _call_one(provider or CONF.get("llm_provider"), q, opts, args.get("type") or "single", timeout=30)
    except Exception as e:
        ans, status = None, str(e)[:120]

    base, model, key, name = resolve_provider(provider)
    return jsonify({
        "ok": bool(ans),
        "status": status,
        "provider": name,
        "label": (LLM_PRESETS.get(name) or {}).get("label", name),
        "base": base,
        "model": model,
        "key_set": bool(key),
        "answer": ans or "",
        "cost": round(time.time() - t0, 2),
    })


@app.route("/llm/profile", methods=["POST", "OPTIONS"])
def llm_profile():
    """保存单个平台的配置并切换到它"""
    if request.method == "OPTIONS":
        return ("", 204)
    global CONF
    args = request.get_json(force=True, silent=True) or {}
    pid = (args.get("provider") or "").lower()
    if not pid:
        return jsonify({"code": -1, "message": "provider 为空"})

    profiles = CONF.get("llm_profiles") or {}
    prof = profiles.get(pid) or {}
    for k in ("base", "model"):
        if k in args:
            prof[k] = (args.get(k) or "").strip()
    if args.get("key"):
        prof["key"] = args["key"].strip()
    profiles[pid] = prof
    CONF["llm_profiles"] = profiles
    CONF["llm_provider"] = pid
    if args.get("enabled") is not None:
        CONF["llm_enabled"] = bool(args["enabled"])
    if prof.get("key"):
        CONF["llm_enabled"] = True

    CONF_FILE.write_text(json.dumps(CONF, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"code": 1, "provider": pid, "key_set": bool(prof.get("key"))})


@app.route("/llm/delete", methods=["POST", "OPTIONS"])
def llm_delete():
    if request.method == "OPTIONS":
        return ("", 204)
    global CONF
    args = request.get_json(force=True, silent=True) or {}
    pid = (args.get("provider") or "").lower()
    profiles = CONF.get("llm_profiles") or {}
    if pid in profiles:
        profiles.pop(pid)
        CONF["llm_profiles"] = profiles
        CONF_FILE.write_text(json.dumps(CONF, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"code": 1, "removed": pid})


@app.route("/config", methods=["GET", "POST", "OPTIONS"])
def config():
    if request.method == "OPTIONS":
        return ("", 204)
    global CONF
    if request.method == "POST":
        patch = request.get_json(force=True, silent=True) or {}
        editable = ("fuzzy", "llm_enabled", "llm_provider", "llm_base",
                    "llm_model", "llm_cache", "llm_first", "llm_fallback")
        for k in editable:
            if k in patch:
                CONF[k] = patch[k]
        if patch.get("llm_key"):
            CONF["llm_key"] = patch["llm_key"]
            CONF["llm_enabled"] = True
        CONF_FILE.write_text(json.dumps(CONF, ensure_ascii=False, indent=2), encoding="utf-8")

    safe = {k: v for k, v in CONF.items() if k != "llm_key"}
    safe["llm_key_set"] = bool(CONF.get("llm_key"))
    safe["presets"] = LLM_PRESETS
    # profiles 里的 key 不外泄
    profs = {}
    for k, v in (CONF.get("llm_profiles") or {}).items():
        profs[k] = {"base": v.get("base", ""), "model": v.get("model", ""), "has_key": bool(v.get("key"))}
    safe["llm_profiles"] = profs
    return jsonify(safe)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>学习通题库控制台</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--dim:#8b98a5;--acc:#1f6feb;--ok:#3fb950;--warn:#d29922}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
  header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  h1{font-size:15px;margin:0;font-weight:600}
  .badge{font-size:12px;padding:1px 8px;border-radius:10px;background:#21262d;color:var(--dim)}
  .badge.ok{color:var(--ok)}
  main{max-width:1000px;margin:0 auto;padding:18px 20px 60px}
  .row{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
  input,textarea,button{font:13px inherit;background:#0d1117;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
  input:focus,textarea:focus{outline:none;border-color:var(--acc)}
  button{background:#21262d;cursor:pointer}
  button:hover{border-color:#484f58}
  button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--dim);font-weight:500;position:sticky;top:0;background:var(--bg)}
  td.q{max-width:560px;word-break:break-word}
  td.a{color:var(--ok);white-space:pre-wrap;max-width:260px}
  .del{color:#f85149;cursor:pointer;font-size:12px}
  .muted{color:var(--dim);font-size:12px}
  .box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:16px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:760px){.grid{grid-template-columns:1fr}}
  #toast{position:fixed;right:18px;bottom:18px;background:#21262d;border:1px solid var(--line);
    padding:9px 14px;border-radius:8px;opacity:0;transition:.25s;pointer-events:none;font-size:13px}
  #toast.show{opacity:1}
</style></head><body>
<header>
  <h1>学习通题库控制台</h1>
  <span class="badge" id="stat">加载中…</span>
  <span class="badge" id="port"></span>
</header>
<main>
  <div class="box grid">
    <div>
      <div class="muted" style="margin-bottom:6px">搜题测试</div>
      <div class="row">
        <input id="q" style="flex:1" placeholder="粘贴题干，回车测试命中">
        <button class="primary" onclick="test()">测试</button>
      </div>
      <div id="testOut" class="muted">未测试</div>
    </div>
    <div>
      <div class="muted" style="margin-bottom:6px">参数</div>
      <div class="row">
        <label class="muted">相似度阈值 <input id="fuzzy" type="number" step="0.05" min="0.2" max="0.95" style="width:80px"></label>
        <button onclick="saveFuzzy()">保存</button>
      </div>
      <div class="muted">低于阈值算未命中。0.5 平衡，0.35 放宽，0.75 严格。</div>
    </div>
  </div>

  <div class="box">
    <div class="muted" style="margin-bottom:8px">大模型答题 · 多平台</div>

    <div class="row" id="provList" style="gap:6px"></div>

    <div class="grid" style="margin-top:10px">
      <div>
        <div class="row">
          <label class="muted" style="width:52px">模型</label>
          <input id="llmModel" style="flex:1" placeholder="留空用推荐值">
        </div>
        <div class="row">
          <label class="muted" style="width:52px">Base</label>
          <input id="llmBase" style="flex:1" placeholder="留空用预设地址">
        </div>
        <div class="row">
          <label class="muted" style="width:52px">Key</label>
          <input id="llmKey" type="password" style="flex:1" placeholder="已设置则留空不修改">
        </div>
        <div class="row">
          <button class="primary" onclick="saveLLM()">保存并启用</button>
          <button onclick="testLLM()">测试连通</button>
          <button onclick="delLLM()" style="color:#f85149">清除此平台</button>
        </div>
        <div class="row" style="margin-bottom:6px">
          <label class="muted"><input type="checkbox" id="llmEnabled"> 启用大模型</label>
          <label class="muted"><input type="checkbox" id="llmCache"> 结果入库</label>
          <label class="muted"><input type="checkbox" id="llmFallback"> 失败自动切换</label>
        </div>
        <div id="llmOut" class="muted">未测试</div>
      </div>
      <div class="muted" style="font-size:12px;line-height:1.85" id="provNote">
        选一个平台，填 Key，点保存。
      </div>
    </div>
  </div>

  <div class="box">
    <div class="muted" style="margin-bottom:6px">批量导入（JSON 数组 / 键值对象）</div>
    <textarea id="imp" rows="5" style="width:100%" placeholder='[{"q":"题干","a":"A"},{"q":"题干2","a":"对"}]'></textarea>
    <div class="row" style="margin-top:8px">
      <button class="primary" onclick="doImport()">导入</button>
      <button onclick="location.href='/bank'">导出全部</button>
      <button onclick="alert('把 /bank 返回的内容另存为 bank.json 即可备份')">备份说明</button>
    </div>
  </div>

  <div class="box">
    <div class="muted" style="margin-bottom:6px">OCS 网课助手 · 题库配置（AnswererWrapper）</div>
    <div class="row">
      <button class="primary" onclick="copyOCS()">复制配置到剪贴板</button>
      <label class="muted"><input type="checkbox" id="ocsMiss" onchange="loadOCS()"> 未命中时显示提示行</label>
      <span class="muted" id="ocsOut"></span>
    </div>
    <textarea id="ocsCfg" rows="14" style="width:100%;font-family:ui-monospace,Consolas,monospace;font-size:12px" readonly></textarea>
    <div class="muted" style="margin-top:6px">
      OCS → 通用 → 全局设置 → 题库配置 → 粘贴上面的 JSON → 保存。
      本机服务只监听 127.0.0.1，配置里的 type 是 <code>GM_xmlhttpRequest</code>，
      所以不会被 https 页面的跨域/混合内容拦住。
    </div>
  </div>

  <div class="row">
    <input id="filter" style="flex:1" placeholder="在题库里过滤…" oninput="render()">
    <span class="badge" id="count"></span>
  </div>
  <table><thead><tr><th style="width:50%">题干</th><th>答案</th><th style="width:60px"></th></tr></thead>
  <tbody id="tb"></tbody></table>
</main>
<div id="toast"></div>
<script>
let BANK = [];
const $ = s => document.querySelector(s);
let PROV = [];
let CUR = 'doubao';
function toast(t){ const e=$('#toast'); e.textContent=t; e.classList.add('show'); setTimeout(()=>e.classList.remove('show'),1600); }
async function load(){
  BANK = await (await fetch('/bank')).json();
  const c = await (await fetch('/config')).json();
  $('#fuzzy').value = c.fuzzy;
  $('#port').textContent = '127.0.0.1:' + c.port;
  $('#stat').textContent = '题库 ' + BANK.length + ' 条';
  $('#stat').className = 'badge ok';
  CUR = c.llm_provider || 'doubao';
  $('#llmEnabled').checked = !!c.llm_enabled;
  $('#llmCache').checked = c.llm_cache !== false;
  $('#llmFallback').checked = c.llm_fallback !== false;
  await loadProviders();
  pickProvider(CUR);
  render();
}
async function loadProviders(){
  const r = await (await fetch('/llm/providers')).json();
  PROV = r.providers || [];
  $('#provList').innerHTML = PROV.map(p => {
    const on = p.id === CUR;
    const keyed = p.has_key;
    return `<button onclick="pickProvider('${p.id}')"
      style="background:${on?'#1f6feb':'#21262d'};border-color:${on?'#1f6feb':'#30363d'};
      color:${on?'#fff':'#e6edf3'};padding:4px 10px;font-size:12px">
      ${esc(p.label)}${keyed?' <span style="color:#3fb950">●</span>':''}</button>`;
  }).join('');
}
function pickProvider(id){
  CUR = id;
  const p = PROV.find(x => x.id === id) || {};
  $('#llmModel').value = p.model || '';
  $('#llmBase').value = p.base || '';
  $('#llmModel').placeholder = p.model || '留空用推荐值';
  $('#llmBase').placeholder = p.base || '留空用预设地址';
  $('#provNote').innerHTML = `<div style="color:#e6edf3;margin-bottom:4px">${esc(p.label||'')}</div>`
    + (p.note ? esc(p.note) + '<br>' : '')
    + (p.has_key ? '<span style="color:#3fb950">已配置 Key</span><br>' : '<span style="color:#d29922">未配置 Key</span><br>')
    + '<div style="margin-top:8px;color:#8b98a5">命中顺序：本地题库 → 学小易 → 大模型。'
    + '大模型结果默认入库，下次直接命中。勾选「失败自动切换」后，当前平台失败会自动试其他已配 Key 的平台。</div>';
  loadProviders();
}
async function saveLLM(){
  const body = {
    provider: CUR,
    model: $('#llmModel').value.trim(),
    base: $('#llmBase').value.trim(),
    enabled: $('#llmEnabled').checked
  };
  const k = $('#llmKey').value.trim();
  if(k) body.key = k;
  await fetch('/llm/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({llm_enabled: $('#llmEnabled').checked, llm_cache: $('#llmCache').checked,
                         llm_fallback: $('#llmFallback').checked, llm_provider: CUR})});
  $('#llmKey').value = '';
  toast(k ? '已保存并启用' : '已保存');
  load();
}
async function delLLM(){
  if(!confirm('清除 ' + CUR + ' 的配置和 Key？')) return;
  await fetch('/llm/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:CUR})});
  toast('已清除'); load();
}
async function testLLM(){
  $('#llmOut').textContent = '请求中…';
  const r = await fetch('/llm/test',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({provider:CUR, question:'中华人民共和国的首都是哪座城市？',
      options:['A.上海','B.北京','C.广州','D.西安'],type:'single'})});
  const j = await r.json();
  if(j.ok){
    $('#llmOut').innerHTML = `<span style="color:var(--ok)">连通</span> → <b>${esc(j.answer)}</b>
      <span class="muted">（${esc(j.label)} / ${esc(j.model)} / ${j.cost}s）</span>`;
  }else{
    const why = {
      'no-key':'还没填 Key',
      'no-base':'缺少 Base 地址',
      'http-401':'Key 无效',
      'http-403':'无权限或未开通',
      'http-404':'模型名或 Base 不对',
      'http-429':'额度用完或限流',
      'empty':'返回为空'
    }[j.status] || j.status;
    $('#llmOut').innerHTML = `<span style="color:var(--warn)">失败</span>
      <span class="muted">${esc(why)}（${esc(j.label)} / ${esc(j.model||'')}）</span>`;
  }
}
function render(){
  const k = ($('#filter').value||'').trim();
  const list = k ? BANK.filter(x => (x.q||'').includes(k) || String(x.a||'').includes(k)) : BANK;
  $('#count').textContent = list.length + ' / ' + BANK.length;
  $('#tb').innerHTML = list.slice(0, 500).map(x => {
    const i = BANK.indexOf(x);
    return `<tr><td class="q">${esc(x.q)}</td><td class="a">${esc(x.a)}</td>
      <td><span class="del" onclick="del(${i})">删除</span></td></tr>`;
  }).join('') || '<tr><td colspan="3" class="muted">题库为空，先导入或去答案页按 Alt+Shift+S 收录</td></tr>';
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
async function test(){
  const q = $('#q').value.trim(); if(!q) return;
  const r = await fetch('/v1/cx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
  const j = await r.json();
  $('#testOut').innerHTML = j.data
    ? `<span style="color:var(--ok)">命中</span> → <b>${esc(j.data)}</b>
       <span class="muted">（${j.source}，相似度 ${j.score}，${j.cost}s）</span>`
    : `<span style="color:var(--warn)">未命中</span> <span class="muted">（${j.cost}s）</span>`;
  load();
}
async function doImport(){
  let t = $('#imp').value.trim(); if(!t) return;
  try { JSON.parse(t); } catch(e){ toast('JSON 格式错误'); return; }
  const r = await fetch('/bank',{method:'POST',headers:{'Content-Type':'application/json'},body:t});
  const j = await r.json();
  toast('导入 ' + (j.added||0) + ' 条');
  $('#imp').value = ''; load();
}
async function del(i){
  const q = BANK[i].q;
  await fetch('/bank/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
  toast('已删除'); load();
}
async function saveFuzzy(){
  const v = parseFloat($('#fuzzy').value) || 0.5;
  await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fuzzy:v})});
  toast('阈值 → ' + v);
}
async function loadOCS(){
  const miss = $('#ocsMiss').checked ? '?miss=1' : '';
  const t = await (await fetch('/ocs/config' + miss)).text();
  $('#ocsCfg').value = t;
  $('#ocsOut').textContent = t.length + ' 字符';
}
async function copyOCS(){
  const t = $('#ocsCfg').value || '';
  if(!t){ toast('先加载配置'); return; }
  try {
    await navigator.clipboard.writeText(t);
    toast('已复制，去 OCS 题库配置里粘贴');
  } catch(e) {
    $('#ocsCfg').select(); document.execCommand('copy');
    toast('已复制（兼容模式）');
  }
}
$('#q').addEventListener('keydown', e => { if(e.key === 'Enter') test(); });
load();
loadOCS();
</script></body></html>"""


@app.route("/")
def index():
    return INDEX_HTML


# ================= StarX / tikuAdapter 协议适配层 =================
# StarX 支持四种源：自动识别 / LemTk / Adapter / 自定义 GET·POST 模板。
# 这里把上面那份 bank.json 同时以三种协议吐出去，四种模式任意填都能用。

_JUDGE = {
    "对": 0, "正确": 0, "√": 0, "是": 0, "t": 0, "true": 0,
    "错": 1, "错误": 1, "×": 1, "否": 1, "f": 1, "false": 1,
}


def _pack(keys, idx, texts, raw=""):
    uniq = lambda xs: [x for i, x in enumerate(xs) if x not in xs[:i]]
    keys, idx, texts = uniq(keys), uniq(idx), uniq(texts)
    return {
        "answerKey": keys,
        "answerKeyText": "".join(keys) if keys else (raw or ""),
        "answerIndex": idx,
        "answerText": "#".join(texts),
        "bestAnswer": texts,
        "allAnswer": [texts] if texts else [],
    }


def _answer_struct(a, options):
    """把 bank.json 里那条 'a' 转成 tikuAdapter 标准 answer 结构"""
    a = str(a or "").strip()
    if not a:
        return _pack([], [], [])
    opts = [str(o) for o in (options or [])]

    # 1) 纯字母串 ACD
    if re.fullmatch(r"[A-Za-z]{1,8}", a):
        keys = list(a.upper())
        idx = [ord(c) - 65 for c in keys if 0 <= ord(c) - 65 < len(opts)]
        texts = [opts[i] for i in idx]
        if idx:
            return _pack(keys, idx, texts)
        return _pack(keys, [], [])

    # 2) 判断题：对/错 → 在选项里定位
    j = _JUDGE.get(norm(a))
    if j is not None and opts and j < len(opts):
        return _pack([chr(65 + j)], [j], [opts[j]])

    # 3) 答案文本反查选项
    an = norm(a)
    for i, o in enumerate(opts):
        on = norm(o)
        if on and (on == an or (len(an) >= 2 and (an in on or on in an))):
            return _pack([chr(65 + i)], [i], [opts[i]])

    # 4) 多答案文本 A#B#C
    parts = [x for x in re.split(r"[#|、,，;；]+", a) if x.strip()]
    if len(parts) > 1:
        keys, idx, texts = [], [], []
        for p in parts:
            pn = norm(p)
            for i, o in enumerate(opts):
                on = norm(o)
                if on and (on == pn or pn in on or on in pn):
                    keys.append(chr(65 + i))
                    idx.append(i)
                    texts.append(opts[i])
                    break
            else:
                texts.append(p)
        if keys:
            return _pack(keys, idx, texts)
        return _pack([], [], parts)

    # 5) 填空/简答：原样返回
    return _pack([], [], [a])


_TYPE_IN = {"single": 0, "multi": 1, "blank": 2, "judge": 3, "qa": 4,
            "单选": 0, "多选": 1, "填空": 2, "判断": 3, "问答": 4}


def _to_type(v):
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v if 0 <= v <= 4 else 0
    s = str(v or "").strip().lower()
    if s.isdigit():
        n = int(s)
        return n if 0 <= n <= 4 else 0
    return _TYPE_IN.get(s, 0)


def _read_starx_payload():
    """StarX 的 LemTk/Adapter 都发 JSON；GET 模板走 query，这里统一收口"""
    p = {}
    if request.method == "GET":
        p = request.args.to_dict(flat=True)
    elif request.is_json:
        p = request.get_json(silent=True) or {}
    else:
        p = request.form.to_dict(flat=True)
    q = (p.get("question") or p.get("title") or p.get("text")
         or p.get("q") or p.get("keyword") or "").strip()
    q = re.sub(r"【(单选题|多选题|判断题|填空题|简答题)】", "", q).strip()
    options = p.get("options") or p.get("option") or []
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except Exception:
            options = [x.strip() for x in re.split(r"[|\n\r]+", options) if x.strip()]
    options = [str(x) for x in (options or []) if str(x).strip()]
    return q, options, _to_type(p.get("type", 0))


def _starx_payload():
    q, options, qtype = _read_starx_payload()
    t0 = time.time()
    if not q:
        empty = _pack([], [], [])
        return {
            "code": 400, "msg": "question 为空", "plat": 0,
            "question": "", "options": options, "type": qtype,
            "answer": empty, "data": empty, "cost": 0,
        }

    ans, score, src = "", 0.0, "未命中"
    with lock:
        got = bank_lookup(q)
    if got:
        ans, score = got
        src = "本地"
    else:
        ans = xxy_lookup(q) or llm_lookup(q, options, qtype) or ""
        if ans:
            src = "大模型" if not ans or True else "未命中"

    struct = _answer_struct(ans, options)
    hit = bool(struct["bestAnswer"] or struct["answerKeyText"])

    if hit:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} [{src}/StarX] {q[:40]} -> {struct['answerKeyText'][:40]}\n")
        except Exception:
            pass

    body = {
        "plat": 0,
        "question": q,
        "options": options,
        "type": qtype,
        "answer": struct,
        "code": 200 if hit else 404,
        "msg": "ok" if hit else "未命中题库",
        "hit": hit,
        "score": round(float(score), 3) if score else 0.0,
        "source": src,
        "cost": round((time.time() - t0) * 1000, 1),
        # LemTk 常见扁平字段
        "answerKey": struct["answerKey"],
        "answerKeyText": struct["answerKeyText"],
        "answerText": struct["answerText"],
        "bestAnswer": struct["bestAnswer"],
    }
    body["data"] = dict(body, **{"answer": struct})
    return body


@app.route("/adapter-service/search", methods=["POST", "GET", "OPTIONS"])
def starx_adapter():
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(_starx_payload())


@app.route("/api/search", methods=["POST", "GET", "OPTIONS"])
def starx_api_search():
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(_starx_payload())


@app.route("/search", methods=["POST", "GET", "OPTIONS"])
def starx_search():
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(_starx_payload())


@app.route("/api/v1/search", methods=["POST", "GET", "OPTIONS"])
def starx_api_v1_search():
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(_starx_payload())


@app.route("/", methods=["POST", "OPTIONS"])
def starx_root():
    """LemTk 模式：根地址直接 POST，StarX 会把根地址原样用"""
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(_starx_payload())


@app.route("/stats")
def stats():
    return jsonify({"bank": len(BANK), "fuzzy": CONF.get("fuzzy"), "port": CONF.get("port")})


# ================= OCS 网课助手 · AnswererWrapper 原生适配 =================
# OCS 的 handler 契约（源码 answer.wrapper.handler.ts）：
#   handler 用 Function(str)() 构造，必须返回一个函数，入参是请求响应体，
#   出参是 [题目, 答案] 或 [[题目,答案], ...]，搜不到返回 undefined。
# 多选答案必须拼成一个字符串（OCS 会用 #;| 等分隔符再 splitAnswer 拆开），
# 绝对不能返回数组。

def _ocs_type(v):
    """OCS 题型 -> 本地题型名"""
    s = str(v or "").strip().lower()
    return {
        "0": "single", "1": "multiple", "2": "completion", "3": "judgement", "4": "completion",
        "single": "single", "multiple": "multiple", "multi": "multiple",
        "judgement": "judgement", "judge": "judgement",
        "completion": "completion", "blank": "completion", "essay": "completion",
        "单选": "single", "多选": "multiple", "判断": "judgement", "填空": "completion",
    }.get(s, "single")


def _ocs_answer(ans, qtype, options=None):
    """把库里的答案压成 OCS 能吃的字符串（多选必须合成一个串，OCS 自己按 # 拆）

    options 参与判断题的字母反查：库里存 "A"/"B" 时，要按选项文本还原成 对/错，
    否则 OCS 的 resolveJudgement（只认 对/错/正确/错误/√/×…）匹配不上。
    """
    opts = [str(o) for o in (options or [])]

    if isinstance(ans, (list, tuple)):
        parts = [str(x).strip() for x in ans if str(x).strip()]
        if qtype == "multiple":
            letters = [p.upper() for p in parts if re.fullmatch(r"[A-Ha-h]", p)]
            if letters and len(letters) == len(parts):
                return "".join(sorted(set(letters)))
            return "#".join(parts)
        if qtype == "completion":
            return " ".join(parts)
        ans = parts[0] if parts else ""

    a = str(ans if ans is not None else "").strip()
    if not a:
        return ""

    if qtype == "multiple":
        # ACD / A,C,D / A#C#D / ["A","C"] -> ACD（OCS 的纯字母兜底只吃连续字母）
        parts = [p.strip() for p in re.split(r"[#|、,，;；\s]+", a) if p.strip()]
        if parts and all(re.fullmatch(r"[A-Ha-h]", p) for p in parts):
            return "".join(sorted({p.upper() for p in parts}))
        m = re.fullmatch(r"[\[\(]?([A-Ha-h][,，、\s]*)+[\]\)]?", a)
        letters = re.findall(r"[A-Ha-h]", a) if m else []
        if letters:
            return "".join(sorted({c.upper() for c in letters}))
        return "#".join(parts)

    if qtype == "judgement":
        j = _JUDGE.get(norm(a))
        if j is None and re.fullmatch(r"[A-Ha-h]", a) and opts:
            # 库里存的是选项字母，用选项文本判断它代表 对 还是 错
            i = ord(a.upper()) - 65
            if 0 <= i < len(opts):
                j = _JUDGE.get(norm(opts[i]))
        if j == 0:
            return "对"
        if j == 1:
            return "错"

    return a


def _ocs_env():
    """统一取 OCS 的 env: title / options / type"""
    if request.method == "GET":
        p = request.args.to_dict(flat=True)
        raw_opts = p.get("options") or p.get("option") or ""
    elif request.is_json:
        p = request.get_json(silent=True) or {}
        raw_opts = p.get("options") or p.get("option") or ""
    else:
        p = request.form.to_dict(flat=True)
        raw_opts = p.get("options") or ""

    title = (p.get("title") or p.get("question") or p.get("q") or "").strip()
    title = re.sub(r"【(单选题|多选题|判断题|填空题|简答题)】", "", title).strip()

    options = raw_opts
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except Exception:
            options = [x for x in re.split(r"[|\n\r]+", options) if x.strip()]
    options = [str(x).strip() for x in (options or []) if str(x).strip()]

    return title, options, _ocs_type(p.get("type"))


@app.route("/ocs", methods=["POST", "GET", "OPTIONS"])
@app.route("/ocs/search", methods=["POST", "GET", "OPTIONS"])
def ocs_search():
    """给 OCS AnswererWrapper 用的接口：返回 {code, question, answer, source, score}"""
    if request.method == "OPTIONS":
        return ("", 204)

    q, options, qtype = _ocs_env()
    t0 = time.time()
    if not q:
        return jsonify({"code": 0, "message": "title 为空", "question": "", "answer": "",
                        "hit": False, "source": "未命中", "score": 0.0})

    ans, src, score = "", "未命中", 0.0
    with lock:
        got = bank_lookup(q)
    if got:
        ans, score = got
        src = "本地"
    else:
        ans = xxy_lookup(q) or llm_lookup(q, options, qtype) or ""
        src = "大模型" if ans else "未命中"
        if ans and CONF.get("llm_cache", True):
            with lock:
                BANK.append({"q": q, "a": ans, "t": time.strftime("%Y-%m-%d")})
                save_bank()

    answer = _ocs_answer(ans, qtype, options)
    line = f"{time.strftime('%H:%M:%S')} [OCS/{src}] {q[:40]} -> {answer[:40]}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    return jsonify({
        "code": 1 if answer else 0,
        "message": "" if answer else "未命中",
        "question": q,
        "answer": answer,
        "hit": bool(answer),
        "score": round(float(score), 3) if score else 0.0,
        "source": src,
        "cost": round(time.time() - t0, 3),
    })


# OCS 的 handler 必须是字符串形式的函数体；这里集中维护，改逻辑只改这一处
_OCS_HANDLER = (
    "return (res) => {"
    " if (!res || res.code !== 1 || !res.answer) return undefined;"
    " return [res.question || undefined, res.answer];"
    "}"
)

_OCS_HANDLER_DEBUG = (
    "return (res) => {"
    " if (!res) return undefined;"
    " if (res.code !== 1 || !res.answer) return [res.message || res.source || '未命中', undefined];"
    " return [res.question || undefined, res.answer];"
    "}"
)


@app.route("/ocs/config", methods=["GET", "OPTIONS"])
def ocs_config():
    """直接吐出可粘贴到 OCS「题库配置」的 AnswererWrapper 数组"""
    if request.method == "OPTIONS":
        return ("", 204)
    host = request.args.get("host") or "127.0.0.1"
    port = request.args.get("port") or CONF.get("port", 8890)
    base = f"http://{host}:{port}"
    show_miss = request.args.get("miss") in ("1", "true", "yes")

    body = [{
        "name": "本地题库(8890)",
        "homepage": base + "/",
        "url": base + "/ocs",
        "method": "post",
        "contentType": "json",
        "type": "GM_xmlhttpRequest",
        "headers": {"Content-Type": "application/json"},
        "data": {
            "title": "${title}",
            "options": {"handler": "return (env)=> (env.options || '').split('\\n').filter(Boolean)"},
            "type": {"handler": "return (env)=> env.type || ''"},
        },
        "handler": _OCS_HANDLER_DEBUG if show_miss else _OCS_HANDLER,
    }]
    text = json.dumps(body, ensure_ascii=False, indent=2)
    resp = app.response_class(text, mimetype="application/json")
    resp.headers["Content-Disposition"] = 'inline; filename="ocs-answerer-wrapper.json"'
    return resp


@app.route("/health")
def health():
    return jsonify({"ok": True, "bank": len(BANK)})


def port_busy(host, port):
    """Windows 允许重复 bind 同一端口，必须自己先探测，否则两个实例会抢请求"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def main():
    """启动服务。打包后由 GUI 以 --serve 方式调用，所以单独抽成函数。"""
    host = CONF.get("host", "127.0.0.1")
    port = int(CONF.get("port", 8890))

    if port_busy(host, port):
        print(f"""
  [启动失败] {host}:{port} 已经被占用了。

  可能原因：
    1. 已经开着一个本服务的窗口（去任务栏找找，别重复双击）
    2. 别的程序占了这个端口

  处理：
    - 关掉多余的黑窗口，或
    - 改 config.json 里的 port（比如 8891），
      同时把浏览器脚本面板里的接口地址改成同一个端口
""")
        sys.exit(1)

    print(f"""
  学习通题库服务
  -----------------------------------------
  地址   http://{host}:{port}
  题库   {len(BANK)} 条  ({BANK_FILE.name})
  相似度 {CONF.get('fuzzy')}
  学小易 {'已配 open_id' if CONF.get('xxy_open_id') else '未配置(留空跳过)'}
  LLM    {'已启用 ' + str(CONF.get('llm_model')) if CONF.get('llm_enabled') else '未启用'}
  -----------------------------------------
  脚本面板填:  http://{host}:{port}/v1/cx
  控制台:     http://{host}:{port}/
""")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
