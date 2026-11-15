import { api } from '@/lib/api';

/** A quick question asked beside a chat. It is never part of the chat. */
export interface SideQuestion {
  id: string;
  question: string;
  answer: string;
  error: string;
  model: string;
  asked_at: string;
  /** Asked while the agent was working. */
  during_run: boolean;
}

export async function listSideQuestions(sessionId: string): Promise<SideQuestion[]> {
  const res = await api.get<{ questions: SideQuestion[] }>(`/sessions/${sessionId}/side-questions`);
  return res.data.questions;
}

export async function askSideQuestion(sessionId: string, question: string): Promise<SideQuestion> {
  const res = await api.post<SideQuestion>(`/sessions/${sessionId}/side-questions`, { question }, { timeout: 150_000 });
  return res.data;
}

export async function clearSideQuestions(sessionId: string): Promise<void> {
  await api.delete(`/sessions/${sessionId}/side-questions`);
}
