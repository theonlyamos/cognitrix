// @vitest-environment node
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

// The key/placeholder check for fr and es lives in src/lib/i18n.test.ts. This
// one catches the other way a screen ends up English-only: text written
// straight into a component instead of behind tr(). Fix a failure with
// `pnpm i18n:extract && pnpm i18n:translate` (or `// i18n-ignore` on purpose).
describe('screen text', () => {
  it('is all behind tr(), so every language can translate it', () => {
    const root = path.resolve(__dirname, '../..');
    let output = '';
    let failed = false;
    try {
      execFileSync(process.execPath, [path.join(root, 'scripts', 'i18n-extract.mjs'), '--check'], { cwd: root, stdio: 'pipe', encoding: 'utf8' });
    } catch (e) {
      failed = true;
      const err = e as { stdout?: string; stderr?: string };
      output = `${err.stdout ?? ''}${err.stderr ?? ''}`;
    }
    expect(failed ? output : '').toBe('');
  }, 60_000);
});
