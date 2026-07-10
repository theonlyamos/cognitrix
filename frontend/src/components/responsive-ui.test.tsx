import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PageHeader } from '@/components/list-ui';
import { CheckList } from '@/components/form';
import { TranscriptView } from '@/components/TranscriptView';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';

const homeSource = readFileSync(resolve(process.cwd(), 'src/pages/Home.tsx'), 'utf8');
const taskDetailSource = readFileSync(resolve(process.cwd(), 'src/pages/TaskDetail.tsx'), 'utf8');
const taskPageSource = readFileSync(resolve(process.cwd(), 'src/pages/TaskPage.tsx'), 'utf8');
const apiKeysSource = readFileSync(resolve(process.cwd(), 'src/pages/ApiKeys.tsx'), 'utf8');
const agentPageSource = readFileSync(resolve(process.cwd(), 'src/pages/AgentPage.tsx'), 'utf8');

function classesOnTag(source: string, anchor: string) {
  const anchorIndex = source.indexOf(anchor);
  expect(anchorIndex, `missing source anchor: ${anchor}`).toBeGreaterThan(-1);

  const boundedTagSource = source.slice(anchorIndex, anchorIndex + 1200);
  const classValue = /className=(?:"([^"]*)"|\{cn\(\s*'([^']*)')/.exec(boundedTagSource);

  expect(classValue, `missing className near: ${anchor}`).not.toBeNull();
  return (classValue?.[1] ?? classValue?.[2] ?? '').split(/\s+/);
}

describe('responsive UI contracts', () => {
  it('gives small buttons a 44px mobile target and compact desktop height', () => {
    render(<Button size="sm">Save</Button>);

    expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('h-11', 'md:h-8');
  });

  it('gives shared inputs and selects 44px mobile targets with compact desktop heights', () => {
    render(
      <>
        <Input aria-label="Task name" />
        <Select aria-label="Task team"><option>None</option></Select>
      </>,
    );

    expect(screen.getByRole('textbox', { name: 'Task name' })).toHaveClass('h-11', 'md:h-10');
    expect(screen.getByRole('combobox', { name: 'Task team' })).toHaveClass('h-11', 'md:h-10');
  });

  it('stacks page-header content before the small breakpoint', () => {
    render(
      <PageHeader title="Tasks" subtitle="2 tasks">
        <Button>New</Button>
      </PageHeader>,
    );

    expect(screen.getByRole('banner')).toHaveClass('flex-col', 'sm:flex-row');
  });

  it('stacks transcript gutters on narrow screens', () => {
    render(<TranscriptView entries={[{ kind: 'user', content: 'Hello' }]} />);

    expect(screen.getByText('YOU').parentElement).toHaveClass(
      'grid-cols-1',
      'sm:grid-cols-[96px_1fr]',
    );
  });

  it('keeps Home composer controls at least 44px on mobile and compact at md+', () => {
    expect(classesOnTag(homeSource, 'key={s}')).toEqual(expect.arrayContaining(['min-h-11', 'md:min-h-0']));
    expect(classesOnTag(homeSource, 'placeholder="Message the agent…"')).toEqual(expect.arrayContaining(['min-h-11', 'md:min-h-0']));
    expect(classesOnTag(homeSource, 'aria-label="Send"')).toEqual(expect.arrayContaining(['h-11', 'w-11', 'md:h-8', 'md:w-8']));
    expect(classesOnTag(homeSource, 'aria-label="Attach files"')).toEqual(expect.arrayContaining(['min-h-11', 'md:min-h-0']));
    expect(classesOnTag(homeSource, 'onClick={toggleBypass}')).toEqual(expect.arrayContaining(['min-h-11', 'md:min-h-0']));
  });

  it('keeps task transcript selectors 44px on mobile and compact at md+', () => {
    expect(classesOnTag(taskDetailSource, 'aria-pressed={selected === s.index}')).toEqual(
      expect.arrayContaining(['min-h-11', 'min-w-11', 'md:min-h-0', 'md:min-w-0']),
    );
    expect(classesOnTag(taskDetailSource, "aria-pressed={selected === 'synthesis'}")).toEqual(
      expect.arrayContaining(['min-h-11', 'md:min-h-0']),
    );
  });

  it('keeps checklist rows 44px on mobile and compact at md+', () => {
    render(
      <CheckList
        options={[{ value: 'read', label: 'Read' }]}
        selected={new Set()}
        onToggle={() => {}}
      />,
    );

    expect(screen.getByRole('checkbox', { name: 'Read' }).closest('label')).toHaveClass(
      'min-h-11',
      'md:min-h-0',
    );
  });

  it('keeps API key secret inputs 44px on mobile and compact at md+', () => {
    expect(classesOnTag(apiKeysSource, 'readOnly')).toEqual(
      expect.arrayContaining(['h-11', 'md:h-10']),
    );
  });

  it('keeps the AgentPage Advanced button 44px on mobile and compact at md+', () => {
    expect(classesOnTag(agentPageSource, 'onClick={() => setShowAdvanced((v) => !v)}')).toEqual(
      expect.arrayContaining(['min-h-11', 'md:min-h-0']),
    );
  });

  it('keeps TaskPage step actions 44px on mobile and compact at md+', () => {
    expect(classesOnTag(taskPageSource, 'onClick={() => setSteps((arr) => arr.filter')).toEqual(
      expect.arrayContaining(['h-11', 'w-11', 'md:h-8', 'md:w-8']),
    );
    expect(classesOnTag(taskPageSource, "onClick={() => setSteps((arr) => [...arr, { step: '', done: false }])}")).toEqual(
      expect.arrayContaining(['min-h-11', 'md:min-h-0']),
    );
  });
});
