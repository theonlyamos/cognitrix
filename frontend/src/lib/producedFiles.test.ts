import { describe, expect, it, vi } from 'vitest';
import { fileMeta, fmtDuration, fmtSize, onOpenFile, onOpenFileElsewhere, parseFileRefs, requestOpenFile } from '@/lib/producedFiles';

describe('produced files', () => {
  it('checks refs from the server', () => {
    expect(parseFileRefs('nope')).toBeUndefined();
    expect(parseFileRefs([{ id: 1 }])).toBeUndefined();
    const [ref] = parseFileRefs([{ id: 'f1', name: 'a.weird', kind: 'nonsense', source: 'pc', shared: true, size: 'x' }])!;
    expect(ref).toMatchObject({ id: 'f1', kind: 'other', source: 'pc', shared: true, shown: false, size: 0, mime_type: 'application/octet-stream' });
  });

  it('writes a short line under a file name', () => {
    expect(fmtSize(0)).toBe('');
    expect(fmtSize(2048)).toBe('2 KB');
    expect(fmtSize(18 * 1024 ** 2)).toBe('18 MB');
    expect(fmtDuration(75)).toBe('1:15');
    expect(fmtDuration(3725)).toBe('1:02:05');
    expect(fileMeta({ kind: 'video', size: 18 * 1024 ** 2, info: { height: 1080, duration: 75 } })).toBe('1080p · 1:15 · 18 MB');
    expect(fileMeta({ kind: 'image', size: 2048, info: { width: 640, height: 480 } })).toBe('640×480 · 2 KB');
  });

  it('opens a file in the page panel first, the app viewer otherwise', () => {
    const page = vi.fn();
    const app = vi.fn();
    const stopApp = onOpenFileElsewhere(app);
    expect(requestOpenFile('a')).toBe(true);
    expect(app).toHaveBeenCalledWith('a');
    const stopPage = onOpenFile(page);
    requestOpenFile('b');
    expect(page).toHaveBeenCalledWith('b');
    expect(app).not.toHaveBeenCalledWith('b');
    stopPage();
    stopApp();
    expect(requestOpenFile('c')).toBe(false);
  });
});
