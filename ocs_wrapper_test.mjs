#!/usr/bin/env node
/**
 * OCS AnswererWrapper 兼容性验证器
 * ---------------------------------
 * 严格复刻 OCS 4.x 真实的 defaultAnswerWrapperHandler 行为
 * (packages/core/src/core/answer-wrapper/answer.wrapper.handler.ts)：
 *   1. data 里的字段：对象且有 handler -> Function(handler)() 得到函数，再以 env 调用
 *      字符串 -> 解析 ${title} / ${options} / ${type} 占位符
 *   2. handler 字段：Function(handler)() 必须返回函数，传入响应体
 *   3. 返回值若是二维数组 -> 每个 item 映射成 {question, answer}；
 *      否则当成单条 [question, answer]
 *   4. 多选答案必须是字符串，OCS 后面用 splitAnswer 按 # ; | ### --- === 拆
 *
 * 用法：node ocs_wrapper_test.mjs [configUrl|configFile]
 */

import { readFileSync } from 'node:fs';

const SEPARATORS = ['===', '#', '---', '###', '|', ';', '；'];

/** 复刻 OCS splitAnswer 的判定顺序（JSON 数组 > 第一个命中的分隔符） */
function splitAnswer(answer) {
  answer = String(answer ?? '').trim();
  if (!answer) return [];
  try {
    const json = JSON.parse(answer);
    if (Array.isArray(json)) return json.map(String).map((s) => s.trim()).filter(Boolean);
  } catch {
    for (const sep of SEPARATORS) {
      if (answer.split(sep).length > 1) {
        return answer.split(sep).map((s) => s.trim()).filter(Boolean);
      }
    }
  }
  return [answer];
}

function resolvePlaceHolder(data, env, encodeURI = false) {
  if (typeof data === 'string') {
    const matches = data.match(/\$\{(.*?)\}/g) || [];
    for (const ph of matches) {
      const key = ph.replace(/\$\{(.*)\}/, '$1');
      const value = env[key];
      if (value === undefined) continue;
      data = data.replace(ph, encodeURI ? encodeURIComponent(value) : value);
    }
    return data;
  }
  if (data && typeof data === 'object') {
    for (const k of Object.keys(data)) data[k] = resolvePlaceHolder(data[k], env, encodeURI);
  }
  return data;
}

