# OCS 网课助手 × 本地题库 · 对接说明

一句话：**把 OCS 的「题库配置」指向本机 8890，粘贴一段 JSON 就完事。**
不需要买题库、不需要 @connect 域名白名单、不需要改脚本源码。

---

## 一、三步接入

### 1. 起服务

```powershell
cd tiku
python tiku_server.py
```

看到这行就是好了：

```
地址   http://0.0.0.0:8890
题库   13 条  (bank.json)
```

不想开黑窗口就双击 `start_tiku.bat`。浏览器打开 <http://127.0.0.1:8890> 是控制台，
页面上有一块 **「OCS 网课助手 · 题库配置」**，点「复制配置到剪贴板」。

### 2. 粘贴配置

OCS → **通用 → 全局设置 → 题库配置** → 粘贴 → 保存。

从控制台复制的就是这段（也可以直接用仓库里的
[ocs-answerer-wrapper.json](ocs-answerer-wrapper.json)）：

```json
[
  {
    "name": "本地题库(8890)",
    "homepage": "http://127.0.0.1:8890/",
    "url": "http://127.0.0.1:8890/ocs",
    "method": "post",
    "contentType": "json",
    "type": "GM_xmlhttpRequest",
    "headers": { "Content-Type": "application/json" },
    "data": {
      "title": "${title}",
      "options": { "handler": "return (env)=> (env.options || '').split('\\n').filter(Boolean)" },
      "type": { "handler": "return (env)=> env.type || ''" }
    },
    "handler": "return (res) => { if (!res || res.code !== 1 || !res.answer) return undefined; return [res.question || undefined, res.answer];}"
  }
]
```

### 3. 验证

OCS 设置页的「在线搜题」输入框随便打一道题，能返回答案就通了。
或者跑仓库自带的验证器：

```powershell
node ocs_e2e_test.mjs .\ocs-answerer-wrapper.json
```

预期输出 `通过 8 项，失败 0 项 / ✅ 端到端就绪`。

---

## 二、这段配置每个字段为什么这么写

