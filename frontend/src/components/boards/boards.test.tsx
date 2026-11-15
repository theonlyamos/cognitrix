import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sheet, parseInput, shownText, type SheetData } from '@/lib/sheetFormulas';
import { SheetEditor, parseTsv } from './SheetEditor';
import { WhiteboardEditor, lineEnds, type BoardItem } from './WhiteboardEditor';
import { DataDocument } from './DataDocument';
import { NewDocumentButtons } from '@/pages/Artifacts';
import { SAVE_DELAY_MS } from './useDocumentSave';

const apiGet = vi.hoisted(() => vi.fn());
const apiPut = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const navigate = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: apiGet, put: apiPut, post: apiPost },
}));
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}));

// jsdom has no PointerEvent: without it pointer events lose their button and position.
if (!('PointerEvent' in window)) {
  class FakePointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  }
  Object.assign(window, { PointerEvent: FakePointerEvent });
}

function rowsSheet(rows: (string | number)[][], extra: SheetData['tabs'] = []): SheetData {
  const cells: Record<string, string | number> = {};
  rows.forEach((row, r) => row.forEach((v, c) => { cells[`${String.fromCharCode(65 + c)}${r + 1}`] = v; }));
  return { tabs: [{ name: 'Budget', cells }, ...extra] };
}

describe('sheet formulas (same results as the server)', () => {
  it('computes the cases the server test checks', () => {
    const data = rowsSheet([
      ['Rent', 1200], ['Food', 300.5], ['Total', '=SUM(B1:B2)'], ['Avg', '=AVERAGE(B1:B2)'],
      ['Calc', '=(B3-1)*2+10%'], ['Join', '="Total: "&B3'], ['If', '=IF(B3>1000,"big","small")'],
      ['Pow', '=2^3^2'], ['Other', "='Rates 2026'!A1*2"], ['Div', '=1/0'], ['Name', '=NOPE(1)'],
      ['Cycle', '=B12'], ['Chain', '=B10+1'], ['Round', '=ROUND(2/3,2)'], ['Bad', '=1+'],
      ['Bool', '=AND(TRUE, B1>1)'], ['Count', '=COUNT(B1:B4)'], ['Text', '=UPPER(A1)&LEN(A1)'],
    ], [{ name: 'Rates 2026', cells: { A1: 21 } }]);
    const sheet = new Sheet(data);
    const v = (ref: string) => sheet.shown('Budget', ref);
    expect([v('B3'), v('B4'), v('B5')]).toEqual([1500.5, 750.25, 2999.1]);
    expect([v('B6'), v('B7'), v('B8'), v('B9')]).toEqual(['Total: 1500.5', 'big', 512, 42]);
    expect([v('B10'), v('B11'), v('B12'), v('B13')]).toEqual(['#DIV/0!', '#NAME?', '#CYCLE!', '#DIV/0!']);
    expect([v('B14'), v('B15'), v('B16'), v('B17'), v('B18')]).toEqual([0.67, '#ERROR!', true, 4, 'RENT4']);
  });

  it('reads what people type and shows formats', () => {
    expect(parseInput('12.5')).toBe(12.5);
    expect(parseInput('=A1')).toBe('=A1');
    expect(parseInput('true')).toBe(true);
    expect(parseInput('007 Agent')).toBe('007 Agent');
    expect(parseInput('')).toBeNull();
    expect(shownText(0.125, 'percent')).toBe('12.5%');
    expect(shownText(1234.5, 'currency')).toBe('1,234.50');
    expect(parseTsv('a\tb\r\n1\t2\n')).toEqual([['a', 'b'], ['1', '2']]);
  });
});

