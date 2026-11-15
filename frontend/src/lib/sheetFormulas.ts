// Sheet formulas, computed in the editor as people type. This is the same
// evaluator as cognitrix/artifact_pages/documents.py (Sheet): keep the two in
// step so what a person sees is what the agent reads.

export type CellValue = string | number | boolean | null;
export type CellFormat = 'number' | 'currency' | 'percent' | 'text';
export interface CellStyle { bold?: boolean; fmt?: CellFormat; align?: 'left' | 'center' | 'right' }
export interface SheetTab {
  name: string;
  cells: Record<string, CellValue>;
  styles?: Record<string, CellStyle>;
  widths?: Record<string, number>;
}
export interface SheetData { tabs: SheetTab[] }

export const MAX_ROWS = 5000;
export const MAX_COLS = 100;
const MAX_CELLS = 50_000;

export function colName(index: number): string {
  let out = '';
  let n = index + 1;
  while (n) {
    const rest = (n - 1) % 26;
    out = String.fromCharCode(65 + rest) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

export function colIndex(letters: string): number {
  let n = 0;
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

export const refOf = (row: number, col: number): string => `${colName(col)}${row + 1}`;

/** 'B3' -> [row 2, col 1], zero-based; null when it isn't a cell in range. */
export function splitRef(ref: string): [number, number] | null {
  const match = /^([A-Z]{1,3})([1-9][0-9]{0,5})$/.exec(ref.replace(/\$/g, '').toUpperCase());
  if (!match) return null;
  const col = colIndex(match[1]);
  const row = Number(match[2]) - 1;
  if (col >= MAX_COLS || row >= MAX_ROWS) return null;
  return [row, col];
}

class FormulaError extends Error {
  constructor(public code: string) { super(code); }
}

type Value = CellValue | Value[];
type Token = [kind: 'num' | 'str' | 'ref' | 'name' | 'op', text: string];

const TOKEN = new RegExp(
  '\\s*(?:' +
  '(\\d+(?:\\.\\d+)?|\\.\\d+)' +
  '|("(?:[^"]|"")*")' +
  "|((?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)!)?\\$?[A-Za-z]{1,3}\\$?[0-9]{1,6}(?::\\$?[A-Za-z]{1,3}\\$?[0-9]{1,6})?)" +
  '|([A-Za-z_][A-Za-z0-9_.]*)' +
  '|(<>|<=|>=|[-+*/^&=<>(),:%])' +
  ')', 'y');
const KINDS = ['num', 'str', 'ref', 'name', 'op'] as const;

function tokens(src: string): Token[] {
  const out: Token[] = [];
  const text = src.trimEnd();
  let pos = 0;
  while (pos < text.length) {
    TOKEN.lastIndex = pos;
    const match = TOKEN.exec(text);
    if (!match || TOKEN.lastIndex === pos) throw new FormulaError('#ERROR!');
    pos = TOKEN.lastIndex;
    const at = match.slice(1).findIndex((g) => g !== undefined);
    out.push([KINDS[at], match[at + 1]]);
  }
  return out;
}

const isError = (v: unknown): v is string => typeof v === 'string' && v.startsWith('#') && (v.endsWith('!') || v.endsWith('?'));

function num(value: Value): number {
  if (value === null || value === '') return 0;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number') return value;
  if (Array.isArray(value)) throw new FormulaError('#VALUE!');
  const trimmed = value.trim();
  const n = Number(trimmed);
  if (trimmed === '' || Number.isNaN(n)) throw new FormulaError('#VALUE!');
  return n;
}

function text(value: Value): string {
  if (value === null) return '';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(value);
  if (Array.isArray(value)) throw new FormulaError('#VALUE!');
  return value;
}

function truthy(value: Value): boolean {
  if (typeof value === 'string') {
    const upper = value.toUpperCase();
    if (upper === 'TRUE' || upper === 'FALSE') return upper === 'TRUE';
    return num(value) !== 0;
  }
  if (Array.isArray(value)) return value.length > 0;
  return !!value;
}

const flat = (values: Value[]): Value[] => values.flatMap((v) => (Array.isArray(v) ? flat(v) : [v]));

function numbers(values: Value[]): number[] {
  const out: number[] = [];
  for (const v of flat(values)) {
    if (typeof v === 'boolean' || v === null || v === '') continue;
    if (typeof v === 'number') out.push(v);
    else if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) out.push(Number(v));
  }
  return out;
}

function round(x: number, digits = 0): number {
  const q = 10 ** Math.trunc(digits);
  return (Math.floor(Math.abs(x) * q + 0.5) / q) * (x >= 0 ? 1 : -1);
}

const FUNCS: Record<string, (a: Value[]) => Value> = {
  SUM: (a) => numbers(a).reduce((s, x) => s + x, 0),
  AVERAGE: (a) => {
    const n = numbers(a);
    if (!n.length) throw new FormulaError('#DIV/0!');
    return n.reduce((s, x) => s + x, 0) / n.length;
  },
  MIN: (a) => (numbers(a).length ? Math.min(...numbers(a)) : 0),
  MAX: (a) => (numbers(a).length ? Math.max(...numbers(a)) : 0),
  COUNT: (a) => numbers(a).length,
  COUNTA: (a) => flat(a).filter((v) => v !== null && v !== '').length,
  ABS: (a) => (a.length ? Math.abs(num(a[0])) : 0),
  ROUND: (a) => (a.length ? round(num(a[0]), a.length > 1 ? num(a[1]) : 0) : 0),
  CONCAT: (a) => flat(a).map(text).join(''),
  LEN: (a) => (a.length ? text(a[0]).length : 0),
  UPPER: (a) => (a.length ? text(a[0]).toUpperCase() : ''),
  LOWER: (a) => (a.length ? text(a[0]).toLowerCase() : ''),
  AND: (a) => flat(a).every(truthy),
  OR: (a) => flat(a).some(truthy),
  NOT: (a) => (a.length ? !truthy(a[0]) : true),
};

const BINARY: Record<string, number> = { '=': 1, '<>': 1, '<': 1, '>': 1, '<=': 1, '>=': 1, '&': 2, '+': 3, '-': 3, '*': 4, '/': 4, '^': 5 };

/** Computed values of a sheet's cells: formulas evaluated, errors as "#CODE!". */
export class Sheet {
  private tabs = new Map<string, SheetTab>();
  private cache = new Map<string, CellValue>();
  private busy = new Set<string>();

  constructor(data: SheetData) {
    for (const tab of data.tabs) this.tabs.set(tab.name.toLowerCase(), tab);
  }

  shown(tab: string, ref: string): CellValue {
    try {
      return this.value(tab, ref);
    } catch (e) {
      if (e instanceof FormulaError) return e.code;
      throw e;
    }
  }

  value(tab: string, ref: string): CellValue {
    const key = `${tab.toLowerCase()}!${ref}`;
    if (this.cache.has(key)) return this.cache.get(key)!;
    const sheet = this.tabs.get(tab.toLowerCase());
    if (!sheet) throw new FormulaError('#REF!');
    const raw = sheet.cells[ref] ?? null;
    if (!(typeof raw === 'string' && raw.startsWith('='))) return raw;
    if (this.busy.has(key)) throw new FormulaError('#CYCLE!');
    this.busy.add(key);
    let result: CellValue;
    try {
      result = new Formula(this, tab.toLowerCase(), raw.slice(1)).run();
      if (typeof result === 'number' && !Number.isFinite(result)) result = '#VALUE!';
    } catch (e) {
      if (e instanceof FormulaError) result = e.code;
      else if (e instanceof RangeError) result = '#VALUE!';
      else throw e;
    } finally {
      this.busy.delete(key);
    }
    this.cache.set(key, result);
    return result;
  }

  hasTab(name: string): boolean { return this.tabs.has(name.toLowerCase()); }
  cellsOf(name: string): Record<string, CellValue> { return this.tabs.get(name.toLowerCase())?.cells ?? {}; }
}

class Formula {
  private toks: Token[];
  private at = 0;

  constructor(private sheet: Sheet, private tab: string, src: string) {
    this.toks = tokens(src);
  }

  run(): CellValue {
    const value = this.expr(1);
    if (this.at !== this.toks.length) throw new FormulaError('#ERROR!');
    if (Array.isArray(value)) throw new FormulaError('#VALUE!');
    return value;
  }

  private peek(): Token | undefined { return this.toks[this.at]; }
  private isOp(tok: Token | undefined, op: string): boolean { return !!tok && tok[0] === 'op' && tok[1] === op; }

  private take(): Token {
    const tok = this.peek();
    if (!tok) throw new FormulaError('#ERROR!');
    this.at += 1;
    return tok;
  }

  private expr(minPower: number): Value {
    let left = this.unary();
    for (;;) {
      const tok = this.peek();
      if (!tok || tok[0] !== 'op' || !(tok[1] in BINARY)) return left;
      const power = BINARY[tok[1]];
      if (power < minPower) return left;
      this.take();
      const right = this.expr(tok[1] === '^' ? power : power + 1);
      left = apply(tok[1], left, right);
    }
  }

  private unary(): Value {
    const tok = this.peek();
    if (this.isOp(tok, '-')) { this.take(); return -num(this.unary()); }
    if (this.isOp(tok, '+')) { this.take(); return num(this.unary()); }
    const value = this.atom();
    if (this.isOp(this.peek(), '%')) { this.take(); return num(value) / 100; }
    return value;
  }

  private atom(): Value {
    const [kind, txt] = this.take();
    if (kind === 'num') return Number(txt);
    if (kind === 'str') return txt.slice(1, -1).replace(/""/g, '"');
    if (kind === 'ref') return this.ref(txt);
    if (kind === 'name') {
      const upper = txt.toUpperCase();
      if (upper === 'TRUE' || upper === 'FALSE') return upper === 'TRUE';
      if (!this.isOp(this.peek(), '(')) throw new FormulaError('#NAME?');
      this.take();
      const args: Value[] = [];
      if (!this.isOp(this.peek(), ')')) {
        for (;;) {
          args.push(this.expr(1));
          if (this.isOp(this.peek(), ',')) { this.take(); continue; }
          break;
        }
      }
      if (!this.isOp(this.take(), ')')) throw new FormulaError('#ERROR!');
      if (upper === 'IF') {
        if (!args.length) throw new FormulaError('#VALUE!');
        const yes = truthy(args[0]);
        if (args.length === 1) return yes;
        return yes ? args[1] : (args.length > 2 ? args[2] : false);
      }
      const func = FUNCS[upper];
      if (!func) throw new FormulaError('#NAME?');
      return func(args);
    }
    if (kind === 'op' && txt === '(') {
      const value = this.expr(1);
      if (!this.isOp(this.take(), ')')) throw new FormulaError('#ERROR!');
      return value;
    }
    throw new FormulaError('#ERROR!');
  }

  private ref(raw: string): Value {
    let tab = this.tab;
    let txt = raw;
    const bang = raw.lastIndexOf('!');
    if (bang >= 0) {
      const name = raw.slice(0, bang);
      txt = raw.slice(bang + 1);
      tab = (name.startsWith("'") ? name.slice(1, -1).replace(/''/g, "'") : name).toLowerCase();
      if (!this.sheet.hasTab(tab)) throw new FormulaError('#REF!');
    }
    if (txt.includes(':')) {
      const [a, b] = txt.split(':', 2).map(splitRef);
      if (!a || !b) throw new FormulaError('#REF!');
      const [r1, r2] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
      const [c1, c2] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
      if ((r2 - r1 + 1) * (c2 - c1 + 1) > MAX_CELLS) throw new FormulaError('#REF!');
      const cells = this.sheet.cellsOf(tab);
      const out: Value[] = [];
      for (let r = r1; r <= r2; r += 1) {
        for (let c = c1; c <= c2; c += 1) {
          const ref = refOf(r, c);
          if (!(ref in cells)) continue;
          const value = this.sheet.value(tab, ref);
          if (isError(value)) throw new FormulaError(value);
          out.push(value);
        }
      }
      return out;
    }
    const spot = splitRef(txt);
    if (!spot) throw new FormulaError('#REF!');
    const value = this.sheet.value(tab, refOf(spot[0], spot[1]));
    if (isError(value)) throw new FormulaError(value);
    return value;
  }
}

function apply(op: string, a: Value, b: Value): Value {
  if (Array.isArray(a) || Array.isArray(b)) throw new FormulaError('#VALUE!');
  if (op === '&') return text(a) + text(b);
  if (['=', '<>', '<', '>', '<=', '>='].includes(op)) {
    let x: string | number;
    let y: string | number;
    if (typeof a === 'string' || typeof b === 'string') {
      x = text(a).toLowerCase();
      y = text(b).toLowerCase();
    } else {
      x = num(a);
      y = num(b);
    }
    switch (op) {
      case '=': return x === y;
      case '<>': return x !== y;
      case '<': return x < y;
      case '>': return x > y;
      case '<=': return x <= y;
      default: return x >= y;
    }
  }
  const x = num(a);
  const y = num(b);
  switch (op) {
    case '+': return x + y;
    case '-': return x - y;
    case '*': return x * y;
    case '/':
      if (y === 0) throw new FormulaError('#DIV/0!');
      return x / y;
    case '^': return x ** y;
    default: throw new FormulaError('#ERROR!');
  }
}

/** A computed value as the grid shows it. */
export function shownText(value: CellValue, fmt?: CellFormat): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'number') {
    if (fmt === 'percent') return `${(value * 100).toFixed(1).replace(/\.0$/, '')}%`;
    if (fmt === 'currency') return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (fmt === 'number') return value.toLocaleString('en-US', { maximumFractionDigits: 4 });
    return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(10)));
  }
  return value;
}

/** What a person typed into a cell, as the value saved. */
export function parseInput(input: string): CellValue {
  if (input === '') return null;
  if (input.startsWith('=')) return input;
  const trimmed = input.trim();
  if (/^-?(\d+\.?\d*|\.\d+)$/.test(trimmed) && trimmed.length < 16) return Number(trimmed);
  const upper = trimmed.toUpperCase();
  if (upper === 'TRUE' || upper === 'FALSE') return upper === 'TRUE';
  return input;
}

/** A saved value as the person edits it. */
export function editText(value: CellValue | undefined): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  return String(value);
}

/** How far the filled cells reach. */
export function bounds(cells: Record<string, CellValue>): { rows: number; cols: number } {
  let rows = 0;
  let cols = 0;
  for (const ref of Object.keys(cells)) {
    const spot = splitRef(ref);
    if (spot) { rows = Math.max(rows, spot[0] + 1); cols = Math.max(cols, spot[1] + 1); }
  }
  return { rows, cols };
}
