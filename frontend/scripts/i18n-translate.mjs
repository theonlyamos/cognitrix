#!/usr/bin/env node
// Fills src/locales/ui.fr.ts and ui.es.ts with translations of any key in
// ui.en.ts they don't have yet, and drops keys English no longer has.
//
//   ANTHROPIC_API_KEY=… node scripts/i18n-translate.mjs [fr|es …] [--model claude-sonnet-5-5]
//
// Run it after scripts/i18n-extract.mjs. Translations already in the files are
// kept, so hand fixes stay. Without an API key it only prunes and reports.

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const LOCALES = path.join(ROOT, 'src', 'locales');
const NAMES = { fr: 'French', es: 'Spanish' };

const argv = process.argv.slice(2);
const modelAt = argv.indexOf('--model');
const MODEL = modelAt >= 0 ? argv[modelAt + 1] : 'claude-sonnet-5-5';
const languages = argv.filter((a, i) => !a.startsWith('--') && argv[i - 1] !== '--model');
const targets = languages.length ? languages : Object.keys(NAMES);
const API_KEY = process.env.ANTHROPIC_API_KEY || process.env.COG_ANTHROPIC_API_KEY;
const BATCH = 80;
const PARALLEL = 4;

function readEntries(file) {
  if (!fs.existsSync(file)) return {};
  const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
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

function writeLanguage(code, entries) {
  const keys = Object.keys(entries).sort();
  const body = keys.map((k) => `  ${JSON.stringify(k)}: ${JSON.stringify(entries[k])},`).join('\n');
  fs.writeFileSync(
    path.join(LOCALES, `ui.${code}.ts`),
    `// ${NAMES[code]} for ui.en.ts, written by scripts/i18n-translate.mjs. Fix any line by hand;
// the script keeps existing lines and only adds missing ones.
import type uiEn from './ui.en';

const ui: Record<keyof typeof uiEn, string> = {
${body}
};

export default ui;
`,
    'utf8',
  );
}

const placeholders = (s) => (s.match(/\{\w+\}/g) ?? []).sort().join(',');

function glossary(code) {
  // The hand-written menus, so screens use the same words.
  const en = readEntries(path.join(LOCALES, 'en.ts'));
  const other = readEntries(path.join(LOCALES, `${code}.ts`));
  return Object.keys(en)
    .filter((k) => !k.startsWith('ui.') && other[k])
    .map((k) => `${en[k]} => ${other[k]}`)
    .join('\n');
}

async function translateBatch(code, batch, terms) {
  const system = `You translate the interface of Cognitrix, an app where people chat with AI agents, give them tasks, tools and a sandboxed computer. Translate UI strings from English to ${NAMES[code]}.
Rules:
- Keep every {placeholder} exactly as written, and keep the same leading and trailing spaces as the English.
- Keep product and brand names, code, commands, file names, environment variables, keyboard keys, model names and URLs unchanged.
- Short labels stay short. Use the informal-neutral register common in modern apps (${code === 'fr' ? 'vouvoiement' : 'tú'}). Keep ALL-CAPS headings in caps, keep lowercase labels lowercase, and keep ending punctuation (… . : ?).
- Some strings are fragments of a sentence split around a link or bold text; translate them so they still read in order.
- The key hints at the screen the text is on.
- Use these existing translations for consistency:
${terms}
Reply with only a JSON object mapping each key to its translation.`;
  const user = JSON.stringify(Object.fromEntries(batch), null, 1);
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const res = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-api-key': API_KEY, 'anthropic-version': '2023-06-01' },
        body: JSON.stringify({ model: MODEL, max_tokens: 16000, system, messages: [{ role: 'user', content: user }] }),
      });
      if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`);
      const data = await res.json();
      const text = data.content.filter((c) => c.type === 'text').map((c) => c.text).join('');
      const json = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1));
      const out = {};
      for (const [key, english] of batch) {
        const value = json[key];
        if (typeof value === 'string' && value.trim() && placeholders(value) === placeholders(english)) {
          // keep the edge spaces the English relies on
          const lead = english.match(/^\s*/)[0];
          const trail = english.match(/\s*$/)[0];
          out[key] = lead + value.trim() + trail;
        }
      }
      return out;
    } catch (e) {
      console.error(`  ${code} batch failed (attempt ${attempt}): ${e.message}`);
      await new Promise((r) => setTimeout(r, 2000 * attempt));
    }
  }
  return {};
}

async function main() {
  const en = readEntries(path.join(LOCALES, 'ui.en.ts'));
  for (const code of targets) {
    if (!NAMES[code]) throw new Error(`unknown language ${code}`);
    const current = readEntries(path.join(LOCALES, `ui.${code}.ts`));
    const entries = Object.fromEntries(Object.entries(current).filter(([k]) => k in en));
    const missing = Object.entries(en).filter(([k]) => !(k in entries));
    console.error(`${code}: ${missing.length} to translate, ${Object.keys(current).length - Object.keys(entries).length} dropped`);
    if (missing.length && API_KEY) {
      const terms = glossary(code);
      const batches = [];
      for (let i = 0; i < missing.length; i += BATCH) batches.push(missing.slice(i, i + BATCH));
      let next = 0;
      const worker = async () => {
        while (next < batches.length) {
          const i = next++;
          Object.assign(entries, await translateBatch(code, batches[i], terms));
          console.error(`  ${code}: batch ${i + 1}/${batches.length} done`);
        }
      };
      await Promise.all(Array.from({ length: PARALLEL }, worker));
    }
    const still = Object.keys(en).filter((k) => !(k in entries));
    if (still.length) console.error(`${code}: ${still.length} still missing${API_KEY ? ' (run again)' : ' (set ANTHROPIC_API_KEY)'}`);
    writeLanguage(code, entries);
  }
}

await main();
