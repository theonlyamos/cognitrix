#!/usr/bin/env node
// Moves English UI text out of the React components into src/locales/ui.en.ts
// and swaps each piece for a `tr('ui.…')` call.
//
//   node scripts/i18n-extract.mjs            # rewrite every component
//   node scripts/i18n-extract.mjs src/pages/Foo.tsx …
//   node scripts/i18n-extract.mjs --check    # list text still to extract, change nothing
//
// It is safe to run again: text already behind tr() is left alone, and the
// same English in the same file always gets the same key. After a run, add
// French and Spanish for the new keys with scripts/i18n-translate.mjs.
//
// What it picks up:
//   - text between JSX tags, with simple {expressions} turned into {placeholders}
//   - string and template literals rendered as children or in text props
//     (title, label, placeholder, aria-label, …), including both sides of
//     `cond ? 'A' : 'B'` and the right side of `x || 'A'`
//   - sentence-like first arguments of calls (setError('Could not …'), confirm(…))
//   - text properties (label, title, description, …) of object literals; at
//     module level they become getters so they follow the language at render time
// Text inside <code>, <kbd> and <pre> is left as is. Add `// i18n-ignore` on
// the line above (or at the end of the line) to keep a literal untouched.

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const SRC = path.join(ROOT, 'src');
const EN_FILE = path.join(SRC, 'locales', 'ui.en.ts');

const args = process.argv.slice(2);
const CHECK = args.includes('--check');
const fileArgs = args.filter((a) => !a.startsWith('--'));

// ---------------------------------------------------------------- catalogue

