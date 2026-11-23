import { describe, expect, it } from 'vitest';
import { madeInTurn, usedComputer } from '@/lib/chillMade';
import type { ProducedFileRef } from '@/lib/producedFiles';

const file = (id: string, extra: Partial<ProducedFileRef> = {}) => ({ id, name: `${id}.md`, shown: true, ...extra }) as ProducedFileRef;
const published = (version: number) => `Published "Report" (artifact_id ab12, version ${version}).`;

describe('usedComputer', () => {
  it('sees window tools, called directly or from Run Code, and nothing else', () => {
    expect(usedComputer([{ tools: [{ name: 'Open App' }] }])).toBe(true);
    expect(usedComputer([{ tools: [{ name: 'Run Code', params: '{"code":"await tools.look_at_window({window_id:1})"}' }] }])).toBe(true);
    expect(usedComputer([{ tools: [{ name: 'Run Code', params: '{"code":"await tools.bash({})"}' }, { name: 'Bash' }] }])).toBe(false);
  });
});

describe('madeInTurn', () => {
  it('keeps shared files, drops hidden ones and repeats', () => {
    const made = madeInTurn([
      { tools: [{ name: 'Share File', status: 'done', files: [file('a', { shared: true })] }] },
      { filesMade: [file('a', { shared: true }), file('b'), file('c', { shown: false })] },
    ]);
    expect(made.files.map((f) => f.id)).toEqual(['a', 'b']);
  });

  it('shows the newest version of a page once, and skips unfinished tools', () => {
    const made = madeInTurn([{ tools: [
      { name: 'Publish Artifact', status: 'done', result: published(1) },
      { name: 'Publish Artifact', status: 'done', result: published(2) },
      { name: 'Share File', status: 'running', files: [file('x')] },
    ] }]);
    expect(made.pages).toHaveLength(1);
    expect(made.pages[0].result).toContain('version 2');
    expect(made.files).toEqual([]);
  });
});
