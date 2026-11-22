import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ChillScreen, type ChillScreenProps } from '@/components/chill/ChillScreen';
import { plainText } from '@/lib/plainText';

const base: ChillScreenProps = {
  empty: false,
  name: 'Ama Test',
  assistantName: 'Jarvis',
  composer: <textarea aria-label="box" />,
  stage: null,
  question: 'What is 17 times 23?',
  answer: '17 times 23 is **391**.',
  streaming: false,
  thinking: null,
  helper: null,
  listening: null,
  cards: null,
  rail: [{ key: 'answer', kind: 'answer', label: 'Answer' }],
  railActive: 'answer',
  onRail: () => {},
  suggestions: ['Plan my week'],
  onSuggestion: () => {},
  onNewChat: () => {},
  chats: [],
  activeChatId: null,
  onOpenChat: () => {},
  onFullMode: () => {},
};

describe('ChillScreen', () => {
  it('greets with the box in the middle before anything is said', () => {
    const onSuggestion = vi.fn();
    render(<ChillScreen {...base} empty question={null} answer={null} onSuggestion={onSuggestion} />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/Ama$/);
    fireEvent.click(screen.getByRole('button', { name: 'Plan my week' }));
    expect(onSuggestion).toHaveBeenCalledWith('Plan my week');
    expect(screen.queryByRole('region', { name: "What's on screen" })).toBeNull();
  });

  it('moves the box down as soon as live voice starts', () => {
    render(<ChillScreen {...base} empty question={null} answer={null} listening={{ phase: 'hearing', heard: '' }} />);
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
    expect(screen.getByText('Hearing you…')).toBeTruthy();
  });

  it('shows the answer on the stage, and a made file with the reply as a caption', async () => {
    const onRail = vi.fn();
    const rail = [
      { key: 'answer', kind: 'answer' as const, label: 'Answer' },
      { key: 'file:1', kind: 'file' as const, label: 'spots.csv', fileKind: 'sheet' as const },
    ];
    const { rerender } = render(<ChillScreen {...base} rail={rail} onRail={onRail} />);
    expect(await screen.findByText('391')).toBeTruthy();
    rerender(<ChillScreen {...base} rail={rail} railActive="file:1" onRail={onRail} stage={<div>the csv viewer</div>} />);
    expect(screen.getByText('the csv viewer')).toBeTruthy();
    expect(screen.getByText('17 times 23 is 391.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Full answer' }));
    expect(onRail).toHaveBeenCalledWith('answer');
    fireEvent.click(screen.getAllByRole('button', { name: 'spots.csv' })[0]);
    expect(onRail).toHaveBeenCalledWith('file:1');
  });

  it('names the helper agent the assistant is working with', () => {
    render(<ChillScreen {...base} helper={{ name: 'Researcher', doing: 'working on a part' }} />);
    expect(screen.getByText('Researcher')).toBeTruthy();
    expect(screen.getByText('working on a part')).toBeTruthy();
  });

  it('switches to the full app', () => {
    const onFullMode = vi.fn();
    render(<ChillScreen {...base} onFullMode={onFullMode} />);
    fireEvent.click(screen.getByRole('button', { name: 'Full' }));
    expect(onFullMode).toHaveBeenCalled();
  });
});

describe('plainText', () => {
  it('drops markdown for the one-line caption', () => {
    expect(plainText('> The **Researcher** said:\n\n- [Tram 28](https://x.y) is `old`')).toBe('The Researcher said: Tram 28 is old');
    expect(plainText('| a | b |\n|---|---|\n| 1 | 2 |')).toBe('a b 1 2');
  });
});