export function readCatalogue(file) {
  if (!fs.existsSync(file)) return {};
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const out = {};
  const visit = (node) => {
    if (ts.isPropertyAssignment(node) && (ts.isStringLiteral(node.name) || ts.isIdentifier(node.name))) {
      const init = node.initializer;
      if (ts.isStringLiteral(init) || ts.isNoSubstitutionTemplateLiteral(init)) out[node.name.text] = init.text;
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

export function writeCatalogue(file, entries, header) {
  const keys = Object.keys(entries).sort();
  const lines = keys.map((k) => `  ${JSON.stringify(k)}: ${JSON.stringify(entries[k])},`);
  fs.writeFileSync(file, `${header}\n${lines.join('\n')}\n`.replace('__BODY__', ''), 'utf8');
}

const EN_HEADER_TOP = `// English text of the app's screens, pulled out of the components by
// scripts/i18n-extract.mjs. Edit the English here or in the component and run
// the script again; fr and es live next to this file (ui.fr.ts, ui.es.ts).
const uiEn = {`;
const EN_HEADER_BOTTOM = `} as const;

export default uiEn;
`;

function writeEn(entries) {
  const keys = Object.keys(entries).sort();
  const body = keys.map((k) => `  ${JSON.stringify(k)}: ${JSON.stringify(entries[k])},`).join('\n');
  fs.writeFileSync(EN_FILE, `${EN_HEADER_TOP}\n${body}\n${EN_HEADER_BOTTOM}`, 'utf8');
}

// ---------------------------------------------------------------- helpers

const TEXT_ATTRS = new Set([
  'title', 'subtitle', 'placeholder', 'aria-label', 'aria-description', 'alt', 'label', 'description',
  'hint', 'text', 'message', 'heading', 'caption', 'tooltip', 'help', 'summary', 'detail', 'desc',
  'note', 'emptyLabel', 'emptyText', 'emptyTitle', 'confirmLabel', 'cancelLabel', 'actionLabel',
  'buttonLabel', 'valuePlaceholder', 'empty', 'question', 'body', 'prompt' /* UI prompt props only */,
]);
const TEXT_ATTR_SUFFIX = /(Label|Text|Title|Message|Hint|Description|Placeholder|Tooltip|Caption)$/;
const TEXT_PROPS = new Set([
  'label', 'title', 'description', 'detail', 'desc', 'hint', 'help', 'placeholder', 'subtitle',
  'summary', 'message', 'emptyLabel', 'heading', 'caption', 'tooltip', 'blurb', 'note', 'cta',
  'action', 'actionLabel', 'buttonLabel', 'confirmLabel', 'question', 'explain', 'why', 'tagline',
]);
const SKIP_PARENT_TAGS = new Set(['code', 'kbd', 'pre', 'style', 'script', 'samp', 'var']);
const SKIP_CALLEES = /^(api\.|fetch$|console\.|localStorage\.|sessionStorage\.|document\.|screen\.|expect$|describe$|it$|test$|vi\.|cn$|clsx$|tr$|t$|translate$|require$|import$|URL$|RegExp$|navigate$|JSON\.|Symbol$|emit$|dispatch|addEventListener|removeEventListener|setItem$|getItem$|querySelector|startsWith|endsWith|includes|split|replace|match|test|indexOf|join|padStart|toLocale|Intl\.|new Date|postMessage|invoke$|listen$|open$|window\.open$|encodeURIComponent|decodeURIComponent|useResource$|useState$|useRef$|setSearchParams|get$|set$|has$|delete$|Number$|String$|parseInt|parseFloat|copy$|writeText|download|send$|ws\.|socket\.|track$|log$|createElement|setAttribute|getAttribute|matchMedia|key$)/;

// Methods that compare or look up data: their arguments are ids, not text.
const SKIP_METHODS = /^(has|includes|indexOf|lastIndexOf|startsWith|endsWith|get|set|delete|add|find|findIndex|filter|some|every|split|replace|replaceAll|match|matchAll|test|localeCompare|getItem|setItem|removeItem|querySelector|querySelectorAll|closest|append|getAll|search|exec)$/;

const hasLetter = (s) => /\p{L}/u.test(s);
const looksLikeClass = (s) => {
  const tokens = s.trim().split(/\s+/);
  return tokens.every((t) => /^[a-z0-9:\-\[\]\/.%#!_()&>*=,'"]+$/.test(t)) && tokens.some((t) => /[-:\[]/.test(t));
};
const looksTechnical = (s) =>
  /^(https?:|mailto:|data:|\/|\.\/|#|@|\$|\{)/.test(s.trim()) || /^[\w.-]+\.(json|md|ts|tsx|js|py|png|svg|txt|csv|html)$/.test(s.trim()) ||
  /^[A-Z0-9_]{2,}$/.test(s.trim()) || /^[a-z0-9]+(_[a-z0-9]+)+$/.test(s.trim()) || /^[a-z]+([A-Z][a-z0-9]*)+$/.test(s.trim()) ||
  /^[a-z0-9-]+\/[a-z0-9-]+/.test(s.trim()) || /^\w+:\w+/.test(s.trim()) || /^[a-z][a-z0-9]*(-[a-z0-9]+)+$/.test(s.trim()) ||
  /^[a-z0-9]+(\.[a-z0-9]+)+$/i.test(s.trim()) || /^(GET|POST|PUT|PATCH|DELETE)\b/.test(s.trim()) || /^\s*[\[{<]/.test(s);
/** A single token that is a name, a key, an address or a code sample. */
const looksTechnicalWord = (s) => {
  const t = s.trim();
  if (/\s/.test(t)) return /^(https?:|\/\w|[\w.-]+@[\w.-]+\.\w+$)/.test(t) || /^[A-Z][A-Z0-9_]*_[A-Z0-9_]+=/.test(t);
  return t.length < 2 || /[_@\/=<>]|:\w|^[.\W]|^https?|\.\w{2,}$|^[A-Z]+-[A-Z]|â/.test(t) || /^[a-z]+-[a-z-]+$/.test(t) ||
    /^(npx|pnpm|npm|pip|uv|git|curl|docker|notion|params|v\d*)$/.test(t) || /\d\.\d/.test(t) || /^[A-Z]{1,2}$/.test(t) ||
    (/…$/.test(t) && /[-_:\d]|^[A-Z]{3}/.test(t));
};

/** Text a person reads, as opposed to an id, a class list or a path. */
function isUiText(s, { loose = false } = {}) {
  if (!s || !hasLetter(s)) return false;
  if (looksLikeClass(s)) return false;
  if (looksTechnicalWord(s)) return false;
  if (loose) return true; // already in a rendered position
  if (looksTechnical(s)) return false;
  const words = s.trim().split(/\s+/);
  if (words.length >= 2) return /^[\p{Lu}\p{N}"“'(‘]/u.test(s.trim()) || /[.…!?:]$/.test(s.trim());
  return /^\p{Lu}\p{Ll}+[.…!?]?$/u.test(s.trim()) || /…$/.test(s.trim());
}

function hash4(text) {
  return crypto.createHash('sha1').update(text).digest('hex').slice(0, 4);
}

function namespaceFor(file) {
  let rel = path.relative(SRC, file).replace(/\\/g, '/').replace(/\.(tsx?|jsx?)$/, '');
  rel = rel.replace(/^(pages|components|context|hooks)\//, '').replace(/^lib\//, 'lib/');
  return rel
    .split('/')
    .map((seg) => seg.replace(/[-_ ](\w)/g, (_, c) => c.toUpperCase()).replace(/^\w/, (c) => c.toLowerCase()))
    .join('.');
}

function slugFor(text) {
  const words = text
    .replace(/\{(\w+)\}/g, ' $1 ')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .match(/[A-Za-z0-9]+/g) ?? [];
  const picked = words.slice(0, 5).map((w, i) => (i === 0 ? w.toLowerCase() : w[0].toUpperCase() + w.slice(1).toLowerCase()));
  const slug = picked.join('') || 'text';
  return /^\d/.test(slug) ? `n${slug}` : slug;
}

/** Same rules React/Babel use to turn raw JSX text into what is rendered. */
function cleanJsxText(value) {
  const lines = value.split(/\r\n|\n|\r/);
  let lastNonEmpty = 0;
  lines.forEach((l, i) => { if (/[^ \t]/.test(l)) lastNonEmpty = i; });
  let out = '';
  lines.forEach((line, i) => {
    const isFirst = i === 0;
    const isLast = i === lines.length - 1;
    let trimmed = line.replace(/\t/g, ' ');
    if (!isFirst) trimmed = trimmed.replace(/^[ ]+/, '');
    if (!isLast) trimmed = trimmed.replace(/[ ]+$/, '');
    if (trimmed) {
      if (i !== lastNonEmpty) trimmed += ' ';
      out += trimmed;
    }
  });
  return decodeEntities(out);
}

function decodeEntities(s) {
  return s
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&rsquo;/g, '’').replace(/&lsquo;/g, '‘')
    .replace(/&ldquo;/g, '“').replace(/&rdquo;/g, '”').replace(/&middot;/g, '·').replace(/&hellip;/g, '…')
    .replace(/&mdash;/g, '—').replace(/&ndash;/g, '–').replace(/&times;/g, '×').replace(/&rarr;/g, '→')
    .replace(/&larr;/g, '←').replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)));
}

function placeholderName(expr) {
  let e = expr;
  while (ts.isParenthesizedExpression(e) || ts.isNonNullExpression(e) || ts.isAsExpression(e)) e = e.expression;
  if (ts.isIdentifier(e)) return e.text;
  if (ts.isPropertyAccessExpression(e)) return e.name.text;
  if (ts.isElementAccessExpression(e)) return 'item';
  if (ts.isCallExpression(e)) {
    const c = e.expression;
    if (ts.isIdentifier(c)) return c.text.replace(/^(get|format|to)/, '').replace(/^\w/, (x) => x.toLowerCase()) || 'value';
    if (ts.isPropertyAccessExpression(c)) {
      if (['toLocaleString', 'toString', 'toFixed', 'trim', 'toLowerCase', 'toUpperCase', 'join'].includes(c.name.text)) return placeholderName(c.expression);
      return c.name.text.replace(/^(get|format|to)/, '').replace(/^\w/, (x) => x.toLowerCase()) || 'value';
    }
  }
  if (ts.isBinaryExpression(e) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(e.operatorToken.kind)) return placeholderName(e.left);
  return 'value';
}

function containsJsx(node) {
  let found = false;
  const visit = (n) => {
    if (found) return;
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) { found = true; return; }
    ts.forEachChild(n, visit);
  };
  visit(node);
  return found;
}

function tagName(el) {
  const opening = ts.isJsxElement(el) ? el.openingElement : el;
  return opening.tagName?.getText?.() ?? '';
}

function isIgnored(sf, node) {
  const text = sf.text;
  const start = node.getStart(sf);
  const lineStart = text.lastIndexOf('\n', start - 1) + 1;
  const prevLineStart = text.lastIndexOf('\n', lineStart - 2) + 1;
  const lineEnd = text.indexOf('\n', start);
  const here = text.slice(lineStart, lineEnd < 0 ? undefined : lineEnd);
  const prev = text.slice(prevLineStart, lineStart);
  return /i18n-ignore/.test(here) || /i18n-ignore/.test(prev);
}

function insideFunction(node) {
  for (let p = node.parent; p; p = p.parent) {
    if (ts.isFunctionLike(p)) return true;
  }
  return false;
}

// ---------------------------------------------------------------- per file

class Keys {
  constructor(catalogue) {
    this.catalogue = catalogue; // key -> english (whole app)
    this.byText = new Map(); // ns|text -> key
    for (const [k, v] of Object.entries(catalogue)) {
      const ns = k.split('.').slice(1, -1).join('.');
      this.byText.set(`${ns}|${v}`, k);
    }
    this.added = 0;
  }
  keyFor(ns, text) {
    const known = this.byText.get(`${ns}|${text}`);
    if (known) return known;
    let key = `ui.${ns}.${slugFor(text)}`;
    if (this.catalogue[key] !== undefined && this.catalogue[key] !== text) key = `${key}_${hash4(text)}`;
    this.catalogue[key] = text;
    this.byText.set(`${ns}|${text}`, key);
    this.added++;
    return key;
  }
}

function varsSource(vars) {
  if (!vars.length) return '';
  const parts = vars.map(([name, src]) => (name === src ? name : `${name}: ${src}`));
  return `, { ${parts.join(', ')} }`;
}

function call(key, vars) {
  return `tr('${key}'${varsSource(vars)})`;
}

/** Build "text {name} text" from a template literal; null when it can't be done cleanly. */
function templateMessage(sf, node) {
  const vars = [];
  const names = new Map();
  const nameFor = (expr) => {
    const src = expr.getText(sf);
    if (names.has(src)) return names.get(src);
    let base = placeholderName(expr);
    let name = base;
    let n = 2;
    while ([...names.values()].includes(name)) name = `${base}${n++}`;
    names.set(src, name);
    vars.push([name, src]);
    return name;
  };
  let msg = node.head.text;
  for (const span of node.templateSpans) {
    if (containsJsx(span.expression)) return null;
    msg += `{${nameFor(span.expression)}}${span.literal.text}`;
  }
  const statics = [node.head.text, ...node.templateSpans.map((s) => s.literal.text)].join(' ');
  if (!hasLetter(statics)) return null;
  if (/[\p{L}]\{\w+\}|\{\w+\}[\p{L}]/u.test(msg)) return null; // glued plural like agent{s}
  if (/[{}]/.test(msg.replace(/\{\w+\}/g, ''))) return null;
  return { msg, vars };
}

function literalText(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  return null;
}

/** Where a literal ends up, walking out through ?:, ||, ?? and (). */
function literalContext(node) {
  let cur = node;
  let p = node.parent;
  while (p) {
    if (ts.isParenthesizedExpression(p)) { cur = p; p = p.parent; continue; }
    if (ts.isConditionalExpression(p) && p.condition !== cur) { cur = p; p = p.parent; continue; }
    if (ts.isBinaryExpression(p) && p.right === cur &&
      [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.AmpersandAmpersandToken].includes(p.operatorToken.kind)) {
      cur = p; p = p.parent; continue;
    }
    if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.BarBarToken && p.left === cur) { cur = p; p = p.parent; continue; }
    break;
  }
  return { outer: cur, parent: p };
}

function attrAllowed(name) {
  return TEXT_ATTRS.has(name) || TEXT_ATTR_SUFFIX.test(name);
}

function propName(prop) {
  const n = prop.name;
  if (!n) return '';
  if (ts.isIdentifier(n) || ts.isStringLiteral(n)) return n.text;
  return '';
}

function inApiPayload(node) {
  for (let p = node.parent; p; p = p.parent) {
    if (ts.isCallExpression(p)) {
      const callee = p.expression.getText();
      if (/^(api\.|fetch|axios|JSON\.|post|put|patch|send|invoke)/.test(callee)) return true;
      if (/^(set[A-Z]|update|save)/.test(callee) && !/Error|Message|Notice|Status/.test(callee)) return true;
    }
    if (ts.isFunctionLike(p)) return false;
  }
  return false;
}

function processPass1(file, sf, keys, ns, report) {
  const edits = [];
  const taken = [];
  const overlaps = (s, e) => taken.some(([a, b]) => s < b && e > a);
  const add = (start, end, text) => {
    if (overlaps(start, end)) return;
    taken.push([start, end]);
    edits.push({ start, end, text });
  };

  const visit = (node) => {
    // object literal text props
    if (ts.isPropertyAssignment(node) && TEXT_PROPS.has(propName(node))) {
      const init = node.initializer;
      const text = literalText(init);
      const tmpl = ts.isTemplateExpression(init) ? templateMessage(sf, init) : null;
      const message = text ?? tmpl?.msg;
      if (message && isUiText(message.replace(/\{\w+\}/g, 'X')) && !isIgnored(sf, node) && !inApiPayload(node)) {
        const key = report(keys.keyFor(ns, message));
        const expr = call(key, tmpl?.vars ?? []);
        if (insideFunction(node)) add(init.getStart(sf), init.getEnd(), expr);
        else if (!tmpl) add(node.getStart(sf), node.getEnd(), `get ${node.name.getText(sf)}() { return ${expr}; }`);
        return;
      }
    }

    const text = literalText(node);
    const isTmpl = ts.isTemplateExpression(node);
    if ((text !== null || isTmpl) && !isIgnored(sf, node)) {
      const tmpl = isTmpl ? templateMessage(sf, node) : null;
      const message = text ?? tmpl?.msg;
      if (message) {
        const { outer, parent } = literalContext(node);
        let ok = false;
        let wrapAttr = false;
        if (parent && ts.isJsxExpression(parent)) {
          const gp = parent.parent;
          if (ts.isJsxAttribute(gp)) ok = attrAllowed(gp.name.getText(sf)) && isUiText(message.replace(/\{\w+\}/g, 'X'), { loose: true });
          else if (ts.isJsxElement(gp) || ts.isJsxFragment(gp)) ok = !SKIP_PARENT_TAGS.has(ts.isJsxElement(gp) ? tagName(gp) : '') && isUiText(message.replace(/\{\w+\}/g, 'X'), { loose: true });
        } else if (parent && ts.isJsxAttribute(parent) && outer === node && text !== null) {
          ok = attrAllowed(parent.name.getText(sf)) && isUiText(message, { loose: true });
          wrapAttr = true;
        } else if (parent && (ts.isCallExpression(parent) || ts.isNewExpression(parent)) && parent.arguments?.includes(outer)) {
          const callee = parent.expression.getText(sf);
          const idx = parent.arguments.indexOf(outer);
          const method = callee.split('.').pop();
          if (!SKIP_CALLEES.test(callee) && !SKIP_METHODS.test(method) && !/^(it|describe|test)\b/.test(callee)) {
            const sentence = isUiText(message.replace(/\{\w+\}/g, 'X'));
            if (/^(set\w*(Error|Message|Notice|Status|Hint|Info|Warning|Feedback|Note|Flash|Toast)|errorMessage|confirm|alert|window\.confirm|window\.alert|prompt|window\.prompt|empty|notice|toast\w*|flash|announce)$/.test(callee)) ok = isUiText(message.replace(/\{\w+\}/g, 'X'), { loose: true });
            else if (method === 'push') ok = isUiText(message.replace(/\{\w+\}/g, 'X'), { loose: true }) && /\s/.test(message.trim());
            else ok = sentence && message.trim().split(/\s+/).length >= 2 && (idx === 0 || /Error$|errorMessage/.test(callee) || idx === parent.arguments.length - 1);
            if (ts.isNewExpression(parent) && !/Error$/.test(callee)) ok = false;
          }
        } else if (parent && ts.isReturnStatement(parent) && insideFunction(node)) {
          // `return 'Just now'` from a small label helper
          ok = isUiText(message.replace(/\{\w+\}/g, 'X')) && message.trim().split(/\s+/).length >= 2;
        }
        if (ok) {
          const key = report(keys.keyFor(ns, message));
          const expr = call(key, tmpl?.vars ?? []);
          add(node.getStart(sf), node.getEnd(), wrapAttr ? `{${expr}}` : expr);
          return;
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return edits;
}

function processPass2(file, sf, keys, ns, report) {
  const edits = [];
  const visitEl = (node) => {
    if (ts.isJsxElement(node) || ts.isJsxFragment(node)) {
      const name = ts.isJsxElement(node) ? tagName(node) : '';
      if (!SKIP_PARENT_TAGS.has(name)) handleChildren(node.children);
    }
    ts.forEachChild(node, visitEl);
  };

  const handleChildren = (children) => {
    let run = [];
    const flush = () => {
      if (run.length) handleRun(run);
      run = [];
    };
    for (const child of children) {
      if (ts.isJsxText(child)) run.push(child);
      else if (ts.isJsxExpression(child) && child.expression && !containsJsx(child.expression)) run.push(child);
      else flush();
    }
    flush();
  };

  const handleRun = (run) => {
    // trim pure-whitespace text at the edges
    while (run.length && ts.isJsxText(run[0]) && !cleanJsxText(run[0].text).trim()) run.shift();
    while (run.length && ts.isJsxText(run[run.length - 1]) && !cleanJsxText(run[run.length - 1].text).trim()) run.pop();
    if (!run.some((c) => ts.isJsxText(c) && hasLetter(cleanJsxText(c.text)))) return;
    if (run.some((c) => isIgnored(sf, c))) return;

    const vars = [];
    const names = new Map();
    let msg = '';
    for (const c of run) {
      if (ts.isJsxText(c)) msg += cleanJsxText(c.text);
      else {
        const expr = c.expression;
        const lit = literalText(expr);
        if (lit !== null) { msg += lit; continue; } // {' '} and friends
        const src = expr.getText(sf);
        let name = names.get(src);
        if (!name) {
          const base = placeholderName(expr);
          name = base;
          let n = 2;
          while ([...names.values()].includes(name)) name = `${base}${n++}`;
          names.set(src, name);
          vars.push([name, src]);
        }
        msg += `{${name}}`;
      }
    }
    const glued = /[\p{L}]\{\w+\}|\{\w+\}[\p{L}]/u.test(msg);
    if (glued || /[{}]/.test(msg.replace(/\{\w+\}/g, ''))) {
      // fall back: each bit of text alone
      for (const c of run) if (ts.isJsxText(c)) handleRun([c]);
      return;
    }
    const lead = /^\s/.test(msg);
    const trail = /\s$/.test(msg);
    const core = msg.trim();
    if (!core || !hasLetter(core.replace(/\{\w+\}/g, ''))) return;
    if (looksTechnicalWord(core) || looksLikeClass(core)) return;

    const first = run[0];
    const last = run[run.length - 1];
    let start = first.getStart(sf);
    let end = last.getEnd();
    let prefix = '';
    let suffix = '';
    if (ts.isJsxText(first)) {
      const raw = first.text.match(/^\s*/)[0];
      if (raw.includes('\n')) { prefix = raw; } else if (lead) prefix = "{' '}";
      start = first.pos;
    } else if (lead) prefix = "{' '}";
    if (ts.isJsxText(last)) {
      const raw = last.text.match(/\s*$/)[0];
      if (raw.includes('\n')) { suffix = raw; } else if (trail) suffix = "{' '}";
      end = last.end;
    } else if (trail) suffix = "{' '}";
    const key = report(keys.keyFor(ns, core));
    edits.push({ start, end, text: `${prefix}{${call(key, vars)}}${suffix}` });
  };

  visitEl(sf);
  return edits;
}

function applyEdits(text, edits) {
  const sorted = [...edits].sort((a, b) => b.start - a.start);
  let out = text;
  let lastStart = Infinity;
  for (const e of sorted) {
    if (e.end > lastStart) continue; // overlapping, keep the later one
    out = out.slice(0, e.start) + e.text + out.slice(e.end);
    lastStart = e.start;
  }
  return out;
}

function ensureImport(text) {
  if (!/\btr\(/.test(text)) return text;
  const sf = ts.createSourceFile('x.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let lastImportEnd = 0;
  for (const st of sf.statements) {
    if (ts.isImportDeclaration(st)) {
      lastImportEnd = st.getEnd();
      const from = st.moduleSpecifier.text;
      if (from === '@/lib/i18n' || from.endsWith('/i18n')) {
        const named = st.importClause?.namedBindings;
        if (named && ts.isNamedImports(named)) {
          if (named.elements.some((e) => e.name.text === 'tr')) return text;
          if (st.importClause.isTypeOnly) continue;
          const last = named.elements[named.elements.length - 1];
          return text.slice(0, last.getEnd()) + ', tr' + text.slice(last.getEnd());
        }
      }
    }
  }
  const line = "import { tr } from '@/lib/i18n';";
  if (!lastImportEnd) return `${line}\n${text}`;
  return text.slice(0, lastImportEnd) + '\n' + line + text.slice(lastImportEnd);
}

function listFiles() {
  if (fileArgs.length) return fileArgs.map((f) => path.resolve(f));
  const out = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (['locales', 'test', '__tests__'].includes(entry.name)) continue;
        if (full.includes(path.join('lib', 'components', 'ui'))) continue;
        walk(full);
      } else if (/\.tsx$/.test(entry.name) && !/\.(test|spec)\.tsx$/.test(entry.name)) out.push(full);
    }
  };
  walk(SRC);
  return out.sort();
}

function main() {
  const catalogue = readCatalogue(EN_FILE);
  const keys = new Keys(catalogue);
  const used = new Set();
  let changedFiles = 0;
  for (const file of listFiles()) {
    const ns = namespaceFor(file);
    let text = fs.readFileSync(file, 'utf8');
    const original = text;
    const report = (k) => { used.add(k); return k; };
    for (const pass of [processPass1, processPass2]) {
      const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const edits = pass(file, sf, keys, ns, report);
      if (edits.length) text = applyEdits(text, edits);
    }
    if (text !== original) {
      changedFiles++;
      if (CHECK) console.log(path.relative(ROOT, file));
      else fs.writeFileSync(file, ensureImport(text), 'utf8');
    }
  }
  if (!CHECK && !fileArgs.length) {
    // Drop keys no file uses any more. A key built at run time
    // (`ui.count.${word}s_one`) keeps every key that starts the same way.
    const exact = new Set();
    const prefixes = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) { if (entry.name !== 'locales') walk(full); continue; }
        if (!/\.(tsx?|jsx?)$/.test(entry.name)) continue;
        const text = fs.readFileSync(full, 'utf8');
        for (const m of text.matchAll(/['"`](ui\.[\w.]+)['"`]/g)) exact.add(m[1]);
        for (const m of text.matchAll(/`(ui\.[\w.]*)\$\{/g)) prefixes.push(m[1]);
      }
    };
    walk(SRC);
    let dropped = 0;
    for (const key of Object.keys(keys.catalogue)) {
      if (!exact.has(key) && !prefixes.some((p) => key.startsWith(p))) { delete keys.catalogue[key]; dropped++; }
    }
    if (dropped) console.error(`${dropped} unused key(s) dropped`);
  }
  if (!CHECK) writeEn(keys.catalogue);
  console.error(`${changedFiles} file(s) ${CHECK ? 'still have text to extract' : 'changed'}, ${keys.added} new key(s), ${Object.keys(keys.catalogue).length} in ui.en.ts`);
  if (CHECK && changedFiles) process.exitCode = 1;
}

main();
