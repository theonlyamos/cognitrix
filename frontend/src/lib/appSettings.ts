import { api } from '@/lib/api';

/** App-wide settings an admin changes in Settings (cognitrix/app_settings.py).
 *  Each one keeps the name of the environment variable it replaced. */
export type AppSettingKind = 'text' | 'secret' | 'number' | 'toggle' | 'select' | 'textarea' | 'list';

export interface AppSetting {
  name: string;
  label: string;
  help: string;
  kind: AppSettingKind;
  /** Saved value; '' means the built-in default. Always '' for secrets. */
  value: string;
  default: string;
  placeholder: string;
  restart: boolean;
  multiline: boolean;
  options: { value: string; label: string }[];
  /** Secrets only. */
  is_set?: boolean;
  hint?: string;
}

export interface AppSettingGroup {
  title: string;
  settings: AppSetting[];
}

export interface AppSettingsPage {
  groups: AppSettingGroup[];
  can_edit: boolean;
}

export type AppSettingsPageId = 'models' | 'people' | 'apps' | 'computers' | 'notifications' | 'advanced';

export const getAppSettings = async (page: AppSettingsPageId) =>
  (await api.get<AppSettingsPage>('/settings/app', { params: { page } })).data;

export const saveAppSettings = async (page: AppSettingsPageId, values: Record<string, string>) =>
  (await api.put<AppSettingsPage>('/settings/app', { values }, { params: { page } })).data;
