import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { sentTasks, type SentTask } from '@/lib/devices';
import { systemNotify } from '@/lib/desktop';
import { Group, StatusPill } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

const DONE = new Set(['completed', 'failed', 'cancelled']);

function tone(status: string) {
  if (status === 'completed') return 'ok' as const;
  if (status === 'failed') return 'warn' as const;
  return DONE.has(status) ? 'off' as const : 'accent' as const;
}

/** Desktop app: tasks this PC handed to the web account, and what came back. */
export function SentTasks({ account }: { account: string }) {
  const [tasks, setTasks] = useState<SentTask[] | null>(null);
  const [error, setError] = useState('');
  const seen = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      sentTasks()
        .then((list) => {
          if (!alive) return;
          // Tell the person when a task they sent finishes.
          for (const t of list) {
            const before = seen.current.get(t.id);
            if (before && !DONE.has(before) && DONE.has(t.status)) {
              void systemNotify({ title: t.title, body: t.status === 'completed' ? tr('ui.sentTasks.finishedOnAccount', { account }) : tr('ui.sentTasks.statusOnAccount', { status: t.status, account }), tag: `sent-${t.id}` });
            }
            seen.current.set(t.id, t.status);
          }
          setTasks(list);
          setError('');
          timer = setTimeout(load, list.some((t) => !DONE.has(t.status)) ? 10000 : 60000);
        })
        .catch((err) => { if (alive) { setError(errorMessage(err, tr('ui.sentTasks.couldNotLoadSentTasks'))); timer = setTimeout(load, 60000); } });
    };
    load();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [account]);

  if (error && !tasks) return <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>;
  if (!tasks) return <p role="status" className="text-[12.5px] text-fg-dim">{tr('ui.sentTasks.loading')}</p>;
  if (tasks.length === 0) return <p className="text-[12.5px] text-fg-dim">{tr('ui.sentTasks.noneYet')}</p>;
  return (
    <Group>
      {tasks.map((t) => (
        <div key={t.id} className="px-3.5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">{t.title}</span>
            <StatusPill tone={tone(t.status)}>{t.status.replace('_', ' ').toUpperCase()}</StatusPill>
            <a href={t.url} target="_blank" rel="noreferrer" className="text-[12px] text-accent-ink hover:underline">{tr('ui.sentTasks.openOnTheWeb')}</a>
          </div>
          {t.result && <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-[12.5px] leading-snug text-fg-dim">{t.result}</p>}
        </div>
      ))}
    </Group>
  );
}