describe('SheetEditor', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    apiPut.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  const source = JSON.stringify(rowsSheet([['Rent', 1200], ['Food', 300]]));

  it('edits a cell, computes the formula and saves from the version it opened', async () => {
    apiPut.mockResolvedValue({ data: { version: 4 } });
    const onSaved = vi.fn();
    render(<SheetEditor pageId="p1" version={3} source={source} onSaved={onSaved} onReload={() => {}} />);
    fireEvent.mouseDown(document.querySelector('[data-ref="B3"]')!);
    fireEvent.keyDown(screen.getByRole('grid'), { key: '=' });
    const input = screen.getByLabelText('Edit B3');
    fireEvent.change(input, { target: { value: '=SUM(B1:B2)' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(document.querySelector('[data-ref="B3"]')!.textContent).toBe('1500');

    await act(async () => { vi.advanceTimersByTime(SAVE_DELAY_MS + 10); });
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    const [url, body] = apiPut.mock.calls[0];
    expect(url).toBe('/artifact-pages/p1/content');
    expect(body.base_version).toBe(3);
    expect(JSON.parse(body.content).tabs[0].cells.B3).toBe('=SUM(B1:B2)');
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(4, false));

    // The next save starts from the version the server gave back.
    fireEvent.mouseDown(document.querySelector('[data-ref="A3"]')!);
    fireEvent.keyDown(screen.getByRole('grid'), { key: 'Delete' });
    fireEvent.mouseDown(document.querySelector('[data-ref="A1"]')!);
    fireEvent.keyDown(screen.getByRole('grid'), { key: 'Delete' });
    await act(async () => { vi.advanceTimersByTime(SAVE_DELAY_MS + 10); });
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(2));
    expect(apiPut.mock.calls[1][1].base_version).toBe(4);
  });

  it('pastes rows from another spreadsheet, makes text bold and undoes', async () => {
    apiPut.mockResolvedValue({ data: { version: 2 } });
    render(<SheetEditor pageId="p1" version={1} source={source} onReload={() => {}} />);
    fireEvent.mouseDown(document.querySelector('[data-ref="A4"]')!);
    fireEvent.paste(screen.getByRole('grid'), { clipboardData: { getData: () => 'Gas\t80\nWater\t40' } });
    expect(document.querySelector('[data-ref="B5"]')!.textContent).toBe('40');
    fireEvent.click(screen.getByRole('button', { name: 'Bold' }));
    expect(document.querySelector('[data-ref="A4"]')!.className).toContain('font-semibold');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(document.querySelector('[data-ref="B5"]')!.textContent).toBe('');
  });

  it('says so and offers Reload when someone else saved first', async () => {
    apiPut.mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { detail: 'This changed since you opened it (now version 5).' } } });
    const onReload = vi.fn();
    render(<SheetEditor pageId="p1" version={3} source={source} onReload={onReload} />);
    fireEvent.mouseDown(document.querySelector('[data-ref="C1"]')!);
    fireEvent.keyDown(screen.getByRole('grid'), { key: 'x' });
    fireEvent.keyDown(screen.getByLabelText('Edit C1'), { key: 'Enter' });
    await act(async () => { vi.advanceTimersByTime(SAVE_DELAY_MS + 10); });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('changed since you opened it');
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(onReload).toHaveBeenCalled();
  });

  it('adds and renames tabs', () => {
    render(<SheetEditor pageId="p1" version={1} source={source} onReload={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add a tab' }));
    expect(screen.getByRole('tab', { name: 'Sheet2' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.doubleClick(screen.getByRole('tab', { name: 'Sheet2' }));
    const name = screen.getByLabelText('Tab name');
    fireEvent.change(name, { target: { value: 'Rates' } });
    fireEvent.keyDown(name, { key: 'Enter' });
    expect(screen.getByRole('tab', { name: 'Rates' })).toBeInTheDocument();
  });
});

describe('WhiteboardEditor', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    apiPut.mockReset();
    apiPut.mockResolvedValue({ data: { version: 2 } });
  });
  afterEach(() => vi.useRealTimers());

  const board = JSON.stringify({ items: [
    { id: 'n1', type: 'note', text: 'Goals', x: 40, y: 40, w: 180, h: 120, color: 'yellow' },
    { id: 'n2', type: 'rect', text: 'Plan', x: 400, y: 40, w: 160, h: 100, color: 'blue' },
    { id: 'a1', type: 'arrow', from: 'n1', to: 'n2', color: 'gray' },
  ] });

  it('joins arrows at the edges of the items they connect', () => {
    const items = (JSON.parse(board) as { items: BoardItem[] }).items;
    const ends = lineEnds(items[2], new Map(items.map((i) => [i.id, i])))!;
    // From the right edge of the note to the left edge of the box.
    expect(ends[0][0]).toBe(220);
    expect(ends[1][0]).toBe(400);
  });

  it('shows the items, adds a sticky note where clicked, types in it and saves', async () => {
    render(<WhiteboardEditor pageId="p9" version={1} source={board} onReload={() => {}} />);
    expect(screen.getByText('Goals')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sticky note' }));
    const svg = document.querySelector('svg[aria-label]')!;
    fireEvent.pointerDown(svg, { button: 0, clientX: 300, clientY: 400, pointerId: 1 });
    const text = screen.getByLabelText('Item text');
    fireEvent.change(text, { target: { value: 'New idea' } });
    fireEvent.blur(text);
    expect(screen.getByText('New idea')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select and move' })).toHaveAttribute('aria-pressed', 'true');

    await act(async () => { vi.advanceTimersByTime(SAVE_DELAY_MS + 10); });
    await waitFor(() => expect(apiPut).toHaveBeenCalled());
    const saved = JSON.parse(apiPut.mock.calls[apiPut.mock.calls.length - 1][1].content) as { items: BoardItem[] };
    expect(saved.items).toHaveLength(4);
    expect(saved.items[3]).toMatchObject({ type: 'note', text: 'New idea', color: 'yellow' });
  });

  it('recolours and deletes the chosen item, taking its arrows with it', async () => {
    render(<WhiteboardEditor pageId="p9" version={1} source={board} onReload={() => {}} />);
    const note = document.querySelector('[data-item-id="n1"] rect')!;
    fireEvent.pointerDown(note, { button: 0, clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.pointerUp(note, { clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'pink' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(document.querySelector('[data-item-id="n1"]')).toBeNull();
    expect(document.querySelector('[data-item-id="a1"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(document.querySelector('[data-item-id="a1"]')).not.toBeNull();
  });
});

describe('DataDocument', () => {
  beforeEach(() => apiGet.mockReset());

  it('loads the newest copy again when the agent publishes', async () => {
    apiGet.mockResolvedValueOnce({ data: { version: 1, source: JSON.stringify(rowsSheet([['Old']])) } });
    const { rerender } = render(<DataDocument pageId="p1" format="sheet" latest={1} />);
    await waitFor(() => expect(document.querySelector('[data-ref="A1"]')?.textContent).toBe('Old'));
    apiGet.mockResolvedValueOnce({ data: { version: 2, source: JSON.stringify(rowsSheet([['New']])) } });
    rerender(<DataDocument pageId="p1" format="sheet" latest={2} />);
    await waitFor(() => expect(document.querySelector('[data-ref="A1"]')?.textContent).toBe('New'));
  });
});

describe('New sheet / New whiteboard', () => {
  it('starts a blank one and opens it', async () => {
    apiPost.mockResolvedValueOnce({ data: { id: 'w1' } });
    render(<MemoryRouter><NewDocumentButtons /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /New whiteboard/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/artifacts/w1'));
    expect(apiPost).toHaveBeenCalledWith('/artifact-pages', { format: 'whiteboard' });
  });
});