async function request(url, { method = 'get', data = {}, headers = {} } = {}) {
  const res = await fetch(url, {
    method: method.toUpperCase(),
    headers: { 'Content-Type': 'application/json', ...headers },
    body: method.toLowerCase() === 'post' ? JSON.stringify(data) : undefined,
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** 与 OCS 一致的 wrapper 执行器 */
async function runWrapper(wrapper, env) {
  const {
    name = '未知题库',
    method = 'get',
    contentType = 'json',
    data: wrapperData = {},
    handler = 'return (res)=> [JSON.stringify(res), undefined]',
    headers = {},
  } = wrapper;

  let url;
  let requestData = {};
  if (method.toLowerCase() === 'get') {
    const u = new URL(resolvePlaceHolder(wrapper.url, env, true));
    for (const k of Object.keys(wrapperData)) {
      u.searchParams.set(k, resolvePlaceHolder(wrapperData[k], env));
    }
    url = u.toString();
  } else {
    url = resolvePlaceHolder(wrapper.url, env);
    const data = {};
    for (const k of Object.keys(wrapperData)) {
      const v = wrapperData[k];
      if (v && typeof v === 'object' && 'handler' in v) {
        const fn = Function(v.handler)();
        if (typeof fn !== 'function') throw new Error(`data.${k} 的 handler 必须返回函数`);
        data[k] = fn(env);
      } else {
        data[k] = resolvePlaceHolder(v, env);
      }
    }
    requestData = data;
  }

  const responseData = await request(url, { method, data: requestData, headers });
  if (contentType === 'text' && typeof responseData !== 'string') {
    // OCS 用 response.text()，这里对齐
  }

  const responseHandler = Function(handler)();
  if (typeof responseHandler !== 'function') throw new Error('handler 必须返回一个函数');

  const info = responseHandler(responseData);
  let results = [];
  if (info && Array.isArray(info)) {
    if (info.every((i) => Array.isArray(i))) {
      results = info.map((i) => ({ question: i[0], answer: i[1], extra_data: i[2] || {} }));
    } else {
      results = [{ question: info[0], answer: info[1], extra_data: info[2] || {} }];
    }
  }
  return { name, url, requestData, response: responseData, results };
}

/** 断言集：必须是字符串答案、格式符合 OCS 后续解析 */
function assertShape(results) {
  const problems = [];
  for (const r of results) {
    if (r.answer === undefined || r.answer === null) {
      problems.push(`answer 为 ${r.answer}`);
      continue;
    }
    if (Array.isArray(r.answer)) {
      problems.push(`answer 是数组（OCS 会当成字符串处理，多选会失效）：${JSON.stringify(r.answer)}`);
      continue;
    }
    if (typeof r.answer !== 'string') {
      problems.push(`answer 类型是 ${typeof r.answer}，应为 string`);
      continue;
    }
  }
  return problems;
}

const CASES = [
  {
    label: '单选题（本地命中）',
    env: { title: '马克思主义中国化时代化的最新成果是（）', type: 'single', options: 'A. 毛泽东思想\nB. 邓小平理论\nC. 习近平新时代中国特色社会主义思想\nD. 科学发展观' },
  },
  {
    label: '多选题（答案需可被 splitAnswer 拆分）',
    env: { title: '下列属于我国基本政治制度的是（）', type: 'multiple', options: 'A. 人民代表大会制度\nB. 中国共产党领导的多党合作和政治协商制度\nC. 民族区域自治制度\nD. 基层群众自治制度' },
  },
  {
    label: '判断题',
    env: { title: '实践是检验真理的唯一标准。', type: 'judgement', options: '正确\n错误' },
  },
  {
    label: '填空题',
    env: { title: '全面建成社会主义现代化强国，总的战略安排是分两步走，从二〇二〇年到二〇三五年基本实现（ ）', type: 'completion', options: '' },
  },
  {
    label: '题库没有的题（走 LLM 或未命中的降级路径）',
    env: { title: '这是一道题库里绝对不存在的测试题：量子纠缠的贝尔不等式阈值是多少？', type: 'single', options: 'A. 1\nB. 2\nC. 2√2\nD. 4' },
  },
];

async function main() {
  const src = process.argv[2] || './ocs-answerer-wrapper.json';
  const raw = /^https?:/.test(src)
    ? await (await fetch(src)).text()
    : readFileSync(src, 'utf8');

  let wrappers;
  try {
    wrappers = JSON.parse(raw);
  } catch (e) {
    console.error(`❌ 配置不是合法 JSON：${e.message}`);
    process.exit(1);
  }
  if (!Array.isArray(wrappers) || !wrappers.length) {
    console.error('❌ 配置必须是 AnswererWrapper 非空数组');
    process.exit(1);
  }

  console.log(`载入 ${wrappers.length} 个题库配置\n` + '='.repeat(60));

  let failed = 0;
  for (const w of wrappers) {
    for (const field of ['name', 'url', 'handler']) {
      if (typeof w[field] !== 'string' || !w[field]) {
        console.error(`❌ [${w.name || '?'}] 缺少必填字段 ${field}`);
        failed++;
      }
    }
    // handler 语法预检：Function() 构造不报错才算能进 OCS
    try {
      const fn = Function(w.handler)();
      if (typeof fn !== 'function') throw new Error('未返回函数');
    } catch (e) {
      console.error(`❌ [${w.name}] handler 无法解析：${e.message}`);
      failed++;
    }
    // data 字段的 handler 也要能解析
    for (const [k, v] of Object.entries(w.data || {})) {
      if (v && typeof v === 'object' && 'handler' in v) {
        try {
          const fn = Function(v.handler)();
          if (typeof fn !== 'function') throw new Error('未返回函数');
        } catch (e) {
          console.error(`❌ [${w.name}] data.${k} 无法解析：${e.message}`);
          failed++;
        }
      }
    }
  }

  for (const c of CASES) {
    console.log(`\n▶ ${c.label}`);
    for (const w of wrappers) {
      let out;
      try {
        out = await runWrapper(w, c.env);
      } catch (e) {
        console.log(`  ✗ ${w.name}: 请求/解析异常 ${e.message}`);
        failed++;
        continue;
      }
      const problems = assertShape(out.results);
      if (problems.length) {
        console.log(`  ✗ ${w.name}: ${problems.join('；')}`);
        failed++;
        continue;
      }
      if (!out.results.length) {
        console.log(`  · ${w.name}: 未命中（返回 undefined，符合 OCS 规范）`);
        continue;
      }
      for (const r of out.results) {
        const parts = r.answer ? splitAnswer(r.answer) : [];
        console.log(`  ✓ ${w.name}: 答案="${r.answer}" → OCS 拆分=${JSON.stringify(parts)}`);
        if (typeof r.question !== 'string' && r.question !== undefined) {
          console.log(`    ! question 不是字符串：${typeof r.question}`);
          failed++;
        }
      }
    }
  }

  console.log('\n' + '='.repeat(60));
  console.log(failed === 0 ? '✅ 全部通过' : `❌ ${failed} 项不通过`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
