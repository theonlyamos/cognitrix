import { describe, expect, it } from 'vitest';
import { copyBackMessage, hunkHeader, numbered, sideBySide, type Hunk } from '@/lib/fileChanges';

const hunk: Hunk = {
  id: 'h1',
  old_start: 18,
  new_start: 18,
  lines: [[' ', 'def f():'], ['-', '    a'], ['-', '    b'], ['+', '    A'], ['+', '    B'], ['+', '    C'], [' ', '']],
};

describe('fileChanges', () => {
  it('numbers old and new lines', () => {
    const rows = numbered(hunk);
    expect(rows.map((r) => [r.old, r.new])).toEqual([[18, 18], [19, null], [20, null], [null, 19], [null, 20], [null, 21], [21, 22]]);
  });

  it('puts removed lines next to the lines that replaced them', () => {
    const rows = sideBySide(hunk);
    expect(rows.map((r) => [r.left?.text ?? null, r.right?.text ?? null])).toEqual([
      ['def f():', 'def f():'], ['    a', '    A'], ['    b', '    B'], [null, '    C'], ['', ''],
    ]);
  });

  it('writes the hunk header', () => {
    expect(hunkHeader(hunk)).toBe('@@ −18,4 +18,5 @@');
  });

  it('sends Copy back with the review card words', () => {
    expect(copyBackMessage('cognitrix')).toBe('[Pressed "Copy back to my computer" on card "Changes in cognitrix" (action: copy_back, value: cognitrix)]');
  });
});
