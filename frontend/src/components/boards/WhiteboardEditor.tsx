import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import {
  ArrowUpRight, Circle, Maximize, Minus, MousePointer2, Pencil, Plus, Redo2, Square, StickyNote, Trash2, Type, Undo2,
} from 'lucide-react';
import { SaveStatus, saveLabel, useDocumentSave } from '@/components/boards/useDocumentSave';
import { cn } from '@/lib/utils';

export type ItemType = 'note' | 'rect' | 'ellipse' | 'text' | 'arrow' | 'line' | 'draw';
export interface BoardItem {
  id: string;
  type: ItemType;
  x?: number; y?: number; w?: number; h?: number;
  color?: string;
  text?: string;
  from?: string; to?: string;
  points?: [number, number][];
}
type Tool = 'select' | 'note' | 'rect' | 'ellipse' | 'text' | 'arrow' | 'draw';

/** Same colours as the read-only page (documents.py _PALETTE): fill, ink. */
export const PALETTE: Record<string, [string, string]> = {
  yellow: ['#fff3b0', '#5c4a00'], green: ['#d8f5c4', '#1f4d12'], blue: ['#d4e6ff', '#123a6b'],
  pink: ['#ffd9e6', '#6b1235'], purple: ['#e8dcff', '#3b1a6b'], orange: ['#ffe0c2', '#6b3412'],
  gray: ['#e6e6e3', '#2b2d30'], white: ['#ffffff', '#16181a'],
};
const LINE_COLORS: Record<string, string> = {
  yellow: '#b8960b', green: '#3f8f22', blue: '#2f6fd1', pink: '#d0457c', purple: '#7a4fd1', orange: '#d9772b', gray: '#6b6f76', white: '#9a9da3',
};
const fill = (color = 'yellow'): [string, string] => PALETTE[color] ?? [color, '#16181a'];
const stroke = (color = 'gray'): string => LINE_COLORS[color] ?? color;

const SIZES: Record<string, [number, number]> = { note: [180, 120], text: [220, 40], rect: [160, 100], ellipse: [160, 100] };
const DEFAULT_COLOR: Record<string, string> = { note: 'yellow', text: 'gray', rect: 'blue', ellipse: 'green', arrow: 'gray', line: 'gray', draw: 'gray' };
const HISTORY = 100;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 3;
const isShape = (item: BoardItem) => item.x !== undefined && !['arrow', 'line', 'draw'].includes(item.type);
const newId = () => Math.random().toString(16).slice(2, 12);
const round1 = (n: number) => Math.round(n * 10) / 10;

const TOOLS: { id: Tool; label: string; key: string; icon: typeof Square }[] = [
  { id: 'select', label: 'Select and move', key: 'v', icon: MousePointer2 },
  { id: 'note', label: 'Sticky note', key: 'n', icon: StickyNote },
  { id: 'rect', label: 'Box', key: 'r', icon: Square },
  { id: 'ellipse', label: 'Circle', key: 'o', icon: Circle },
  { id: 'text', label: 'Text', key: 't', icon: Type },
  { id: 'arrow', label: 'Arrow (drag from one item to another)', key: 'a', icon: ArrowUpRight },
  { id: 'draw', label: 'Pen', key: 'p', icon: Pencil },
];

function parse(source: string): BoardItem[] {
  try {
    const data = JSON.parse(source) as { items?: BoardItem[] };
    return Array.isArray(data.items) ? data.items : [];
  } catch {
    return [];
  }
}

/** Where a line from a box's centre towards (tx, ty) leaves the box. */
function edgePoint(item: BoardItem, tx: number, ty: number): [number, number] {
  const w = item.w ?? 0;
  const h = item.h ?? 0;
  const cx = (item.x ?? 0) + w / 2;
  const cy = (item.y ?? 0) + h / 2;
  const dx = tx - cx;
  const dy = ty - cy;
  if (!dx && !dy) return [cx, cy];
  if (item.type === 'ellipse') {
    const t = 1 / Math.sqrt((dx * dx) / ((w / 2) ** 2) + (dy * dy) / ((h / 2) ** 2));
    return [cx + dx * t, cy + dy * t];
  }
  const t = Math.min(dx ? (w / 2) / Math.abs(dx) : Infinity, dy ? (h / 2) / Math.abs(dy) : Infinity);
  return [cx + dx * t, cy + dy * t];
}