对着 OCS 源码
[`answer.wrapper.handler.ts`](https://github.com/ocsjs/ocsjs/blob/4.0/packages/core/src/core/answer-wrapper/answer.wrapper.handler.ts)
逐条解释，**改配置时别破坏这几条**：

| 字段 | 值 | 原因 |
|---|---|---|
| `type` | `GM_xmlhttpRequest` | OCS 跑在 `https://mooc1.chaoxing.com`，直接 fetch `http://127.0.0.1` 会被混合内容拦。`GM_xmlhttpRequest` 走油猴/脚本猫的特权通道，不受同源限制，也就不用改 `@connect` |
| `data.title` | `"${title}"` | `${xxx}` 是 OCS 的占位符，由 `resolvePlaceHolder` 在**请求前**替换成题干 |
| `data.options` | `{handler: ...}` | `data` 里的对象如果带 `handler` 字段，OCS 会用 `Function(handler)()` 生成函数、再传 `env` 调用。OCS 给的 `env.options` 是**换行分隔的字符串**，这里拆成数组发给服务端 |
| `data.type` | `{handler: ...}` | 同上，把 `single / multiple / judgement / completion` 直接透传 |
| `handler` | `return (res) => ...` | **必须 return 一个函数**。OCS 拿 `Function(handler)()` 的返回值当解析器，返回的不是函数就抛「handler 响应处理器必须返回一个函数」 |
| 返回值 | `[题目, 答案]` 或 `[[题,答],...]` | 搜不到必须返回 `undefined`，否则 OCS 会把提示文案当成题目展示 |
| 答案类型 | **字符串** | 多选要拼成一个串（`"A#C"` 或 `"ACD"`），OCS 后面用 `splitAnswer` 按 `# ; \| ### --- ===` 再拆。**返回数组会静默失效** |

> 想在某些题库搜不到时给个提示行，把控制台的「未命中时显示提示行」勾上，
> 或者 `python make_ocs_config.py --miss`，handler 会变成
> `return [res.message, undefined]`（OCS 的约定：题目位置放提示，答案放 undefined）。

---

## 三、答案会怎么落到选项上

返回的答案字符串不是直接塞进输入框，OCS 还会走一遍 resolver
（`packages/core/src/core/worker/resolvers/*.ts`）。所以**答案格式要对得上题型**：

| 题型 | 服务端返回 | OCS 怎么处理 |
|---|---|---|
| 单选 `single` | `C` | 走 `resolvePlainAnswer` → 取第 3 个选项；也支持直接返回选项原文 |
| 多选 `multiple` | `BCD`（推荐）或 `B#C#D` | 纯字母走 plainOptions；带 `#` 的走 `splitAnswer` 再逐个匹配选项文本 |
| 判断 `judgement` | `对` / `错` | `resolveJudgement` 只认 `对/正确/是/√/T` 与 `错/错误/否/×/F`，所以服务端会把库里的 `A/B` 按选项文本还原成对错 |
| 填空 `completion` | `社会主义现代化` | 多个空用 `#` 分隔；`resolveCompletion` 按空数对齐 |

服务端 `_ocs_answer()` 已经做了这层归一化，包括：

- `["A","C"]` → `AC`
- `特殊侵权责任#过错推定责任` → 保持（OCS 能拆）
- 判断题库里存 `A` → 查选项文本判断代表对还是错 → 输出 `对`/`错`

---

## 四、题库从哪来

命中顺序：**本地库 → 学小易 → 大模型**。三条都能单独开关。

### ① 本地库（主力，零成本）

```powershell
# 导入已有题库（支持 {"q","a"} / {"question","answer"} / 键值对象）
python import_bank.py tiku.json

# 从答案页批量收录
#   浏览器控制台或 curl 直接打 /learn
curl -X POST http://127.0.0.1:8890/learn -H "Content-Type: application/json" ^
     -d "[{\"q\":\"题干\",\"a\":\"A\"},{\"q\":\"题干2\",\"a\":\"对\"}]"
```

匹配用 bigram Dice 相似度，默认阈值 `0.5`，归一化会去掉标点/空格/括号，
所以题干多一个下划线、少一个句号不影响命中。控制台里能直接调阈值。

### ② 大模型兜底（已配好 DeepSeek）

`config.json` 里现在用的是 `deepseek-chat`，实测单题 **1.3–1.5 秒**返回：

```json
{
  "llm_enabled": true,
  "llm_provider": "deepseek",
  "llm_profiles": {
    "deepseek": { "base": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": "sk-..." }
  }
}
```

prompt 已经约束成「只输出字母 / 判断题只输出对错 / 填空题直接给答案」，`temperature=0`。
命中的结果会自动落库（`llm_cache`），同一道题第二次就直接走本地。

### ③ 免 key 兜底（有额度限制，别当主力）

| 平台 | 实测 |
|---|---|
| `pollinations` | 免 key 可用，但**每 IP 30 秒一次**，超了返回 402 |
| `llm7` | 免 key 可用，但匿名档每日 token 额度很小，很快 429 |

两个都已经写进 `LLM_PRESETS`，作为 `llm_fallback` 的末位备选。**日常用 DeepSeek，别指望它们扛量。**

### ④ 学小易（需要有效 open_id）

`config.json` 的 `xxy_open_id` 留空就自动跳过。要从微信「学小易」小程序抓 `wx-open-id` 请求头，
会过期，过期后服务端判定失效并继续走大模型。

---

## 五、文件清单

| 文件 | 作用 |
|---|---|
| [tiku_server.py](tiku_server.py) | 服务本体。`/ocs` 是 OCS 接口，`/v1/cx` 是学习通脚本用的，`/adapter-service/search` 等是 StarX 的 |
| [make_ocs_config.py](make_ocs_config.py) | 生成 AnswererWrapper JSON（换端口/IP 时用它，别手改） |
| [ocs-answerer-wrapper.json](ocs-answerer-wrapper.json) | 现成可粘贴的配置 |
| [ocs_e2e_test.mjs](ocs_e2e_test.mjs) | **端到端验证**：复刻 OCS 的 handler + resolver，验证答案能落到正确选项 |
| [ocs_wrapper_test.mjs](ocs_wrapper_test.mjs) | 协议层验证：只查 handler 契约和答案是否字符串 |
| [import_bank.py](import_bank.py) | 导入题库，自带格式归一化 |
| [config.json](config.json) | 端口、阈值、LLM 配置 |
| [bank.json](bank.json) | 题库数据 |

---

## 六、接口一览（OCS 相关）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/ocs` | **OCS 专用**，返回 `{code, question, answer, source, score}` |
| GET | `/ocs/config` | 直接吐 AnswererWrapper JSON，`?miss=1` 切提示模式 |
| GET | `/` | 控制台（含 OCS 配置一键复制） |
| GET | `/health` | `{"ok":true,"bank":N}` |

`/ocs` 同时接受 JSON 和 form，也支持 GET（`?title=&options=&type=`），
方便你换成 `method: "get"` 的写法排查问题。

---

## 七、排错

| 现象 | 原因 | 处理 |
|---|---|---|
| OCS 报「题库连接失败」 | 服务没起 / 端口不对 | `curl http://127.0.0.1:8890/health` |
| 搜题一直转圈后超时 | OCS 的 `timeout_seconds` 是 60s，LLM 慢于它 | 用 DeepSeek（1.5s）；或先把题录进本地库 |
| 答案出来了但选不中选项 | 答案格式与题型不匹配 | 跑 `node ocs_e2e_test.mjs`，它会指出哪一题落不到选项 |
| 多选题只选中一个 | 答案返回成了数组 | 见第二节表格最后一行，必须是字符串 |
| 判断题选错位置 | 库里存的是 `A/B` 而不是 `对/错` | 已兼容（按选项文本还原），若选项本身是「A. 正确」这种会走字母兜底 |
| 每次都要重新搜 | `llm_cache` 关了 | 控制台勾「结果入库」 |
| 改了端口后 OCS 不通 | 配置里的 URL 没跟着改 | `python make_ocs_config.py --port 8891` 重新生成 |

---

## 八、和原来那套的关系

- 原来的 `题库源-配置说明.md` 讲的是**学习通油猴脚本**（`chaoxing-mouse-auto-next.user.js`）怎么接，
  那是 `cx / icodef / generic` 三种自定义格式，跟 OCS 无关。
- 现在这套 `tiku/` 服务**同时**吐三种协议：
  - `/v1/cx` → 学习通油猴脚本
  - `/ocs` → **OCS 网课助手**
  - `/adapter-service/search`、`/api/search`、`/search` → StarX / tikuAdapter

一个 `bank.json` 喂三个前端，收录一次三处都能用。
