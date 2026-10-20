#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""豆包客户端 CDP 问答模块：发题 -> 取回答"""
import asyncio, json, sys, time, urllib.request
import websockets

CDP = "http://127.0.0.1:9334"


def pick_target():
    with urllib.request.urlopen(CDP + "/json/list", timeout=6) as r:
        ts = json.load(r)
    for t in ts:
        if t.get("type") == "page" and "doubao-chat/chat" in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    for t in ts:
        if t.get("type") == "page" and "doubao" in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    raise RuntimeError("豆包聊天页 target 不存在，客户端可能没开 CDP")


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0

    async def call(self, method, **params):
        self.i += 1
        mid = self.i
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr, await_promise=False):
        r = await self.call("Runtime.evaluate",
                            expression=expr, returnByValue=True,
                            awaitPromise=await_promise)
        res = r.get("result", {})
        if res.get("subtype") == "error":
            return None
        return res.get("value")


JS_FOCUS = r"""
(() => {
  const el = document.querySelector('div.tiptap.ProseMirror, [contenteditable="true"]');
  if (!el) return 'NO_EDITOR';
  el.focus();
  const sel = window.getSelection();
  const r = document.createRange();
  r.selectNodeContents(el); r.collapse(false);
  sel.removeAllRanges(); sel.addRange(r);
  return 'OK';
})()
"""

JS_NEW_CHAT = r"""
(() => {
  const b = document.querySelector('[data-testid="create_conversation_button"]');
  if (!b) return 'NO_BTN';
  b.click(); return 'CLICKED';
})()
"""

JS_SEND_BTN = r"""
(() => {
  // 只认输入区里的发送按钮，绝不能点 message_* 气泡
  const bad = /^(send_message|message_|receive_message|union_message)/;
  const cands = [
    ...document.querySelectorAll('[data-testid="chat_input_send_button"]'),
    ...document.querySelectorAll('[data-testid="chat_input"] button, [data-testid="chat_input"] [role="button"]'),
    ...document.querySelectorAll('button[data-testid*="send"]'),
    ...document.querySelectorAll('button')
  ];
  for (const b of cands) {
    const tid = b.getAttribute('data-testid') || '';
    if (bad.test(tid)) continue;
    if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
    const cls = (typeof b.className === 'string') ? b.className : '';
    const sig = (tid + ' ' + cls + ' ' + (b.innerText || '')).toLowerCase();
    if (/send|发送/.test(sig)) { b.click(); return 'CLICKED:' + (tid || b.tagName); }
  }
  return 'NO_BTN';
})()
"""

# 用户消息条数，用来确认「真的发出去了」
JS_USER_COUNT = r"""
(() => {
  const list = document.querySelector('[data-testid="message-list"]');
  if (!list) return '0';
  return String(list.querySelectorAll('[data-testid="send_message"]').length);
})()
"""

JS_EDITOR_EMPTY = r"""
(() => {
  const el = document.querySelector('div.tiptap.ProseMirror, [contenteditable="true"]');
  if (!el) return 'NO';
  return (el.innerText || '').trim().length === 0 ? 'EMPTY' : 'HAS_TEXT';
})()
"""

# 精确读取「最后一条助手消息」：只认 receive_message 气泡内的正文
JS_LAST_ANSWER = r"""
(() => {
  const list = document.querySelector('[data-testid="message-list"]');
  if (!list) return JSON.stringify({ok: false, n: 0, text: '', streaming: false});
  const recv = [...list.querySelectorAll('[data-testid="receive_message"]')];
  if (!recv.length) return JSON.stringify({ok: false, n: 0, text: '', streaming: false});
  const last = recv[recv.length - 1];
  const tc = last.querySelector('[data-testid="message_text_content"]');
  const text = ((tc ? tc.innerText : last.innerText) || '').trim();
  const body = document.body.innerText || '';
  const streaming = /停止生成|正在生成|生成中/.test(body)
    || !!last.querySelector('[data-testid*="stop"], [class*="loading"], [class*="generating"]');
  return JSON.stringify({ok: true, n: recv.length, text: text, streaming: streaming});
})()
"""


