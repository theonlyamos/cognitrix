import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CheckList } from '@/components/form';
import { ErrorState, PageHeader } from '@/components/list-ui';
import { TranscriptView } from '@/components/TranscriptView';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';

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

  it('keeps the shared error retry action touch-sized and operable', async () => {
    const onRetry = vi.fn();
    render(<ErrorState message="Network unavailable" onRetry={onRetry} />);

    const retry = screen.getByRole('button', { name: /retry/i });
    await userEvent.click(retry);

    expect(onRetry).toHaveBeenCalledOnce();
    expect(retry).toHaveClass('min-h-11', 'md:min-h-0');
  });
});
