import { api } from '@/lib/api';

/** A person's standing answer for agents working in one app's windows. */
export interface ComputerApp {
  /** Program name, e.g. "notepad.exe". */
  app: string;
  label: string;
  level: 'allow' | 'deny';
}

export type ComputerAppLevel = 'allow' | 'ask' | 'deny';

export interface ComputerApps {
  /** Background computer use works on the machine Cognitrix runs on. */
  available: boolean;
  apps: ComputerApp[];
}

export async function listComputerApps(): Promise<ComputerApps> {
  return (await api.get<ComputerApps>('/computer/apps')).data;
}

/** 'ask' forgets the answer. */
export async function setComputerApp(app: string, level: ComputerAppLevel): Promise<ComputerApp[]> {
  return (await api.put<{ apps: ComputerApp[] }>('/computer/apps', { app, level })).data.apps;
}
