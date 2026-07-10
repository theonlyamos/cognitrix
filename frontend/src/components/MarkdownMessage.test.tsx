import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import MarkdownMessage from '@/components/MarkdownMessage';

const css = readFileSync(resolve(process.cwd(), 'src/app.css'), 'utf8');

describe('MarkdownMessage code copy action', () => {
  function renderCodeBlock() {
    const codePreRule = /\.md-code pre\s*\{[\s\S]*?\}/.exec(css)?.[0] ?? '';
    const copyRule = /\.md-copy\s*\{[\s\S]*?\}/.exec(css)?.[0] ?? '';
    return render(
      <>
        <style>{`${codePreRule}\n${copyRule}`}</style>
        <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
          <MarkdownMessage content={'```ts\nconst answer = 42;\n```'} />
        </MemoryRouter>
      </>,
    );
  }

  it('renders a named copy button for fenced code', () => {
    renderCodeBlock();

    expect(screen.getByRole('button', { name: 'copy' })).toHaveClass('md-copy');
  });

  it('keeps the copy action visible and 44px on touch while scoping hover reveal to fine pointers', () => {
    const baseRule = /\.md-copy\s*\{([\s\S]*?)\}/.exec(css)?.[1] ?? '';
    const finePointerStart = css.indexOf(
      '@media (min-width: 768px) and (hover: hover) and (pointer: fine)',
    );
    const finePointerRule = finePointerStart >= 0 ? css.slice(finePointerStart) : '';

    expect(baseRule).toContain('min-width: 44px');
    expect(baseRule).toContain('min-height: 44px');
    expect(baseRule).toContain('opacity: 1');
    expect(finePointerStart).toBeGreaterThan(-1);
    expect(finePointerRule).toMatch(/\.md-copy\s*\{[\s\S]*?opacity:\s*0/);
    expect(finePointerRule).toContain('.md-code:hover .md-copy');
  });

  it('reserves touch-mode code space above text and restores compact fine-pointer padding', () => {
    const { container } = renderCodeBlock();
    const pre = container.querySelector('pre');
    const finePointerStart = css.indexOf(
      '@media (min-width: 768px) and (hover: hover) and (pointer: fine)',
    );
    const finePointerRule = finePointerStart >= 0 ? css.slice(finePointerStart) : '';

    expect(pre).not.toBeNull();
    expect(getComputedStyle(pre!).paddingTop).toBe('3.25rem');
    expect(finePointerRule).toContain('.md-code pre { padding-top: .8em; }');
  });
});
