#!/usr/bin/env node
/**
 * OCS AnswererWrapper 端到端验证（协议 + 判题落点）
 * -------------------------------------------------
 * 两步都按 OCS 4.x 真实源码复刻：
 *   Step 1  answer.wrapper.handler.ts  —— 发请求、解析 data 字段、跑 handler
 *   Step 2  worker/resolvers/*.ts + core/utils/string.ts
 *           —— 用返回的答案字符串去命中选项（splitAnswer / removeRedundant /
 *              normalizeString / answerNormalizedMatch / answerSimilar /
 *              resolveSingle / resolveMultiple / resolveJudgement / resolveCompletion）
 *
 * 只要这两步的结果与预期选项一致，粘进 OCS 就能直接答题。
 * 重点覆盖的坑：多选答案必须是字符串（数组会被 OCS 当字符串处理）、
 * 选项前缀 "A." 必须能被剥离、判断题「对/错」要能对上「正确/错误」。
 *
 * 用法：node ocs_e2e_test.mjs [configFile|configUrl]
 *
 * 注意：未命中用例会走 LLM 兜底，结果按 llm_cache 落库。跑完若不想留测试题，
 *       见文末 CLEANUP_HINT。
 */

import { readFileSync } from 'node:fs';

// ============ 1. OCS splitAnswer ============
const DEFAULT_SEPS = ['===', '#', '---', '###', '|', ';', '；'];

function splitAnswer(answer, separators = DEFAULT_SEPS) {
  answer = String(answer ?? '').trim();
  if (!answer) return [];
  separators = separators.filter((s) => s.trim().length > 0);
  try {
    const json = JSON.parse(answer);
    if (Array.isArray(json)) return json.map(String).map((s) => s.trim()).filter(Boolean);
  } catch {
    for (const sep of separators) {
      if (answer.split(sep).length > 1) {
        return answer.split(sep).map((s) => s.trim()).filter(Boolean);
      }
    }
  }
  return [answer];
}

// ============ 2. OCS 字符串工具（core/utils/string.ts） ============
function clearString(str, ...exclude) {
  exclude.push(...['①②③④⑤⑥⑦⑧⑨']);
  return String(str ?? '')
    .trim()
    .toLocaleLowerCase()
    .replace(RegExp(`[^\\u2E80-\\u9FFFA-Za-z0-9${exclude.join('')}]*`, 'g'), '');
}

