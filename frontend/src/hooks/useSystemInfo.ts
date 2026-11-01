import { useResource } from '@/hooks/useResource';

export interface SystemInfo {
  version: string;
  desktop: boolean;
  data_folder: string;
  working_folder: string;
  database: string;
  task_queue: 'filesystem' | 'redis';
  worker_running: boolean | null;
  worker_log: string | null;
  /** Coding agent CLIs found on this computer (claude, codex, gemini). */
  coding_agents?: string[];
}

/** Engine facts for Settings and the agent editor (GET /system/info). */
export function useSystemInfo() {
  const { data, refetch } = useResource<SystemInfo>('/system/info');
  return { info: data ?? null, reload: () => void refetch({ silent: true }) };
}