const center = (item: BoardItem): [number, number] => [(item.x ?? 0) + (item.w ?? 0) / 2, (item.y ?? 0) + (item.h ?? 0) / 2];

/** The two ends of an arrow or line: joined items' edges, else its points. */
export function lineEnds(item: BoardItem, byId: Map<string, BoardItem>): [number, number][] | null {
  const a = item.from ? byId.get(item.from) : undefined;
  const b = item.to ? byId.get(item.to) : undefined;
  if (a && b) {
    const [bx, by] = center(b);
    const [ax, ay] = center(a);
    return [edgePoint(a, bx, by), edgePoint(b, ax, ay)];
  }
  return item.points && item.points.length >= 2 ? item.points : null;
}

function arrowHead([x1, y1]: [number, number], [x2, y2]: [number, number], size = 11): string {
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const left = [x2 - size * Math.cos(angle - 0.45), y2 - size * Math.sin(angle - 0.45)];
  const right = [x2 - size * Math.cos(angle + 0.45), y2 - size * Math.sin(angle + 0.45)];
  return `${x2},${y2} ${left[0]},${left[1]} ${right[0]},${right[1]}`;
}

/** The top-most box under a board point. */
function shapeAt(items: BoardItem[], x: number, y: number, skip?: string): BoardItem | undefined {
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (!isShape(item) || item.id === skip) continue;
    if (x >= item.x! && x <= item.x! + item.w! && y >= item.y! && y <= item.y! + item.h!) return item;
  }
  return undefined;
}

type Drag =
  | { kind: 'pan'; sx: number; sy: number; pan: { x: number; y: number } }
  | { kind: 'move'; id: string; bx: number; by: number; before: BoardItem[]; moved: boolean }
  | { kind: 'resize'; id: string; bx: number; by: number; before: BoardItem[] }
  | { kind: 'draw'; id: string; before: BoardItem[] }
  | { kind: 'arrow'; from?: string; start: [number, number]; now: [number, number] };

interface WhiteboardEditorProps {
  pageId: string;
  version: number;
  source: string;
  onSaved?: (version: number, more: boolean) => void;
  onReload: () => void;
  /** The person changed something that isn't saved yet. */
  onChange?: () => void;
}

/** A whiteboard people edit: sticky notes, boxes, arrows between them and
 *  pen drawings, on a canvas that pans and zooms. Saves itself. */
