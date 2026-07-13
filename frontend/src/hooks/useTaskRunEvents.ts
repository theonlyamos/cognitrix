import { useEventStream } from '@/hooks/useEventStream';
import type { TaskRunEvent } from '@/lib/task-run-events';

interface Options {
  taskId?: string;
  runId: string | null;
  onEvent: (event: TaskRunEvent) => void;
}

export function useTaskRunEvents({ taskId, runId, onEvent }: Options) {
  const path = taskId && runId
    ? '/tasks/' + encodeURIComponent(taskId)
      + '/runs/' + encodeURIComponent(runId) + '/events'
    : null;
  return useEventStream<TaskRunEvent>({
    path,
    enabled: path !== null,
    onEvent: ({ data }) => onEvent(data),
  });
}
