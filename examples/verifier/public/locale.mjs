// SPDX-License-Identifier: Apache-2.0
// Display language only. Never translate transaction data, editable fields,
// raw evidence, JSON artifacts or contract/runtime values.
import { COPY } from './locale-copy.mjs';

const EN_ZH = new Map(COPY);
const ZH_EN = new Map(COPY.map(([en, zh]) => [zh, en]));
const LONG_EN = [...EN_ZH].filter(([en, zh]) =>
  en.length >= 13 && en !== zh && /[A-Za-z]/.test(en)).sort((a, b) => b[0].length - a[0].length);
const LONG_ZH = [...ZH_EN].filter(([zh, en]) =>
  zh.length >= 5 && zh !== en && /[\u3400-\u9fff]/.test(zh)).sort((a, b) => b[0].length - a[0].length);
const OMIT = 'script,style,pre,code,kbd,samp,textarea,input,option,[data-i18n-raw]';
const canonical = new WeakMap();
const attributeCache = new WeakMap();
const KEY = 'agentledger.ui-language.v1';
let currentLang = 'en', observer, queued = false;
let translating = false;
const valid = value => value === 'en' || value === 'zh';
function preference(fallback) {
  try {
    const query = new URLSearchParams(location.search).get('lang');
    if (valid(query)) { localStorage.setItem(KEY, query); return query; }
    const saved = localStorage.getItem(KEY);
    if (valid(saved)) return saved;
  } catch { /* Browser storage can be disabled; switching still works. */ }
  return fallback;
}
function mapString(value, lang) {
  const map = lang === 'zh' ? EN_ZH : ZH_EN;
  const exact = map.get(value);
  if (exact !== undefined) return exact;
  // Dynamic status messages combine fixed copy with IDs / hashes / amounts.
  // Only long, known literals are substituted; never rewrite identifiers.
  const parts = lang === 'zh' ? LONG_EN : LONG_ZH;
  let result = value;
  for (const [source, target] of parts) {
    if (result.includes(source)) result = result.split(source).join(target);
  }
  return result;
}
export function localize(value, lang = currentLang) {
  if (typeof value !== 'string') return value;
  const whitespace = /^(\s*)([\s\S]*?)(\s*)$/.exec(value);
  const core = whitespace?.[2] ?? value;
  return (whitespace?.[1] ?? '') + mapString(core, lang) + (whitespace?.[3] ?? '');
}
export function language() { return currentLang; }
function ignored(node) {
  const parent = node.nodeType === 3 ? node.parentElement : node;
  return Boolean(parent?.closest(OMIT));
}
function translateText(node) {
  if (ignored(node) || !node.nodeValue?.trim()) return;
  let record = canonical.get(node);
  if (!record || node.nodeValue !== record.rendered) record = { original: node.nodeValue, rendered: node.nodeValue };
  const output = localize(record.original);
  if (node.nodeValue !== output) node.nodeValue = output;
  record.rendered = output;
  canonical.set(node, record);
}
function translateAttrs(element) {
  if (!element.hasAttribute || element.matches(OMIT)) return;
  let records = attributeCache.get(element) ?? {};
  for (const attr of ['placeholder', 'title', 'aria-label', 'aria-description']) {
    const value = element.getAttribute(attr);
    if (!value) continue;
    if (!records[attr] || records[attr].rendered !== value) records[attr] = { original: value, rendered: value };
    const output = localize(records[attr].original);
    if (output !== value) element.setAttribute(attr, output);
    records[attr].rendered = output;
  }
  attributeCache.set(element, records);
}
function render() {
  if (translating) return;
  translating = true;
  observer?.disconnect();
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  const switcher = document.getElementById('language-switch');
  if (switcher) {
    switcher.textContent = currentLang === 'zh' ? 'English' : '中文';
    switcher.setAttribute('aria-label', currentLang === 'zh' ? 'Switch to English' : '切换为中文');
    switcher.setAttribute('title', currentLang === 'zh' ? 'Switch to English' : '切换为中文');
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) translateText(walker.currentNode);
  for (const element of document.body.querySelectorAll('*')) translateAttrs(element);
  const title = document.title;
  let titleRecord = attributeCache.get(document.documentElement) ?? {};
  if (!titleRecord.title || titleRecord.title.rendered !== title)
    titleRecord.title = { original: title, rendered: title };
  const newTitle = localize(titleRecord.title.original);
  if (title !== newTitle) document.title = newTitle;
  titleRecord.title.rendered = newTitle;
  attributeCache.set(document.documentElement, titleRecord);
  translating = false;
  observer?.observe(document.body, { childList: true, subtree: true, characterData: true });
}
function scheduleRender() {
  if (queued) return;
  queued = true;
  queueMicrotask(() => { queued = false; render(); });
}
export function setLanguage(next) {
  if (!valid(next)) return;
  currentLang = next;
  try { localStorage.setItem(KEY, next); } catch { /* no storage permission */ }
  render();
  document.dispatchEvent(new CustomEvent('agentledger:language', { detail: { language: next } }));
}
export function initLanguage({ fallback = 'en' } = {}) {
  currentLang = preference(fallback);
  const button = document.getElementById('language-switch');
  if (button) button.addEventListener('click', () => setLanguage(currentLang === 'zh' ? 'en' : 'zh'));
  observer = new MutationObserver(scheduleRender);
  render();
  return { language: currentLang, setLanguage };
}
