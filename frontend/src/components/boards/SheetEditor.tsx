import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react';
import { Bold, Plus, Redo2, Undo2, X } from 'lucide-react';
import {
  MAX_COLS, MAX_ROWS, Sheet, bounds, colName, editText, parseInput, refOf, shownText,
  type CellFormat, type CellStyle, type CellValue, type SheetData,
} from '@/lib/sheetFormulas';
import { SaveStatus, saveLabel, useDocumentSave } from '@/components/boards/useDocumentSave';
import { cn } from '@/lib/utils';

const DEFAULT_WIDTH = 110;
const ROW_STEP = 200;
const HISTORY = 100;
const MAX_TABS = 20;
const toolButton = 'grid h-8 w-8 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-40 aria-pressed:bg-panel-2 aria-pressed:text-fg';

interface Spot { r: number; c: number }
const clampSpot = (s: Spot): Spot => ({ r: Math.max(0, Math.min(MAX_ROWS - 1, s.r)), c: Math.max(0, Math.min(MAX_COLS - 1, s.c)) });

function parse(source: string): SheetData {
  try {
    const data = JSON.parse(source) as SheetData;
    if (Array.isArray(data.tabs) && data.tabs.length) return data;
  } catch { /* fall through */ }
  return { tabs: [{ name: 'Sheet1', cells: {} }] };
}

/** Rows of tab-separated text (what spreadsheets put on the clipboard). */
export function parseTsv(text: string): string[][] {
  const rows = text.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n');
  return rows.map((row) => row.split('\t'));
}

interface SheetEditorProps {
  pageId: string;
  version: number;
  source: string;
  onSaved?: (version: number, more: boolean) => void;
  onReload: () => void;
  /** The person changed something that isn't saved yet. */
  onChange?: () => void;
}

/** A spreadsheet people edit: cells, formulas, tabs, bold and number formats.
 *  Saves itself; the agent reads the same JSON (see documents.py). */