async def ask(question: str, timeout: float = 120.0, new_chat: bool = False, debug=False) -> dict:
    """发送题目并等待豆包的**新**回复（用基线 diff，避免读到上一轮残留）"""
    t0 = time.time()
    ws_url = pick_target()
    async with websockets.connect(ws_url, max_size=40 * 1024 * 1024) as ws:
        s = Session(ws)
        await s.call("Runtime.enable")
        await s.call("Page.enable")

        if new_chat:
            await s.js(JS_NEW_CHAT)
            await asyncio.sleep(1.5)

        # 基线：发送前最后一条助手消息
        base = json.loads(await s.js(JS_LAST_ANSWER) or "{}")
        base_n = base.get("n", 0)
        base_text = base.get("text", "")
        if debug:
            print(f"[base] n={base_n} text={base_text[:40]!r}", file=sys.stderr)

        st = await s.js(JS_FOCUS)
        if st != "OK":
            raise RuntimeError(f"找不到输入框: {st}")

        await s.call("Input.insertText", text=question)
        await asyncio.sleep(0.6)

        before_users = int(await s.js(JS_USER_COUNT) or 0)

        async def _enter():
            await s.call("Input.dispatchKeyEvent", type="keyDown", key="Enter",
                         code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
            await s.call("Input.dispatchKeyEvent", type="keyUp", key="Enter",
                         code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)

        async def _user_count():
            return int(await s.js(JS_USER_COUNT) or 0)

        sent = await s.js(JS_SEND_BTN)
        await asyncio.sleep(2.5)
        if await _user_count() <= before_users:
            # 点按钮没生效，退回回车发送
            if debug:
                print(f"[send] {sent} 无效，改用 Enter", file=sys.stderr)
            await s.js(JS_FOCUS)
            await _enter()
            await asyncio.sleep(2.5)
            sent = "ENTER"
        if await _user_count() <= before_users:
            # 输入框可能没吃到文本，重填一次再回车
            left = await s.js(JS_EDITOR_EMPTY)
            if left != "HAS_TEXT":
                await s.js(JS_FOCUS)
                await s.call("Input.insertText", text=question)
                await asyncio.sleep(0.5)
            await s.js(JS_FOCUS)
            await _enter()
            await asyncio.sleep(3.0)
            sent = "ENTER-RETRY"
        if await _user_count() <= before_users:
            raise RuntimeError("消息没发出去（用户消息条数未增加）")
        if debug:
            print(f"[send] {sent} users {before_users}->{await _user_count()}", file=sys.stderr)

        # 等待「新的助手消息」出现并稳定
        answer = ""
        last_seen = None          # 只跟踪新消息的内容，不与旧基线混淆
        stable_since = None
        seen_new = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.8)
            raw = await s.js(JS_LAST_ANSWER)
            if not raw:
                continue
            d = json.loads(raw)
            n, txt, streaming = d.get("n", 0), (d.get("text") or "").strip(), d.get("streaming")
            # 新消息 = 助手消息条数变多，或最后一条内容与基线不同
            fresh = (n > base_n) or (txt and txt != base_text)
            if debug:
                print(f"[poll] n={n} len={len(txt)} fresh={fresh} streaming={streaming}", file=sys.stderr)
            if not fresh:
                continue
            seen_new = True
            if not txt:
                continue           # 刚发出、还没吐字
            if last_seen is None or txt != last_seen:
                last_seen, stable_since = txt, time.time()
            elif not streaming and stable_since and time.time() - stable_since > 2.0:
                answer = txt
                break
        if not answer and seen_new and last_seen:
            answer = last_seen
        return {
            "ok": bool(answer) and seen_new,
            "answer": answer,
            "cost": round(time.time() - t0, 1),
            "send_mode": sent,
            "new_msg": seen_new,
        }


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "1+1等于几？只回答数字"
    r = asyncio.run(ask(q, debug=True))
    print(json.dumps(r, ensure_ascii=False, indent=2))