export function WhiteboardEditor({ pageId, version, source, onSaved, onReload, onChange }: WhiteboardEditorProps) {
  const [items, setItems] = useState<BoardItem[]>(() => parse(source));
  const [tool, setTool] = useState<Tool>('select');
  const [selected, setSelected] = useState<string | null>(null);
  const [editingText, setEditingText] = useState<{ id: string; text: string } | null>(null);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [preview, setPreview] = useState<[[number, number], [number, number]] | null>(null);
  const past = useRef<BoardItem[][]>([]);
  const future = useRef<BoardItem[][]>([]);
  const drag = useRef<Drag | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const saver = useDocumentSave(pageId, version, onSaved, onChange);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const chosen = selected ? byId.get(selected) : undefined;

  const persist = useCallback((next: BoardItem[]) => saver.save(JSON.stringify({ items: next })), [saver]);

  /** A change that goes into undo history and gets saved. */
  const commit = useCallback((next: BoardItem[], before: BoardItem[] = items) => {
    past.current = [...past.current.slice(-(HISTORY - 1)), before];
    future.current = [];
    setItems(next);
    persist(next);
  }, [items, persist]);

  const undo = () => {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push(items);
    setItems(prev);
    persist(prev);
    setSelected(null);
  };
  const redo = () => {
    const next = future.current.pop();
    if (!next) return;
    past.current.push(items);
    setItems(next);
    persist(next);
  };

  const remove = (id: string) => {
    commit(items.filter((i) => i.id !== id && i.from !== id && i.to !== id));
    setSelected(null);
  };

  const update = (id: string, change: Partial<BoardItem>) => commit(items.map((i) => (i.id === id ? { ...i, ...change } : i)));

  const toBoard = (clientX: number, clientY: number): [number, number] => {
    const rect = boxRef.current?.getBoundingClientRect();
    return [round1((clientX - (rect?.left ?? 0) - pan.x) / zoom), round1((clientY - (rect?.top ?? 0) - pan.y) / zoom)];
  };

  const fit = useCallback(() => {
    const box = boxRef.current;
    if (!box) return;
    const xs: number[] = [];
    const ys: number[] = [];
    for (const item of items) {
      if (isShape(item)) { xs.push(item.x!, item.x! + item.w!); ys.push(item.y!, item.y! + item.h!); }
      for (const [px, py] of item.points ?? []) { xs.push(px); ys.push(py); }
    }
    const width = box.clientWidth || 800;
    const height = box.clientHeight || 600;
    if (!xs.length) { setPan({ x: 0, y: 0 }); setZoom(1); return; }
    const [minX, maxX, minY, maxY] = [Math.min(...xs) - 40, Math.max(...xs) + 40, Math.min(...ys) - 40, Math.max(...ys) + 40];
    const z = Math.max(MIN_ZOOM, Math.min(1.2, width / (maxX - minX), height / (maxY - minY)));
    setZoom(z);
    setPan({ x: (width - (maxX - minX) * z) / 2 - minX * z, y: (height - (maxY - minY) * z) / 2 - minY * z });
  }, [items]);

  // Show everything when the board opens.
  useLayoutEffect(() => { fit(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button === 1 || (event.button === 0 && event.altKey)) {
      drag.current = { kind: 'pan', sx: event.clientX, sy: event.clientY, pan };
      event.currentTarget.setPointerCapture?.(event.pointerId);
      return;
    }
    if (event.button !== 0) return;
    if (editingText) finishText();
    const [bx, by] = toBoard(event.clientX, event.clientY);
    const target = (event.target as Element).closest?.('[data-item-id]');
    const hitId = target?.getAttribute('data-item-id') ?? undefined;
    const handle = (event.target as Element).getAttribute?.('data-handle');
    event.currentTarget.setPointerCapture?.(event.pointerId);

    if (tool === 'select') {
      if (handle && selected) {
        drag.current = { kind: 'resize', id: selected, bx, by, before: items };
      } else if (hitId) {
        setSelected(hitId);
        drag.current = { kind: 'move', id: hitId, bx, by, before: items, moved: false };
      } else {
        setSelected(null);
        drag.current = { kind: 'pan', sx: event.clientX, sy: event.clientY, pan };
      }
      return;
    }
    if (tool === 'draw') {
      const item: BoardItem = { id: newId(), type: 'draw', points: [[bx, by]], color: chosen && !isShape(chosen) ? chosen.color : 'gray' };
      drag.current = { kind: 'draw', id: item.id, before: items };
      setItems([...items, item]);
      return;
    }
    if (tool === 'arrow') {
      const from = shapeAt(items, bx, by);
      drag.current = { kind: 'arrow', from: from?.id, start: from ? center(from) : [bx, by], now: [bx, by] };
      return;
    }
    // A note, box, circle or text where the person clicked. Stop the click
    // from focusing the board, which would take focus from the text box.
    event.preventDefault();
    const [w, h] = SIZES[tool];
    const item: BoardItem = { id: newId(), type: tool, x: round1(bx - w / 2), y: round1(by - h / 2), w, h, color: DEFAULT_COLOR[tool] };
    commit([...items, item]);
    setSelected(item.id);
    setTool('select');
    setEditingText({ id: item.id, text: '' });
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === 'pan') {
      setPan({ x: d.pan.x + event.clientX - d.sx, y: d.pan.y + event.clientY - d.sy });
      return;
    }
    const [bx, by] = toBoard(event.clientX, event.clientY);
    if (d.kind === 'move') {
      const dx = bx - d.bx;
      const dy = by - d.by;
      if (!d.moved && Math.abs(dx) + Math.abs(dy) < 2) return;
      d.moved = true;
      setItems(d.before.map((i) => {
        if (i.id !== d.id) return i;
        if (isShape(i)) return { ...i, x: round1(i.x! + dx), y: round1(i.y! + dy) };
        return i.points ? { ...i, points: i.points.map(([px, py]) => [round1(px + dx), round1(py + dy)] as [number, number]) } : i;
      }));
    } else if (d.kind === 'resize') {
      setItems(d.before.map((i) => (i.id === d.id && isShape(i)
        ? { ...i, w: Math.max(20, round1(i.w! + bx - d.bx)), h: Math.max(20, round1(i.h! + by - d.by)) } : i)));
    } else if (d.kind === 'draw') {
      setItems((now) => now.map((i) => (i.id === d.id && (i.points?.length ?? 0) < 2000 ? { ...i, points: [...(i.points ?? []), [bx, by]] } : i)));
    } else if (d.kind === 'arrow') {
      d.now = [bx, by];
      setPreview([d.start, d.now]);
    }
  };

  const onPointerUp = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const now = itemsRef.current;
    if ((d.kind === 'move' && d.moved) || d.kind === 'resize') {
      commit(now, d.before);
    } else if (d.kind === 'draw') {
      const drawn = now.find((i) => i.id === d.id);
      if (!drawn || (drawn.points?.length ?? 0) < 2) setItems(d.before);
      else commit(now, d.before);
    } else if (d.kind === 'arrow') {
      setPreview(null);
      const [bx, by] = toBoard(event.clientX, event.clientY);
      const to = shapeAt(items, bx, by, d.from);
      let arrow: BoardItem | null = null;
      if (d.from && to) arrow = { id: newId(), type: 'arrow', from: d.from, to: to.id, color: 'gray' };
      else if (Math.hypot(bx - d.start[0], by - d.start[1]) > 8) {
        const start = d.from ? edgePoint(byId.get(d.from)!, bx, by) : d.start;
        arrow = { id: newId(), type: 'arrow', points: [[round1(start[0]), round1(start[1])], [bx, by]], color: 'gray' };
      }
      if (arrow) {
        commit([...items, arrow]);
        setSelected(arrow.id);
      }
    }
  };

  const onWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey) {
      const rect = boxRef.current?.getBoundingClientRect();
      const mx = event.clientX - (rect?.left ?? 0);
      const my = event.clientY - (rect?.top ?? 0);
      const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
      setPan({ x: mx - ((mx - pan.x) / zoom) * next, y: my - ((my - pan.y) / zoom) * next });
      setZoom(next);
    } else {
      setPan({ x: pan.x - event.deltaX, y: pan.y - event.deltaY });
    }
  };

  const zoomBy = (factor: number) => {
    const box = boxRef.current;
    const mx = (box?.clientWidth ?? 800) / 2;
    const my = (box?.clientHeight ?? 600) / 2;
    const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * factor));
    setPan({ x: mx - ((mx - pan.x) / zoom) * next, y: my - ((my - pan.y) / zoom) * next });
    setZoom(next);
  };

  function finishText() {
    if (!editingText) return;
    const { id, text } = editingText;
    setEditingText(null);
    const item = byId.get(id);
    if (!item) return;
    const clean = text.slice(0, 4000);
    if ((item.text ?? '') === clean) return;
    update(id, { text: clean.trim() ? clean : undefined });
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const box = boxRef.current;
      if (!box || !box.contains(document.activeElement) || editingText) return;
      const mod = event.ctrlKey || event.metaKey;
      if ((event.key === 'Delete' || event.key === 'Backspace') && selected) { event.preventDefault(); remove(selected); }
      else if (mod && event.key.toLowerCase() === 'z') { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
      else if (mod && event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); }
      else if (event.key === 'Escape') { setSelected(null); setTool('select'); }
      else if (event.key === 'Enter' && chosen && chosen.type !== 'draw') { event.preventDefault(); setEditingText({ id: chosen.id, text: chosen.text ?? '' }); }
      else if (!mod && !event.altKey) {
        const picked = TOOLS.find((t) => t.key === event.key.toLowerCase());
        if (picked) setTool(picked.id);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const editItem = editingText ? byId.get(editingText.id) : undefined;
  const editBox = editItem && (isShape(editItem)
    ? { left: editItem.x! * zoom + pan.x, top: editItem.y! * zoom + pan.y, width: editItem.w! * zoom, height: editItem.h! * zoom }
    : (() => {
      const ends = lineEnds(editItem, byId);
      if (!ends) return null;
      const [mx, my] = [(ends[0][0] + ends[ends.length - 1][0]) / 2, (ends[0][1] + ends[ends.length - 1][1]) / 2];
      return { left: mx * zoom + pan.x - 80, top: my * zoom + pan.y - 18, width: 160, height: 36 };
    })());

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel text-fg">
      <SaveStatus state={saver.state} message={saver.message} onRetry={saver.retry} onReload={onReload} />
      <div className="flex flex-none flex-wrap items-center gap-1 border-b border-line px-2 py-1" role="toolbar" aria-label="Whiteboard tools">
        {TOOLS.map((t) => (
          <button key={t.id} type="button" aria-pressed={tool === t.id} aria-label={t.label} title={`${t.label} (${t.key.toUpperCase()})`}
            onClick={() => setTool(t.id)}
            className={cn('grid h-8 w-8 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg', tool === t.id && 'bg-fg text-bg hover:bg-fg hover:text-bg')}>
            <t.icon size={15} />
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-line" aria-hidden />
        {chosen && (
          <>
            <div className="flex items-center gap-1" role="group" aria-label="Colour">
              {Object.keys(PALETTE).map((name) => (
                <button key={name} type="button" aria-label={name} aria-pressed={chosen.color === name} title={name}
                  onClick={() => update(chosen.id, { color: name })}
                  className={cn('h-5 w-5 rounded-full border border-line', chosen.color === name && 'ring-2 ring-fg ring-offset-1 ring-offset-panel')}
                  style={{ background: isShape(chosen) ? PALETTE[name][0] : stroke(name) }} />
              ))}
            </div>
            <button type="button" aria-label="Delete" title="Delete (Del)" onClick={() => remove(chosen.id)}
              className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-danger-ink"><Trash2 size={15} /></button>
          </>
        )}
        <span className="ml-auto flex items-center gap-1">
          <button type="button" className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2 disabled:opacity-40" onClick={undo} disabled={!past.current.length} aria-label="Undo"><Undo2 size={15} /></button>
          <button type="button" className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2 disabled:opacity-40" onClick={redo} disabled={!future.current.length} aria-label="Redo"><Redo2 size={15} /></button>
          <button type="button" className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2" onClick={() => zoomBy(1 / 1.2)} aria-label="Zoom out"><Minus size={15} /></button>
          <span className="w-10 text-center font-mono text-[11px] text-fg-dim">{Math.round(zoom * 100)}%</span>
          <button type="button" className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2" onClick={() => zoomBy(1.2)} aria-label="Zoom in"><Plus size={15} /></button>
          <button type="button" className="grid h-8 w-8 place-items-center rounded-md text-fg-dim hover:bg-panel-2" onClick={fit} aria-label="Show everything" title="Show everything"><Maximize size={14} /></button>
          <span className="ml-1 font-mono text-[11px] text-fg-dim" role="status">{saveLabel(saver.state)}</span>
        </span>
      </div>

      <div ref={boxRef} tabIndex={0} onWheel={onWheel} aria-label="Whiteboard"
        className={cn('relative min-h-0 flex-1 overflow-hidden outline-none', tool === 'select' ? 'cursor-default' : 'cursor-crosshair')}
        style={{ backgroundImage: 'radial-gradient(rgb(var(--fg-dim-rgb, 120 120 120) / 0.25) 1px, transparent 1px)', backgroundSize: `${24 * zoom}px ${24 * zoom}px`, backgroundPosition: `${pan.x}px ${pan.y}px` }}>
        <svg className="absolute inset-0 h-full w-full touch-none select-none" role="img" aria-label={`${items.length} items`}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
          onDoubleClick={(event) => {
            const id = (event.target as Element).closest?.('[data-item-id]')?.getAttribute('data-item-id');
            const item = id ? byId.get(id) : undefined;
            if (item && item.type !== 'draw') { setSelected(item.id); setEditingText({ id: item.id, text: item.text ?? '' }); }
          }}>
          <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
            {items.map((item) => {
              const isSel = item.id === selected;
              if (item.type === 'draw') {
                const pts = (item.points ?? []).map((p) => p.join(',')).join(' ');
                return (
                  <g key={item.id} data-item-id={item.id}>
                    <polyline points={pts} fill="none" stroke="transparent" strokeWidth={14} />
                    <polyline points={pts} fill="none" stroke={stroke(item.color)} strokeWidth={isSel ? 4 : 2.5} strokeLinecap="round" strokeLinejoin="round" />
                  </g>
                );
              }
              if (item.type === 'arrow' || item.type === 'line') {
                const ends = lineEnds(item, byId);
                if (!ends) return null;
                const pts = ends.map((p) => p.join(',')).join(' ');
                const last = ends[ends.length - 1];
                const [mx, my] = [(ends[0][0] + last[0]) / 2, (ends[0][1] + last[1]) / 2];
                return (
                  <g key={item.id} data-item-id={item.id}>
                    <polyline points={pts} fill="none" stroke="transparent" strokeWidth={14} />
                    <polyline points={pts} fill="none" stroke={stroke(item.color)} strokeWidth={isSel ? 3.5 : 2} />
                    {item.type === 'arrow' && <polygon points={arrowHead(ends[ends.length - 2], last)} fill={stroke(item.color)} />}
                    {item.text && <text x={mx} y={my - 6} textAnchor="middle" fontSize={13} fill="currentColor">{item.text}</text>}
                  </g>
                );
              }
              const [bg, ink] = fill(item.color);
              const { x = 0, y = 0, w = 160, h = 100 } = item;
              return (
                <g key={item.id} data-item-id={item.id}>
                  {item.type === 'ellipse' ? (
                    <ellipse cx={x + w / 2} cy={y + h / 2} rx={w / 2} ry={h / 2} fill={bg} stroke={isSel ? '#2f6fd1' : ink} strokeOpacity={isSel ? 1 : 0.35} strokeWidth={isSel ? 2.5 : 1.2} />
                  ) : item.type === 'text' ? (
                    <rect x={x} y={y} width={w} height={h} fill="transparent" stroke={isSel ? '#2f6fd1' : 'none'} strokeDasharray="4 3" />
                  ) : (
                    <rect x={x} y={y} width={w} height={h} rx={item.type === 'note' ? 4 : 10} fill={bg}
                      stroke={isSel ? '#2f6fd1' : ink} strokeOpacity={isSel ? 1 : item.type === 'note' ? 0.15 : 0.35} strokeWidth={isSel ? 2.5 : 1.2}
                      style={item.type === 'note' ? { filter: 'drop-shadow(0 2px 3px rgba(0,0,0,0.15))' } : undefined} />
                  )}
                  {editingText?.id !== item.id && item.text && (
                    <foreignObject x={x} y={y} width={w} height={h} style={{ pointerEvents: 'none' }}>
                      <div className={cn('flex h-full w-full overflow-hidden whitespace-pre-wrap break-words p-2 leading-snug',
                        item.type === 'text' ? 'items-start text-[16px] font-medium text-fg' : 'items-center justify-center text-center text-[14px]',
                        item.type === 'note' && 'items-start justify-start text-left')}
                        style={item.type === 'text' ? undefined : { color: ink }}>
                        {item.text}
                      </div>
                    </foreignObject>
                  )}
                  {isSel && <rect data-handle="resize" x={x + w - 6} y={y + h - 6} width={12} height={12} rx={2} fill="#2f6fd1" className="cursor-nwse-resize" aria-label="Resize" />}
                </g>
              );
            })}
            {preview && (
              <g pointerEvents="none">
                <line x1={preview[0][0]} y1={preview[0][1]} x2={preview[1][0]} y2={preview[1][1]} stroke="#2f6fd1" strokeWidth={2} strokeDasharray="5 4" />
                <polygon points={arrowHead(preview[0], preview[1])} fill="#2f6fd1" />
              </g>
            )}
          </g>
        </svg>
        {items.length === 0 && !editingText && (
          <p className="pointer-events-none absolute inset-x-0 top-1/3 text-center text-[13px] text-fg-dim">
            Pick a sticky note, box or pen above, then click the board. Drag between two items with the arrow to join them.
          </p>
        )}
        {editingText && editBox && (
          <textarea autoFocus ref={(el) => { if (el && document.activeElement !== el) window.setTimeout(() => el.focus(), 0); }} aria-label="Item text" value={editingText.text}
            onChange={(e) => setEditingText({ id: editingText.id, text: e.target.value })}
            onBlur={finishText}
            onKeyDown={(e) => {
              if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); finishText(); boxRef.current?.focus(); }
            }}
            className="absolute z-10 resize-none rounded border-2 border-[#2f6fd1] bg-white/95 p-2 text-[14px] leading-snug text-[#16181a] outline-none"
            style={{ ...editBox, fontSize: 14 * Math.max(0.6, Math.min(zoom, 1.5)) }} />
        )}
      </div>
    </div>
  );
}
