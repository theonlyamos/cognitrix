import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PageHeader } from '@/components/list-ui';
import { TranscriptView } from '@/components/TranscriptView';
import { Button } from '@/lib/components/ui/button';

describe('responsive UI contracts', () => {
  it('gives small buttons a 44px mobile target and compact desktop height', () => {
    render(<Button size="sm">Save</Button>);

    expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('h-11', 'md:h-8');
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
});