export function SheetEditor({ pageId, version, source, onSaved, onReload, onChange }: SheetEditorProps) {
  const [data, setData] = useState<SheetData>(() => parse(source));
  const [tabIndex, setTabIndex] = useState(0);
  const [sel, setSel] = useState<Spot>({ r: 0, c: 0 });
  const [end, setEnd] = useState<Spot>({ r: 0, c: 0 });
  const [editing, setEditingState] = useState<string | null>(null);
  /** Where the edit is typed: in the cell itself or the bar above the grid. */
  const [editWhere, setEditWhere] = useState<'cell' | 'bar'>('cell');
  const editingRef = useRef<string | null>(null);
  const setEditing = (text: string | null, where: 'cell' | 'bar' = 'cell') => {
    editingRef.current = text;
    setEditingState(text);
    if (text !== null) setEditWhere(where);
  };
  const [renaming, setRenaming] = useState<number | null>(null);
  const [extraRows, setExtraRows] = useState(0);
  const past = useRef<SheetData[]>([]);
  const future = useRef<SheetData[]>([]);
  const gridRef = useRef<HTMLDivElement>(null);
  const saver = useDocumentSave(pageId, version, onSaved, onChange);

  const tab = data.tabs[Math.min(tabIndex, data.tabs.length - 1)];
  const sheet = useMemo(() => new Sheet(data), [data]);
  const filled = useMemo(() => bounds(tab.cells), [tab]);
  const rowCount = Math.min(MAX_ROWS, Math.max(filled.rows + 20, 50, sel.r + 2) + extraRows);
  const colCount = Math.min(MAX_COLS, Math.max(filled.cols + 4, 12, sel.c + 2));
  const top = { r: Math.min(sel.r, end.r), c: Math.min(sel.c, end.c) };
  const bottom = { r: Math.max(sel.r, end.r), c: Math.max(sel.c, end.c) };
  const inRange = (r: number, c: number) => r >= top.r && r <= bottom.r && c >= top.c && c <= bottom.c;
  const selRef = refOf(sel.r, sel.c);
  const selStyle: CellStyle = tab.styles?.[selRef] ?? {};

  const commit = useCallback((next: SheetData) => {
    past.current = [...past.current.slice(-(HISTORY - 1)), data];
    future.current = [];
    setData(next);
    saver.save(JSON.stringify(next));
  }, [data, saver]);

  /** A copy of the sheet with this tab changed. */
  const withTab = (change: (t: SheetData['tabs'][number]) => SheetData['tabs'][number]): SheetData => ({
    tabs: data.tabs.map((t, i) => (i === tabIndex ? change({ ...t, cells: { ...t.cells }, styles: { ...(t.styles ?? {}) } }) : t)),
  });

  const setCells = (values: Record<string, CellValue>) => {
    commit(withTab((t) => {
      for (const [ref, value] of Object.entries(values)) {
        if (value === null) delete t.cells[ref];
        else t.cells[ref] = value;
      }
      return t;
    }));
  };

  const styleRange = (change: (style: CellStyle) => CellStyle) => {
    commit(withTab((t) => {
      const styles = t.styles!;
      for (let r = top.r; r <= bottom.r; r += 1) {
        for (let c = top.c; c <= bottom.c; c += 1) {
          const ref = refOf(r, c);
          const next = change({ ...(styles[ref] ?? {}) });
          const clean = Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined && v !== false)) as CellStyle;
          if (Object.keys(clean).length) styles[ref] = clean;
          else delete styles[ref];
        }
      }
      if (!Object.keys(styles).length) delete t.styles;
      return t;
    }));
  };

  const move = (to: Spot, extend = false) => {
    const spot = clampSpot(to);
    if (extend) setEnd(spot);
    else { setSel(spot); setEnd(spot); }
  };

  const finishEdit = (then?: Spot) => {
    const typed = editingRef.current;
    if (typed !== null) {
      setEditing(null);
      if (editText(tab.cells[selRef]) !== typed) setCells({ [selRef]: parseInput(typed) });
    }
    if (then) move(then);
    gridRef.current?.focus();
  };

  const undo = () => {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push(data);
    setData(prev);
    saver.save(JSON.stringify(prev));
  };
  const redo = () => {
    const next = future.current.pop();
    if (!next) return;
    past.current.push(data);
    setData(next);
    saver.save(JSON.stringify(next));
  };

  const clearRange = () => {
    const values: Record<string, CellValue> = {};
    for (let r = top.r; r <= bottom.r; r += 1) {
      for (let c = top.c; c <= bottom.c; c += 1) {
        const ref = refOf(r, c);
        if (ref in tab.cells) values[ref] = null;
      }
    }
    if (Object.keys(values).length) setCells(values);
  };

  const onGridKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (editing !== null || renaming !== null) return;
    const mod = event.ctrlKey || event.metaKey;
    const from = event.shiftKey ? end : sel;
    const step: Record<string, Spot> = { ArrowUp: { r: -1, c: 0 }, ArrowDown: { r: 1, c: 0 }, ArrowLeft: { r: 0, c: -1 }, ArrowRight: { r: 0, c: 1 } };
    if (step[event.key]) {
      event.preventDefault();
      move({ r: from.r + step[event.key].r, c: from.c + step[event.key].c }, event.shiftKey);
    } else if (event.key === 'Tab') {
      event.preventDefault();
      move({ r: sel.r, c: sel.c + (event.shiftKey ? -1 : 1) });
    } else if (event.key === 'Enter' || event.key === 'F2') {
      event.preventDefault();
      setEditing(editText(tab.cells[selRef]));
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      clearRange();
    } else if (mod && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) redo(); else undo();
    } else if (mod && event.key.toLowerCase() === 'y') {
      event.preventDefault();
      redo();
    } else if (mod && event.key.toLowerCase() === 'b') {
      event.preventDefault();
      styleRange((s) => ({ ...s, bold: !selStyle.bold }));
    } else if (!mod && !event.altKey && event.key.length === 1) {
      event.preventDefault();
      setEditing(event.key);
    }
  };

  const onCopy = (event: ClipboardEvent<HTMLDivElement>) => {
    if (editing !== null) return;
    const lines: string[] = [];
    for (let r = top.r; r <= bottom.r; r += 1) {
      const cells: string[] = [];
      for (let c = top.c; c <= bottom.c; c += 1) cells.push(editText(tab.cells[refOf(r, c)]));
      lines.push(cells.join('\t'));
    }
    event.clipboardData.setData('text/plain', lines.join('\n'));
    event.preventDefault();
  };

  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (editing !== null) return;
    const text = event.clipboardData.getData('text/plain');
    if (!text) return;
    event.preventDefault();
    const rows = parseTsv(text);
    const values: Record<string, CellValue> = {};
    rows.forEach((row, dr) => row.forEach((cell, dc) => {
      const r = sel.r + dr;
      const c = sel.c + dc;
      if (r < MAX_ROWS && c < MAX_COLS) values[refOf(r, c)] = parseInput(cell);
    }));
    setCells(values);
    setEnd(clampSpot({ r: sel.r + rows.length - 1, c: sel.c + Math.max(...rows.map((r) => r.length)) - 1 }));
  };

  const addTab = () => {
    if (data.tabs.length >= MAX_TABS) return;
    const names = new Set(data.tabs.map((t) => t.name.toLowerCase()));
    let n = data.tabs.length + 1;
    while (names.has(`sheet${n}`)) n += 1;
    commit({ tabs: [...data.tabs, { name: `Sheet${n}`, cells: {} }] });
    setTabIndex(data.tabs.length);
    move({ r: 0, c: 0 });
  };

  const renameTab = (index: number, name: string) => {
    setRenaming(null);
    const clean = name.split(/\s+/).join(' ').trim().slice(0, 60);
    if (!clean || clean === data.tabs[index].name) return;
    if (data.tabs.some((t, i) => i !== index && t.name.toLowerCase() === clean.toLowerCase())) return;
    commit({ tabs: data.tabs.map((t, i) => (i === index ? { ...t, name: clean } : t)) });
  };

  const deleteTab = (index: number) => {
    if (data.tabs.length < 2) return;
    if (!window.confirm(`Delete the tab “${data.tabs[index].name}”?`)) return;
    commit({ tabs: data.tabs.filter((_, i) => i !== index) });
    setTabIndex(Math.max(0, Math.min(tabIndex, data.tabs.length - 2)));
  };

  // Keep the selected cell in view.
  useEffect(() => {
    gridRef.current?.querySelector<HTMLElement>(`[data-ref="${refOf(end.r, end.c)}"]`)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [end]);

  const barValue = editing ?? editText(tab.cells[selRef]);
  const width = (c: number) => tab.widths?.[colName(c)] ?? DEFAULT_WIDTH;

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel text-fg">
      <SaveStatus state={saver.state} message={saver.message} onRetry={saver.retry} onReload={onReload} />
      <div className="flex flex-none items-center gap-1 border-b border-line px-2 py-1">
        <button type="button" className={toolButton} onClick={undo} disabled={!past.current.length} aria-label="Undo" title="Undo (Ctrl+Z)"><Undo2 size={15} /></button>
        <button type="button" className={toolButton} onClick={redo} disabled={!future.current.length} aria-label="Redo" title="Redo (Ctrl+Y)"><Redo2 size={15} /></button>
        <span className="mx-1 h-5 w-px bg-line" aria-hidden />
        <button type="button" className={toolButton} aria-pressed={!!selStyle.bold} aria-label="Bold" title="Bold (Ctrl+B)"
          onClick={() => styleRange((s) => ({ ...s, bold: !selStyle.bold }))}><Bold size={15} /></button>
        <select aria-label="Number format" value={selStyle.fmt ?? ''} className="h-8 rounded-md border border-line bg-panel px-1.5 text-[12px]"
          onChange={(e) => { const fmt = e.target.value as CellFormat | ''; styleRange((s) => ({ ...s, fmt: fmt || undefined })); }}>
          <option value="">Automatic</option>
          <option value="number">Number 1,234.5</option>
          <option value="currency">Money 1,234.50</option>
          <option value="percent">Percent 12%</option>
          <option value="text">Plain text</option>
        </select>
        <span className="ml-auto font-mono text-[11px] text-fg-dim" role="status">{saveLabel(saver.state)}</span>
      </div>
      <div className="flex flex-none items-center gap-2 border-b border-line px-2 py-1">
        <span className="w-14 flex-none text-center font-mono text-[12px] text-fg-dim" aria-label="Selected cell">
          {selRef}{(bottom.r !== top.r || bottom.c !== top.c) ? `:${refOf(bottom.r, bottom.c)}` : ''}
        </span>
        <input aria-label="Cell contents" value={barValue} placeholder="Type a value, or = for a formula like =SUM(B2:B9)"
          onFocus={() => { if (editingRef.current === null) setEditing(editText(tab.cells[selRef]), 'bar'); }}
          onChange={(e) => setEditing(e.target.value, 'bar')}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); finishEdit({ r: sel.r + 1, c: sel.c }); }
            if (e.key === 'Escape') { e.preventDefault(); setEditing(null); gridRef.current?.focus(); }
          }}
          onBlur={() => { if (editingRef.current !== null) finishEdit(); }}
          className="h-8 min-w-0 flex-1 rounded-md border border-line bg-bg px-2 font-mono text-[12.5px] outline-none focus:border-fg-dim" />
      </div>

      <div ref={gridRef} tabIndex={0} role="grid" aria-label={`Sheet ${tab.name}`} aria-rowcount={rowCount} aria-colcount={colCount}
        onKeyDown={onGridKey} onCopy={onCopy} onPaste={onPaste}
        className="relative min-h-0 flex-1 overflow-auto outline-none">
        <table className="border-separate border-spacing-0 text-[13px]" style={{ tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: 46 }} />
            {Array.from({ length: colCount }, (_, c) => <col key={c} style={{ width: width(c) }} />)}
          </colgroup>
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-20 border-b border-r border-line bg-panel-2" />
              {Array.from({ length: colCount }, (_, c) => (
                <th key={c} scope="col" className={cn('sticky top-0 z-10 border-b border-r border-line bg-panel-2 px-1 py-1 font-mono text-[11px] font-medium text-fg-dim',
                  c >= top.c && c <= bottom.c && 'bg-accent/15 text-fg')}>{colName(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: rowCount }, (_, r) => (
              <tr key={r}>
                <th scope="row" className={cn('sticky left-0 z-10 border-b border-r border-line bg-panel-2 px-1 text-right font-mono text-[11px] font-medium text-fg-dim',
                  r >= top.r && r <= bottom.r && 'bg-accent/15 text-fg')}>{r + 1}</th>
                {Array.from({ length: colCount }, (_, c) => {
                  const ref = refOf(r, c);
                  const raw = tab.cells[ref];
                  const style = tab.styles?.[ref];
                  const isSel = r === sel.r && c === sel.c;
                  if (isSel && editing !== null && editWhere === 'cell') {
                    return (
                      <td key={c} data-ref={ref} className="border-b border-r border-line p-0 outline outline-2 -outline-offset-1 outline-accent">
                        <input autoFocus aria-label={`Edit ${ref}`} value={editing} onChange={(e) => setEditing(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') { e.preventDefault(); finishEdit({ r: sel.r + (e.shiftKey ? -1 : 1), c: sel.c }); }
                            else if (e.key === 'Tab') { e.preventDefault(); finishEdit({ r: sel.r, c: sel.c + (e.shiftKey ? -1 : 1) }); }
                            else if (e.key === 'Escape') { e.preventDefault(); setEditing(null); gridRef.current?.focus(); }
                          }}
                          onBlur={() => finishEdit()}
                          className="h-7 w-full bg-bg px-1.5 font-mono text-[12.5px] outline-none" />
                      </td>
                    );
                  }
                  const value = raw === undefined ? null : typeof raw === 'string' && raw.startsWith('=') ? sheet.shown(tab.name, ref) : raw;
                  const text = style?.fmt === 'text' ? editText(raw) : shownText(value, style?.fmt);
                  const error = typeof value === 'string' && /^#[A-Z/0]+[!?]$/.test(value);
                  const align = style?.align ?? (typeof value === 'number' && style?.fmt !== 'text' ? 'right' : typeof value === 'boolean' ? 'center' : 'left');
                  return (
                    <td key={c} data-ref={ref} role="gridcell" aria-selected={inRange(r, c)}
                      title={typeof raw === 'string' && raw.startsWith('=') ? raw : undefined}
                      onMouseDown={(e) => { if (editingRef.current !== null) finishEdit(); move({ r, c }, e.shiftKey); gridRef.current?.focus(); e.preventDefault(); }}
                      onMouseEnter={(e) => { if (e.buttons === 1) move({ r, c }, true); }}
                      onDoubleClick={() => setEditing(editText(raw))}
                      className={cn('h-7 cursor-cell overflow-hidden text-ellipsis whitespace-nowrap border-b border-r border-line px-1.5',
                        inRange(r, c) && 'bg-accent/10', isSel && 'outline outline-2 -outline-offset-1 outline-accent',
                        style?.bold && 'font-semibold', error && 'text-danger-ink')}
                      style={{ textAlign: align }}>
                      {text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {rowCount < MAX_ROWS && (
          <button type="button" onClick={() => setExtraRows((n) => n + ROW_STEP)}
            className="sticky left-0 m-2 rounded-md border border-line px-2 py-1 text-[12px] text-fg-dim hover:text-fg">
            More rows
          </button>
        )}
      </div>

      <div role="tablist" aria-label="Tabs" className="flex flex-none items-center gap-1 overflow-x-auto border-t border-line bg-panel-2 px-2 py-1">
        {data.tabs.map((t, i) => renaming === i ? (
          <input key={t.name} autoFocus defaultValue={t.name} aria-label="Tab name"
            onBlur={(e) => renameTab(i, e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') renameTab(i, e.currentTarget.value); if (e.key === 'Escape') setRenaming(null); }}
            className="h-7 w-28 rounded-md border border-fg-dim bg-bg px-2 text-[12px] outline-none" />
        ) : (
          <span key={t.name} className={cn('group inline-flex h-7 flex-none items-center rounded-md text-[12px]', i === tabIndex ? 'bg-panel font-medium text-fg shadow-sm' : 'text-fg-dim hover:text-fg')}>
            <button type="button" role="tab" aria-selected={i === tabIndex} title="Double-click to rename"
              onClick={() => { setTabIndex(i); setEditing(null); move({ r: 0, c: 0 }); }} onDoubleClick={() => setRenaming(i)}
              className="h-full px-2.5">{t.name}</button>
            {i === tabIndex && data.tabs.length > 1 && (
              <button type="button" aria-label={`Delete tab ${t.name}`} onClick={() => deleteTab(i)} className="mr-1 grid h-5 w-5 place-items-center rounded hover:bg-panel-2"><X size={11} /></button>
            )}
          </span>
        ))}
        <button type="button" onClick={addTab} disabled={data.tabs.length >= MAX_TABS} aria-label="Add a tab" title="Add a tab"
          className="grid h-7 w-7 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel hover:text-fg disabled:opacity-40"><Plus size={14} /></button>
      </div>
    </div>
  );
}