function normalizeString(str) {
  return clearString(
    String(str ?? '')
      .replace(/[，。！？；：""''、（）【】《》\s]/g, '')
      .replace(/[,.\-!?;:'"()[\]<>]/g, '')
      .replace(/[ａ-ｚＡ-Ｚ０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
      .replace(/％/g, '%')
  );
}

function removeRedundant(str) {
  return (
    String(str ?? '')
      .trim()
      .replace(/^[A-Z]{1}[^A-Za-z0-9⺀-鿿]+([A-Za-z0-9⺀-鿿]+)/, '$1')
      .replace(/^[A-Z]{1}([⺀-鿿][A-Za-z0-9⺀-鿿]*)/, '$1') || ''
  );
}

function answerNormalizedMatch(answers, options) {
  const _answers = answers.map(removeRedundant).map(normalizeString);
  const _options = options.map(removeRedundant).map(normalizeString);
  return _answers.length
    ? _options
        .map((opt, i) => {
          let level = 0;
          if (opt) {
            for (const ans of _answers) {
              if (ans === opt) {
                level = 2;
                break;
              } else if (ans.includes(opt)) {
                level = 1;
              }
            }
          }
          return { level, original: options[i] };
        })
        .filter((x) => x.level > 0)
        .sort((a, b) => b.level - a.level)
        .map((x) => x.original)
    : [];
}

/** string-similarity 的 findBestMatch：Dice 系数（bigram） */
function diceCoefficient(a, b) {
  if (a === b) return 1;
  if (a.length < 2 || b.length < 2) return 0;
  const bigrams = (s) => {
    const m = new Map();
    for (let i = 0; i < s.length - 1; i++) {
      const g = s.slice(i, i + 2);
      m.set(g, (m.get(g) || 0) + 1);
    }
    return m;
  };
  const ma = bigrams(a);
  const mb = bigrams(b);
  let inter = 0;
  for (const [g, n] of ma) {
    const n2 = mb.get(g) || 0;
    inter += Math.min(n, n2);
  }
  const total = a.length - 1 + (b.length - 1);
  return total ? (2 * inter) / total : 0;
}

function findBestMatch(main, targets) {
  let best = { rating: 0, target: '' };
  for (const t of targets) {
    const rating = diceCoefficient(main, t);
    if (rating > best.rating) best = { rating, target: t };
  }
  return { bestMatch: best };
}

function answerSimilar(answers, options) {
  const _answers = answers.map(removeRedundant).map((a) => clearString(a));
  const _options = options.map(removeRedundant).map((o) => clearString(o));
  return _answers.length
    ? _options.map((o) => (o.trim() === '' ? { rating: 0, target: '' } : findBestMatch(o, _answers).bestMatch))
    : _options.map(() => ({ rating: 0, target: '' }));
}

// ============ 3. OCS resolvers ============
const CORRECT_WORDS = ['是', '对', '正确', '确定', '√', '对的', '是的', '正确的', 'true', 'True', 'T', 'yes', '1'];
const INCORRECT_WORDS = ['非', '否', '错', '错误', '×', 'X', '错的', '不对', '不正确的', '不正确', '不是', '不是的', 'false', 'False', 'F', 'no', '0'];

function jMatches(target, words) {
  return words.some(
    (w) => clearString(removeRedundant(w), '√', '×') === clearString(removeRedundant(target), '√', '×')
  );
}

function resolveJudgement(answerGroups, options) {
  for (const answers of answerGroups) {
    const showCorrect = answers.find((a) => jMatches(a, CORRECT_WORDS));
    const showIncorrect = answers.find((a) => jMatches(a, INCORRECT_WORDS));
    if (showCorrect || showIncorrect) {
      for (const option of options) {
        if (showCorrect && jMatches(option, CORRECT_WORDS)) return { finish: true, option };
        if (showIncorrect && jMatches(option, INCORRECT_WORDS)) return { finish: true, option };
      }
      return { finish: false };
    }
  }
  return { finish: false };
}

function isJudgementOptions(options) {
  if (options.length !== 2) return false;
  return (
    options.some((o) => jMatches(o, CORRECT_WORDS)) && options.some((o) => jMatches(o, INCORRECT_WORDS))
  );
}

function resolvePlainAnswer(answer) {
  const resolve = String(answer).trim().replace(/[,，.。．、 #]/g, '').trim();
  if (resolve.length > 8 || !/[A-Z]/.test(resolve)) return undefined;
  const counter = {};
  let min = 0;
  for (let i = 0; i < resolve.length; i++) {
    if (resolve.charCodeAt(i) < min) return undefined;
    min = resolve.charCodeAt(i);
    counter[min] = (counter[min] || 0) + 1;
  }
  for (const k in counter) if (counter[k] !== 1) return undefined;
  return resolve;
}

function resolveSingle(answers, options, separators) {
  const allAnswer = answers.map((a) => splitAnswer(a, separators)).flat();
  const optionStrings = options.map(removeRedundant);

  const normalized = answerNormalizedMatch(allAnswer, optionStrings);
  if (normalized.length) {
    const i = optionStrings.findIndex((o) => o === normalized[0]);
    if (i !== -1) return { finish: true, option: options[i] };
  }

  const ratings = answerSimilar(allAnswer, optionStrings);
  let bestIndex = -1;
  let bestRating = 0;
  ratings.forEach((r, i) => {
    if (r.rating > bestRating) {
      bestRating = r.rating;
      bestIndex = i;
    }
  });
  if (bestIndex !== -1 && bestRating > 0.6) return { finish: true, option: options[bestIndex] };

  for (const answer of allAnswer) {
    const ans = resolvePlainAnswer(String(answer).replace(/\n/g, '').trim());
    if (ans && ans.length === 1) {
      const i = ans.charCodeAt(0) - 65;
      if (optionStrings[i] !== undefined) return { finish: true, option: options[i] };
    }
  }

  if (allAnswer.length > 1) {
    const r = resolveSingle([allAnswer.join('')], options, separators);
    if (r.finish) return r;
  }
  return { finish: false };
}

function resolveMultiple(resultAnswers, options, separators) {
  const optionStrings = options.map(removeRedundant);
  const groups = [];

  for (const raw of resultAnswers) {
    const answers = splitAnswer(String(raw).trim(), separators);

    // 阶段1：归一化包含匹配（只允许 答案⊇选项）
    let g1 = { options: [], ratings: [] };
    for (const opt of answerNormalizedMatch(answers, optionStrings)) {
      const idx = optionStrings.indexOf(opt);
      if (idx === -1) continue;
      const hit = answers.find((a) => {
        const na = normalizeString(removeRedundant(a));
        const no = normalizeString(removeRedundant(opt));
        return na === no || na.includes(no);
      });
      if (!hit) continue;
      g1.options.push(options[idx]);
      const na = normalizeString(removeRedundant(hit));
      const no = normalizeString(removeRedundant(opt));
      g1.ratings.push(na === no ? 1 : 0.8);
    }

    // 阶段2：相似度 > 0.6
    const ratings = answerSimilar(answers, optionStrings);
    const g2 = { options: [], ratings: [] };
    ratings.forEach((r, j) => {
      if (r.rating > 0.6) {
        g2.options.push(options[j]);
        g2.ratings.push(r.rating);
      }
    });

    const score = (g) => g.options.length * 100 + g.ratings.reduce((a, b) => a + b, 0);
    groups.push(score(g1) >= score(g2) ? g1 : g2);
  }

  const sorted = groups.filter((g) => g.options.length !== 0).sort((a, b) => score(b) - score(a));
  if (sorted[0]) return { finish: true, options: sorted[0].options };

  const plain = [];
  for (const answer of resultAnswers) {
    const ans = String(answer).replace(/\n/g, '').trim();
    if (resolvePlainAnswer(ans)) {
      for (const ch of ans) {
        const i = ch.charCodeAt(0) - 65;
        if (options[i] !== undefined) plain.push(options[i]);
      }
    }
  }
  if (plain.length) return { finish: true, options: [...new Set(plain)] };
  return { finish: false };
}

function resolveCompletion(answerGroups, blankCount, separators) {
  for (const answers of answerGroups) {
    let ans = answers.filter(Boolean);
    if (ans.length === 1) ans = splitAnswer(ans[0], separators);
    if (ans.length !== 0 && (ans.length === blankCount || blankCount === 1)) {
      if (ans.length === blankCount) return { finish: true, answers: ans };
      if (blankCount === 1) return { finish: true, answers: [ans.join(' ')] };
    }
  }
  return { finish: false };
}

/** OCS createDefaultQuestionResolver 的 single：先判判断题选项，再走 resolveSingle */
function resolveAsOcs(type, answers, optionTexts, separators) {
  if (type === 'judgement') {
    return resolveJudgement([answers], optionTexts);
  }
  if (type === 'multiple') {
    return resolveMultiple(answers, optionTexts, separators);
  }
  if (type === 'completion') {
    return resolveCompletion([answers], optionTexts.length || 1, separators);
  }
  // single
  if (isJudgementOptions(optionTexts.map((o) => o.trim()))) {
    return resolveJudgement([answers], optionTexts);
  }
  return resolveSingle(answers, optionTexts, separators);
}

// ============ 4. wrapper 执行器 ============
function resolvePlaceHolder(data, env, encode = false) {
  if (typeof data === 'string') {
    for (const ph of data.match(/\$\{(.*?)\}/g) || []) {
      const v = env[ph.replace(/\$\{(.*)\}/, '$1')];
      if (v === undefined) continue;
      data = data.replace(ph, encode ? encodeURIComponent(v) : v);
    }
    return data;
  }
  if (data && typeof data === 'object') {
    for (const k of Object.keys(data)) data[k] = resolvePlaceHolder(data[k], env, encode);
  }
  return data;
}

async function runWrapper(w, env) {
  const { method = 'get', data: wrapperData = {}, handler, headers = {} } = w;
  let url;
  let requestData = {};

  if (method.toLowerCase() === 'get') {
    const u = new URL(resolvePlaceHolder(w.url, env, true));
    for (const k of Object.keys(wrapperData)) u.searchParams.set(k, resolvePlaceHolder(wrapperData[k], env));
    url = u.toString();
  } else {
    url = resolvePlaceHolder(w.url, env);
    const data = {};
    for (const k of Object.keys(wrapperData)) {
      const v = wrapperData[k];
      if (v && typeof v === 'object' && 'handler' in v) {
        const fn = Function(v.handler)();
        if (typeof fn !== 'function') throw new Error(`data.${k} handler 未返回函数`);
        data[k] = fn(env);
      } else {
        data[k] = resolvePlaceHolder(v, env);
      }
    }
    requestData = data;
  }

  const res = await fetch(url, {
    method: method.toUpperCase(),
    headers: { 'Content-Type': 'application/json', ...headers },
    body: method.toLowerCase() === 'post' ? JSON.stringify(requestData) : undefined,
  });
  const text = await res.text();
  let responseData;
  try {
    responseData = JSON.parse(text);
  } catch {
    responseData = text;
  }

  const fn = Function(handler)();
  if (typeof fn !== 'function') throw new Error('handler 未返回函数');
  const info = fn(responseData);

  let results = [];
  if (info && Array.isArray(info)) {
    results = info.every((i) => Array.isArray(i))
      ? info.map((i) => ({ question: i[0], answer: i[1] }))
      : [{ question: info[0], answer: info[1] }];
  }
  return { responseData, results, requestData };
}

// ============ 5. 用例（含预期落点） ============
const CASES = [
  {
    label: '单选题',
    type: 'single',
    title: '马克思主义中国化时代化的最新成果是（）',
    options: ['A. 毛泽东思想', 'B. 邓小平理论', 'C. 习近平新时代中国特色社会主义思想', 'D. 科学发展观'],
    expect: ['C. 习近平新时代中国特色社会主义思想'],
  },
  {
    // 注意：人民代表大会制度是「根本」政治制度，不属于基本政治制度，故库内答案是 BCD
    label: '多选题',
    type: 'multiple',
    title: '下列属于我国基本政治制度的是（）',
    options: ['A. 人民代表大会制度', 'B. 中国共产党领导的多党合作和政治协商制度', 'C. 民族区域自治制度', 'D. 基层群众自治制度'],
    expect: ['B. 中国共产党领导的多党合作和政治协商制度', 'C. 民族区域自治制度', 'D. 基层群众自治制度'],
  },
  {
    label: '判断题',
    type: 'judgement',
    title: '实践是检验真理的唯一标准。',
    options: ['正确', '错误'],
    expect: ['正确'],
  },
  {
    label: '填空题',
    type: 'completion',
    title: '全面建成社会主义现代化强国，总的战略安排是分两步走，从二〇二〇年到二〇三五年基本实现（ ）',
    options: ['1'],
    expect: ['社会主义现代化'],
  },
  {
    label: '多选-文本型答案',
    type: 'multiple',
    title: '违反安全保障义务责任属于（）',
    options: ['A. 公平责任', 'B. 特殊侵权责任', 'C. 过错推定责任', 'D. 连带责任'],
    expect: ['B. 特殊侵权责任', 'C. 过错推定责任'],
  },
  {
    label: '多选题-纯字母答案',
    type: 'multiple',
    title: '现代国防的基本特征包括',
    options: ['A. 军事力量', 'B. 经济实力', 'C. 科技水平', 'D. 民族凝聚力'],
    expect: ['A. 军事力量', 'C. 科技水平', 'D. 民族凝聚力'],
  },
  {
    // 库里存的是字母 A/B，服务端需按选项文本还原成 对/错 才能被 resolveJudgement 命中
    label: '判断题-字母答案还原',
    type: 'judgement',
    title: '判断：在社会主义市场经济中，计划手段完全不能使用。',
    options: ['正确', '错误'],
    expect: ['错误'],
  },
  {
    label: '多选题-LLM 兜底',
    type: 'multiple',
    title: '【多选题】下列关于计算机病毒的说法正确的是',
    options: ['A. 是一种程序', 'B. 具有传染性', 'C. 只能通过U盘传播', 'D. 可以自我复制'],
    expect: ['A. 是一种程序', 'B. 具有传染性', 'D. 可以自我复制'],
  },
];

const sameSet = (a, b) =>
  a.length === b.length && [...a].sort().join('\u0000') === [...b].sort().join('\u0000');

async function main() {
  const src = process.argv[2] || './ocs-answerer-wrapper.json';
  const raw = /^https?:/.test(src) ? await (await fetch(src)).text() : readFileSync(src, 'utf8');
  const wrappers = JSON.parse(raw);
  if (!Array.isArray(wrappers) || !wrappers.length) {
    console.error('配置必须是 AnswererWrapper 非空数组');
    process.exit(1);
  }

  console.log(`配置: ${src}  (${wrappers.length} 个题库)\n${'='.repeat(66)}`);
  let fail = 0;
  let pass = 0;

  for (const c of CASES) {
    console.log(`\n▶ ${c.label} — ${c.title.slice(0, 24)}…`);
    const env = { title: c.title, type: c.type, options: c.options.join('\n') };

    for (const w of wrappers) {
      let out;
      try {
        out = await runWrapper(w, env);
      } catch (e) {
        console.log(`  ✗ ${w.name}: ${e.message}`);
        fail++;
        continue;
      }

      if (!out.results.length) {
        console.log(`  · ${w.name}: 返回 undefined（OCS 视为未命中）`);
        continue;
      }

      // 协议层：答案必须是字符串
      const bad = out.results.filter((r) => r.answer != null && typeof r.answer !== 'string');
      if (bad.length) {
        console.log(`  ✗ ${w.name}: answer 类型错误（${typeof bad[0].answer}），OCS 要求 string`);
        fail++;
        continue;
      }

      const answers = out.results.map((r) => r.answer).filter((a) => a);
      if (!answers.length) {
        console.log(`  · ${w.name}: 答案为空`);
        continue;
      }

      // 判题层：交给 OCS resolver
      const blankCount = c.type === 'completion' ? Math.max(1, c.options.length) : c.options.length;
      const resolved = resolveAsOcs(c.type, answers, c.options, undefined);

      if (!resolved.finish) {
        console.log(`  ✗ ${w.name}: 答案 "${answers[0]}" 无法命中任何选项（resolver finish=false）`);
        fail++;
        continue;
      }

      const got =
        c.type === 'completion' ? resolved.answers : c.type === 'single' || c.type === 'judgement' ? [resolved.option] : resolved.options;

      if (!c.expect) {
        console.log(`  ✓ ${w.name}: "${answers[0]}" → 落到 ${JSON.stringify(got)}`);
        pass++;
      } else if (sameSet(got, c.expect)) {
        console.log(`  ✓ ${w.name}: "${answers[0]}" → 落到 ${JSON.stringify(got)}`);
        pass++;
      } else {
        console.log(`  ✗ ${w.name}: "${answers[0]}" → 落到 ${JSON.stringify(got)}，预期 ${JSON.stringify(c.expect)}`);
        fail++;
      }
    }
  }

  console.log(`\n${'='.repeat(66)}`);
  console.log(`通过 ${pass} 项，失败 ${fail} 项`);
  console.log(fail === 0 ? '✅ 端到端就绪：粘进 OCS 即可答题' : '❌ 存在不通过的项，见上');
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});