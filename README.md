# 本地题库服务（tiku-service）

自建的本地题库服务，为 **OCS 网课助手**、学习通油猴脚本、StarX/tikuAdapter 等前端提供统一的搜题接口。

自带图形化管理面板，双击即用，不需要命令行。

## 为什么自己搭

公共题库有两个绕不开的问题：

1. **限流和失效** —— 免费接口基本都靠 token 续命，随时 403/429
2. **答案格式对不上前端** —— 多选答案、判断题的「对/错」都得按各自前端的约定返回，否则搜到了也选不中

所以这里做成一个本地服务：一次收录，三个前端通用；题库和匹配逻辑都在自己手上。

## 快速开始

### 方式一：直接跑 exe（无需 Python）

从 [Releases](https://github.com/wushenai/tiku-service/releases) 或仓库里的 `dist/学习通题库.zip` 解压后：

```
双击  启动题库.exe
```

面板会自动把服务起起来，地址 `http://127.0.0.1:8890`。

### 方式二：从源码跑

```bash
pip install flask requests websockets fonttools
python tiku_gui.py          # 打开面板
# 或者只要服务，不要界面
python tiku_server.py
```

双击 `tiku_gui.vbs` 可以无黑窗口启动面板（**保持该文件纯 ASCII**，否则 wscript 解析报错）。

## 接入 OCS 网课助手

OCS → 通用 → 全局设置 → 题库配置 → 粘贴：

```json
[
  {
    "name": "本地题库",
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

点面板上的「复制 OCS 配置」可以直接拿到这一段。

### 每个字段为什么这么写

对着 [OCS 官方 API 文档](https://docs.ocsjs.com/docs/other/api) 和
[源码](https://github.com/ocsjs/ocsjs/blob/4.0/packages/core/src/core/answer-wrapper/answer.wrapper.handler.ts) 逐条解释：

| 字段 | 值 | 原因 |
|---|---|---|
| `type` | `GM_xmlhttpRequest` | OCS 跑在 `https://mooc1.chaoxing.com`，直接 fetch `http://127.0.0.1` 会被混合内容拦截。走油猴特权通道，也不用改 `@connect` |
| `data.title` | `"${title}"` | `resolvePlaceHolder` 在请求前把 `${xxx}` 替换成题干 |
| `data.options` | `{handler: ...}` | `data` 里的对象若带 `handler`，OCS 会用 `Function(handler)()` 生成函数再传 `env` 调用。`env.options` 是**换行分隔的字符串**，这里转数组 |
| `data.type` | `{handler: ...}` | 同上，透传题型 |
| `handler` | `return (res) => ...` | **必须 return 一个函数**。返回的不是函数会抛「handler 响应处理器必须返回一个函数」 |
| 返回值 | `[题目, 答案]` | 搜不到必须返回 `undefined`，否则提示文案会被当成题目展示 |
| 答案类型 | **字符串** | 多选拼成一个串（`"ACD"` 或 `"B#C#D"`），OCS 后面用 `splitAnswer` 按 `# ; \| ### --- ===` 拆。**返回数组会静默失效** |

## 答案怎么落到选项上

服务端不只是返回文本，还要匹配 OCS 的 resolver 逻辑
（[resolvers/*.ts](https://github.com/ocsjs/ocsjs/tree/4.0/packages/core/src/core/worker/resolvers)）：

| 题型 | 返回 | OCS 处理 |
|---|---|---|
| 单选 `single` | `C` | `resolvePlainAnswer` 取第 3 个选项 |
| 多选 `multiple` | `BCD` 或 `B#C#D` | 纯字母走 plainOptions；带 `#` 走 `splitAnswer` 逐个匹配 |
| 判断 `judgement` | `对` / `错` | `resolveJudgement` 只认 `对/正确/是/√` 与 `错/错误/否/×`，所以库里存 `A/B` 时服务端会按选项文本反查还原 |
| 填空 `completion` | `社会主义现代化` | 多空用 `#` 分隔，按空数对齐 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/ocs` | OCS 专用，返回 `{code, question, answer, source, score}` |
| GET | `/ocs/config` | 直接吐出 AnswererWrapper JSON（`?miss=1` 切提示模式） |
| POST | `/v1/cx` | 学习通油猴脚本格式 |
| POST | `/decrypt` | 超星字体解密（`font-cxsecret`） |
| POST | `/learn` | 收录问答 |
| GET/POST | `/bank` | 导出 / 导入整库 |
| POST | `/bank/delete` | 删除条目 |
| GET | `/config` | 查看配置（不回传 key 明文） |
| POST | `/llm/test` | 测大模型连通性 |
| GET | `/` | 网页控制台 |
| GET | `/health` | `{"ok":true,"bank":N}` |

`/adapter-service/search`、`/api/search`、`/search`、`/api/v1/search` 是 StarX / tikuAdapter 的兼容入口。

## 题库来源

命中顺序：**本地库 → 学小易 → 大模型**。

### 本地库（主力）

匹配用 **bigram Dice 相似度**，默认阈值 `0.5`。归一化会去掉标点、空格、括号、题号，
所以题干多一个下划线、少一个句号不影响命中。

```bash
# 导入
python import_bank.py your_bank.json

# 或直接打接口
curl -X POST http://127.0.0.1:8890/learn \
  -H "Content-Type: application/json" \
  -d '[{"q":"题干","a":"A"},{"q":"题干2","a":"对"}]'
```

支持三种格式：`{"q","a"}`、`{"question","answer"}`、键值对象。

### 大模型兜底

编辑 `config.json` 填自己的 key：

```json
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
```

prompt 已约束成「只输出字母 / 判断题只输出对错 / 填空直接给答案」，`temperature=0`。
命中的结果默认落库（`llm_cache`），同一题第二次直接走本地。

也支持 Ollama、豆包客户端 CDP（免 key，需客户端带 `--remote-debugging-port=9334`）等。

> **仓库里不含任何 API key。** `config.json` 是从 `config.template.json` 生成的。

### 学小易

`config.json` 的 `xxy_open_id` 留空则自动跳过。需要从微信小程序抓 `wx-open-id`，会过期。

## 验证

不是「看起来能用」，下面都跑过：

```bash
# OCS 端到端：复刻 handler + resolvers，验证答案真的落到正确选项
node ocs_e2e_test.mjs ./ocs-answerer-wrapper.json

# 协议层：只查 handler 契约和答案类型
node ocs_wrapper_test.mjs ./ocs-answerer-wrapper.json

# 面板动作：启动/停止/重启/接管外部实例
python test_gui_actions.py

# 面板主题配色：从 Tk 内部解析真实值
python verify_gui.py

# 打包产物完整性 + 冻结路径逻辑
python verify_package.py
python verify_frozen_paths.py
```

`ocs_e2e_test.mjs` 会复刻 OCS 真实的
[`answer.wrapper.handler.ts`](https://github.com/ocsjs/ocsjs/blob/4.0/packages/core/src/core/answer-wrapper/answer.wrapper.handler.ts)
与 `resolvers`，用 Dice 系数、`removeRedundant`、`splitAnswer` 走一遍，
确认返回的答案字符串能命中正确选项——只验证「接口返回了东西」没有意义。

## 目录

```
tiku-service/
├─ tiku_server.py           服务主体（Flask）
├─ tiku_gui.py              图形面板（tkinter）
├─ tiku_gui.vbs             无窗口启动器（纯 ASCII）
├─ cxsecret_font.py         超星字体解密
├─ xuexiaoyi_API.py         学小易小程序接口
├─ doubao_cdp.py            豆包客户端 CDP 答题
├─ font_map.json            字形 → 汉字映射表（1.5MB）
├─ config.template.json     配置模板（不含 key）
├─ bank.example.json        题库格式示例
├─ make_ocs_config.py       生成 AnswererWrapper JSON
├─ import_bank.py           题库导入
├─ build_package.py         打包成 zip
├─ ocs-answerer-wrapper.json
├─ OCS对接说明.md
├─ 软件使用说明.md
└─ dist/
   ├─ 启动题库.exe          单文件程序（含 Python 运行时，24MB）
   └─ 学习通题库.zip        分发用压缩包
```

## 打包

```bash
python build_package.py
```

产物在 `dist/学习通题库.zip`。打包脚本会：

1. **拦住密钥泄露** —— 配置从模板重新生成，同时扫描源码里有无硬编码 key，有则中止
2. 用 PyInstaller 打单文件 exe（`--onefile --windowed`）
3. 排除日志、缓存、备份、测试脚本
4. 静态验证 exe 内嵌归档（入口 + PYZ 模块 + Tk 运行时 + 数据文件）

> 关于 exe 的说明：单文件程序容易被国产杀毒误报，这是 PyInstaller 打包方式的通病。
> 介意的话从源码跑。另外某些启用 WDAC/App Control 策略的机器会拒绝执行未签名 exe。

## 注意事项

- 服务默认只监听 `127.0.0.1`，局域网访问不到。**不要把 host 改成 `0.0.0.0`**
- 题库存在 `bank.json`，换机器拷贝整个目录即可
- 大模型兜底只能算辅助，准确率自行判断，别拿去顶重要考试

## License

MIT
