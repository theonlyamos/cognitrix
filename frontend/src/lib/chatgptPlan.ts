import { api } from '@/lib/api';
import { desktop, isDesktop } from '@/lib/desktop';

/** Where people check and limit what Cognitrix uses of their ChatGPT plan. */
export const CHATGPT_USAGE_URL = 'https://chatgpt.com/settings/usage';

export interface ChatGPTPlanStatus {
  signed_in: boolean;
  email: string | null;
  name: string | null;
  /** 'expired' when the sign-in ran out and needs doing again. */
  needs_sign_in: string | null;
  /** The account used last time, when nobody is signed in now. */
  previous_email: string | null;
  usage_url: string;
  /** The signed-in person is you (only their chats can use the plan). */
  yours: boolean;
  can_edit: boolean;
}

export interface PlanSignInStart {
  flow_id: string;
  authorize_url: string;
  redirect_uri: string;
}

export interface PlanFlowStatus {
  status: 'pending' | 'done' | 'error';
  error: string | null;
}

export const getChatGPTPlan = async () => (await api.get<ChatGPTPlanStatus>('/providers/chatgpt/plan')).data;
export const signOutChatGPTPlan = async () =>
  (await api.delete<{ ok: boolean; warning: string | null }>('/providers/chatgpt/plan')).data;
export const testChatGPTPlan = async () =>
  (await api.post<{ ok: boolean; message: string }>('/providers/chatgpt/plan/test')).data;
export const planFlowStatus = async (flowId: string) =>
  (await api.get<PlanFlowStatus>(`/providers/chatgpt/plan/flows/${encodeURIComponent(flowId)}`)).data;
export const pastePlanAddress = async (flowId: string, url: string) =>
  (await api.post<PlanFlowStatus>(`/providers/chatgpt/plan/flows/${encodeURIComponent(flowId)}/address`, { url })).data;

/**
 * Start "Continue with ChatGPT" and open OpenAI's sign-in page.
 *
 * Call it straight from a click: on the web the tab is opened before the
 * request, or the browser blocks it. In the desktop app it opens in the
 * system browser.
 */
export async function startChatGPTSignIn(newAccount = false): Promise<PlanSignInStart> {
  const inDesktop = isDesktop();
  const tab = inDesktop ? null : window.open('', '_blank');
  let flow: PlanSignInStart;
  try {
    flow = (await api.post<PlanSignInStart>('/providers/chatgpt/plan/sign-in', { new_account: newAccount })).data;
  } catch (err) {
    tab?.close();
    throw err;
  }
  if (inDesktop) await desktop.openExternal(flow.authorize_url);
  else if (tab) tab.location.href = flow.authorize_url;
  else window.open(flow.authorize_url, '_blank', 'noopener');
  return flow;
}

/** Poll a sign-in until it finishes, fails, or `signal` aborts it. */
export async function waitForChatGPTSignIn(
  flowId: string,
  { signal, pollMs = 1500, timeoutMs = 10 * 60 * 1000 }: { signal?: AbortSignal; pollMs?: number; timeoutMs?: number } = {},
): Promise<PlanFlowStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, pollMs));
    if (signal?.aborted) return { status: 'error', error: 'Sign-in was cancelled.' };
    const status = await planFlowStatus(flowId);
    if (status.status !== 'pending') return status;
  }
  return { status: 'error', error: 'Sign-in took too long. Please try again.' };
}

const WELCOME_KEY = 'cognitrix.chatgptPlanWelcomed';

/** True the first time this browser sees a ChatGPT plan sign-in finish. */
export function firstPlanSignIn(): boolean {
  try {
    if (localStorage.getItem(WELCOME_KEY)) return false;
    localStorage.setItem(WELCOME_KEY, '1');
  } catch {
    return false;
  }
  return true;
}
