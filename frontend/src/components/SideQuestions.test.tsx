import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SideQuestionsPanel } from './SideQuestions';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const apiDelete = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost, delete: apiDelete },
  API_BASE: '/api/v1',
  errorMessage: (e: unknown, fallback: string) =>
    (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback,
}));

const row = (id: string, question: string, answer: string) => ({
  id, question, answer, error: '', model: 'anthropic/m', asked_at: '2026-11-15T10:00:00Z', during_run: true,
});

describe('SideQuestionsPanel', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiDelete.mockReset();
  });

  it('shows earlier questions and asks a new one on Enter', async () => {
    apiGet.mockResolvedValue({ data: { questions: [row('1', 'What is it doing?', 'Running the tests.')] } });
    apiPost.mockResolvedValue({ data: row('2', 'Which file?', 'auth.py') });
    render(<SideQuestionsPanel sessionId="s1" busy agentName="Coder" onClose={() => {}} onUseInChat={() => {}} />);
    expect(await screen.findByText('Running the tests.')).toBeTruthy();
    expect(screen.getByText(/Coder keeps working/)).toBeTruthy();
    expect(apiGet).toHaveBeenCalledWith('/sessions/s1/side-questions');

    const box = screen.getByRole('textbox', { name: 'Side question' });
    fireEvent.change(box, { target: { value: 'Which file?' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(await screen.findByText('auth.py')).toBeTruthy();
    expect(apiPost).toHaveBeenCalledWith('/sessions/s1/side-questions', { question: 'Which file?' }, expect.anything());
    expect((box as HTMLTextAreaElement).value).toBe('');
  });

  it('keeps the question when asking fails, and can hand it to the chat', async () => {
    apiGet.mockResolvedValue({ data: { questions: [row('1', 'Why?', 'Because.')] } });
    apiPost.mockRejectedValue({ response: { data: { detail: 'Too many side questions.' } } });
    const onUse = vi.fn();
    render(<SideQuestionsPanel sessionId="s1" busy={false} agentName="" onClose={() => {}} onUseInChat={onUse} />);
    await screen.findByText('Because.');
    const box = screen.getByRole('textbox', { name: 'Side question' });
    fireEvent.change(box, { target: { value: 'again?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    expect(await screen.findByText('Too many side questions.')).toBeTruthy();
    expect((box as HTMLTextAreaElement).value).toBe('again?');
    fireEvent.click(screen.getByRole('button', { name: 'ask in chat' }));
    expect(onUse).toHaveBeenCalledWith('Why?');
  });

  it('clears and closes', async () => {
    apiGet.mockResolvedValue({ data: { questions: [row('1', 'Q', 'A')] } });
    apiDelete.mockResolvedValue({ data: { removed: 1 } });
    const onClose = vi.fn();
    render(<SideQuestionsPanel sessionId="s1" busy={false} agentName="" onClose={onClose} onUseInChat={() => {}} />);
    await screen.findByText('A');
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    await waitFor(() => expect(screen.queryByText('A')).toBeNull());
    expect(apiDelete).toHaveBeenCalledWith('/sessions/s1/side-questions');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Side question' }), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
