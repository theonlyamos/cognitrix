import { api } from '@/lib/api';

export type Security = 'ssl' | 'starttls' | 'none';

export interface EmailAccount {
  address: string;
  /** 'plus': box+name@domain. 'domain': name@domain (all the domain's mail lands in the box). */
  addressing: 'plus' | 'domain';
  imap_host: string;
  imap_port: number;
  imap_security: Security;
  smtp_host: string;
  smtp_port: number;
  smtp_security: Security;
  username: string;
  /** The password itself never comes back. */
  password_set: boolean;
  folder: string;
  /** Agent for mail to the mailbox's own address; '' = your first agent. */
  agent_id: string;
  /** One per line; '' = only your own account email. */
  allowed_senders: string;
  require_auth: boolean;
  answer_others: boolean;
  enabled: boolean;
  status: '' | 'connecting' | 'connected' | 'error';
  last_error: string;
  last_checked_at: string;
  running: boolean;
}

export interface AgentAddress {
  agent_id: string;
  agent_name: string;
  name: string;
  enabled: boolean;
  address: string;
}

export interface AgentEmailState {
  /** False when the engine runs with COGNITRIX_EMAIL=0. */
  available: boolean;
  owner_email: string;
  account: EmailAccount | null;
  agents: AgentAddress[];
}

export type EmailUpdate = Partial<Omit<EmailAccount, 'password_set' | 'status' | 'last_error' | 'last_checked_at' | 'running'>> & {
  password?: string;
};

export async function getAgentEmail(): Promise<AgentEmailState> {
  return (await api.get<AgentEmailState>('/email')).data;
}

export async function saveAgentEmail(update: EmailUpdate): Promise<AgentEmailState> {
  return (await api.put<AgentEmailState>('/email', update)).data;
}

export async function saveAgentAddress(agentId: string, update: { name?: string; enabled?: boolean }): Promise<AgentEmailState> {
  return (await api.put<AgentEmailState>(`/email/agents/${encodeURIComponent(agentId)}`, update)).data;
}

export async function testAgentEmail(): Promise<{ to: string }> {
  return (await api.post<{ to: string }>('/email/test')).data;
}

export async function deleteAgentEmail(): Promise<void> {
  await api.delete('/email');
}

export interface MailPreset {
  id: string;
  label: string;
  imap_host: string;
  imap_port: number;
  imap_security: Security;
  smtp_host: string;
  smtp_port: number;
  smtp_security: Security;
  note: string;
}

export const PRESETS: MailPreset[] = [
  {
    id: 'gmail', label: 'Gmail', imap_host: 'imap.gmail.com', imap_port: 993, imap_security: 'ssl',
    smtp_host: 'smtp.gmail.com', smtp_port: 465, smtp_security: 'ssl',
    note: 'Turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords and paste it as the password.',
  },
  {
    id: 'fastmail', label: 'Fastmail', imap_host: 'imap.fastmail.com', imap_port: 993, imap_security: 'ssl',
    smtp_host: 'smtp.fastmail.com', smtp_port: 465, smtp_security: 'ssl',
    note: 'Create an app password under Settings › Privacy & Security › Integrations.',
  },
  {
    id: 'icloud', label: 'iCloud Mail', imap_host: 'imap.mail.me.com', imap_port: 993, imap_security: 'ssl',
    smtp_host: 'smtp.mail.me.com', smtp_port: 587, smtp_security: 'starttls',
    note: 'Create an app-specific password at account.apple.com. The user name is the part before @icloud.com.',
  },
  {
    id: 'zoho', label: 'Zoho Mail', imap_host: 'imap.zoho.com', imap_port: 993, imap_security: 'ssl',
    smtp_host: 'smtp.zoho.com', smtp_port: 465, smtp_security: 'ssl',
    note: 'Turn on IMAP access in Zoho Mail settings and use an app password.',
  },
  {
    id: 'other', label: 'Other', imap_host: '', imap_port: 993, imap_security: 'ssl',
    smtp_host: '', smtp_port: 465, smtp_security: 'ssl',
    note: 'Any mailbox with IMAP and SMTP works. Your provider lists the server names in its help pages.',
  },
];
