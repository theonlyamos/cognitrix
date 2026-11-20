import { useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { LayoutPanelTop, Shapes, Table2 } from 'lucide-react';
import { useResource } from '@/hooks/useResource';
import type { ArtifactList } from '@/lib/artifactPages';
import { tr } from '@/lib/i18n';

/** The artifacts a task made (its results page, boards its runs keep fresh),
 * as a strip of links at the top of the task. Nothing when it made none. */
export function TaskArtifacts({ taskId, refreshKey }: { taskId: string; refreshKey?: unknown }) {
  const { data, refetch } = useResource<ArtifactList>(`/artifact-pages?task_id=${encodeURIComponent(taskId)}`);
  const first = useMemo(() => ({ key: refreshKey }), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (refreshKey !== first.key) void refetch({ silent: true });
  }, [refreshKey, first, refetch]);
  const pages = useMemo(
    () => [...(data?.pages ?? [])].sort((a, b) => (b.updated || '').localeCompare(a.updated || '')),
    [data],
  );

  if (!pages.length) return null;
  return (
    <nav aria-label={tr('ui.artifacts.taskArtifacts.artifactsFromThisTask')} className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-6 py-2 max-md:px-4">
      <span className="font-mono text-[10px] tracking-[0.18em] text-fg-dim">{tr('ui.artifacts.taskArtifacts.artifacts')}</span>
      {pages.slice(0, 6).map((page) => {
        const Icon = page.format === 'sheet' ? Table2 : page.format === 'whiteboard' ? Shapes : LayoutPanelTop;
        return (
          <Link key={page.id} to={`/artifacts/${page.id}`}
            className="inline-flex h-8 max-w-[18rem] items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 text-[12.5px] hover:bg-panel-2">
            <Icon size={14} className="flex-none text-fg-dim" aria-hidden />
            <span className="min-w-0 truncate font-medium">{page.title}</span>
            <span className="flex-none font-mono text-[11px] text-fg-dim">v{page.version}</span>
            {page.open_comments > 0 && (
              <span className="flex-none rounded-full bg-accent px-1.5 font-mono text-[10px] font-semibold text-accent-foreground">{page.open_comments}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
