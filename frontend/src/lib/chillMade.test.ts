import { describe, expect, it } from 'vitest';
import { madeInTurn } from '@/lib/chillMade';
import type { ProducedFileRef } from '@/lib/producedFiles';

const file = (id: string, extra: Partial<ProducedFileRef> = {}) => ({ id, name: `${id}.md`, shown: true, ...extra }) as ProducedFileRef;
const published = (version: number) => `Published "Report" (artifact_id ab12, version ${version}).`;

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
