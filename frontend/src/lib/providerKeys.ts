import { api } from '@/lib/api';

export interface ProviderKey {
  id: string;
  /** env: from the engine's environment or .env (can't be edited here);
   *  saved: saved from Settings; local: needs no key; none: missing. */
  source: 'env' | 'saved' | 'local' | 'none';
  env_var: string;
  /** Last four characters of a saved key, e.g. "…3f2a". */
  hint: string | null;
  base_url: string;
}

export interface ProviderKeyList {
  providers: ProviderKey[];
  can_edit: boolean;
}

export const listProviderKeys = async () => (await api.get<ProviderKeyList>('/providers/keys')).data;
export const saveProviderKey = async (id: string, apiKey: string) =>
  (await api.put<ProviderKey>(`/providers/keys/${id}`, { api_key: apiKey })).data;
export const removeProviderKey = async (id: string) => (await api.delete<ProviderKey>(`/providers/keys/${id}`)).data;
export const testProviderKey = async (id: string) =>
  (await api.post<{ ok: boolean; message: string }>(`/providers/keys/${id}/test`)).data;
