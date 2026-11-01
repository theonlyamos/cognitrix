import { api } from '@/lib/api';

export type CredentialKind = 'token' | 'basic' | 'oauth';

/** A saved sign-in. Values never come back from the server. */
export interface Credential {
  id: string;
  name: string;
  kind: CredentialKind;
  description: string;
  allowed_hosts: string[];
  /** Header the token goes in; '' means Authorization: Bearer. */
  header: string;
  /** Last four characters, e.g. "…a1b2". */
  hint: string;
  username: string;
  last_used_at: string | null;
  /** What an agent writes to use it, e.g. {{secret:GITHUB_TOKEN}}. */
  placeholder: string;
}

export interface CredentialInput {
  name: string;
  kind: 'token' | 'basic';
  description?: string;
  allowed_hosts: string[];
  header?: string;
  value?: string;
  username?: string;
  password?: string;
}

export type CredentialUpdate = Partial<Omit<CredentialInput, 'name' | 'kind'>>;

export async function listCredentials(): Promise<Credential[]> {
  return (await api.get<{ credentials: Credential[] }>('/credentials')).data.credentials;
}

export async function addCredential(input: CredentialInput): Promise<Credential> {
  return (await api.post<Credential>('/credentials', input)).data;
}

export async function updateCredential(id: string, input: CredentialUpdate): Promise<Credential> {
  return (await api.patch<Credential>(`/credentials/${encodeURIComponent(id)}`, input)).data;
}

export async function deleteCredential(id: string): Promise<void> {
  await api.delete(`/credentials/${encodeURIComponent(id)}`);
}
