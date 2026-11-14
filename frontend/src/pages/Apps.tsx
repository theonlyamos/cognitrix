import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AppWindow, LayoutGrid, Pin, Plus, Rows3, Search } from 'lucide-react';
import { AccessLabel, AppStatusBadge, AppThumbnail, Sparkline } from '@/components/hosted-apps/parts';
import { EmptyState, ErrorState, LoadingState } from '@/components/list-ui';
import { api, errorMessage } from '@/lib/api';
import { ago } from '@/lib/artifactPages';
import { saveAppsView, savedAppsView, type AppList, type AppSummary, type AppsView } from '@/lib/hostedApps';
import { cn } from '@/lib/utils';
import Artifacts, { type ArtifactKind } from '@/pages/Artifacts';

type Tab = 'apps' | ArtifactKind;
type Filter = 'all' | 'online' | 'asleep' | 'pinned' | 'mine';

const TABS: { id: Tab; label: string }[] = [
  { id: 'apps', label: 'Apps' },
  { id: 'pages', label: 'Pages' },
  { id: 'images', label: 'Images' },
  { id: 'documents', label: 'Docs' },
  { id: 'shared', label: 'Shared with me' },
];
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'online', label: 'Online' },
  { id: 'asleep', label: 'Asleep' },
  { id: 'pinned', label: 'Pinned' },
  { id: 'mine', label: 'Mine' },
];

function AgentChip({ app }: { app: AppSummary }) {
  const name = app.role === 'owner' ? app.agent?.name || 'agent' : `shared by ${app.owner.name}`;
  return <span className="truncate font-mono text-[11px] text-fg-dim">{name} · {ago(app.updated)}</span>;
}

function AppCard({ app }: { app: AppSummary }) {
  return (
    <Link to={`/apps/${app.id}`} className={cn('group flex min-w-0 flex-col overflow-hidden rounded-[12px] border border-line bg-panel transition-colors hover:border-fg-dim')}>
      <div className="relative border-b border-line">
        <AppThumbnail app={app} className={cn('aspect-[16/9] w-full', app.status === 'asleep' && 'opacity-70 grayscale')} />
        <span className="absolute left-2.5 top-2.5 rounded-full border border-line bg-panel px-2 py-0.5"><AppStatusBadge app={app} /></span>
        {app.pinned && <span className="absolute right-2.5 top-2.5 inline-flex items-center gap-1 rounded-md bg-panel px-1.5 py-0.5 font-mono text-[10.5px] text-fg-dim"><Pin size={10} />Pinned</span>}
      </div>
      <div className="flex min-w-0 flex-col gap-1.5 p-3">
        <div className="flex items-center gap-2">
          <h3 className="min-w-0 flex-1 truncate text-[14px] font-semibold group-hover:text-accent-ink" title={app.title}>{app.title}</h3>
          {app.branch && <span className="flex-none rounded border border-line px-1.5 font-mono text-[10px] text-fg-dim">{app.branch}</span>}
        </div>
        <div className="flex items-center gap-2">
          <AccessLabel access={app.access} />
          <span className="ml-auto">{app.status !== 'building' && <Sparkline values={app.visits_7d} width={70} height={20} />}</span>
        </div>
        <AgentChip app={app} />
      </div>
    </Link>
  );
}

function AppTable({ apps }: { apps: AppSummary[] }) {
  const navigate = useNavigate();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-[13px]">
        <thead>
          <tr className="text-left font-mono text-[10px] uppercase tracking-[0.14em] text-fg-dim">
            {['Name', 'Status', 'Who can open', 'Visits · 7 days', 'Built by', 'Updated'].map((h) => (
              <th key={h} className="border-b border-line px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {apps.map((app) => (
            <tr key={app.id} className="cursor-pointer hover:bg-panel-2" onClick={() => navigate(`/apps/${app.id}`)}>
              <td className="border-b border-line px-3 py-2.5">
                <Link to={`/apps/${app.id}`} className="flex min-w-0 items-center gap-3" onClick={(e) => e.stopPropagation()}>
                  <AppThumbnail app={app} className="h-9 w-14 flex-none rounded-[5px] border border-line" />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{app.title}{app.pinned && <Pin size={11} className="ml-1.5 inline text-fg-dim" aria-label="Pinned" />}</span>
                    <span className="block truncate font-mono text-[11px] text-fg-dim">/apps/open/{app.slug}{app.branch ? ` · branch ${app.branch}` : ''}</span>
                  </span>
                </Link>
              </td>
              <td className="border-b border-line px-3 py-2.5">
                <AppStatusBadge app={app} />
                {app.last_error && app.status !== 'online' && <span className="mt-0.5 block max-w-[220px] truncate font-mono text-[10.5px] text-fg-dim" title={app.last_error}>{app.last_error}</span>}
              </td>
              <td className="border-b border-line px-3 py-2.5"><AccessLabel access={app.access} /></td>
              <td className="border-b border-line px-3 py-2.5">{app.status === 'building' ? <span className="text-fg-dim">–</span> : <Sparkline values={app.visits_7d} />}</td>
              <td className="border-b border-line px-3 py-2.5 text-fg-dim">{app.role === 'owner' ? app.agent?.name || '–' : app.owner.name}</td>
              <td className="border-b border-line px-3 py-2.5 font-mono text-[11.5px] text-fg-dim">{ago(app.updated)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PhoneRow({ app }: { app: AppSummary }) {
  return (
    <Link to={`/apps/${app.id}`} className="flex items-center gap-3 border-b border-line px-1 py-3">
      <AppThumbnail app={app} className="h-10 w-16 flex-none rounded-[6px] border border-line" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-medium">{app.title}</span>
        <span className="mt-0.5 flex items-center gap-2"><AppStatusBadge app={app} /><span className="font-mono text-[11px] text-fg-dim">{ago(app.updated)}</span></span>
      </span>
    </Link>
  );
}

function AppsTab() {
  const [data, setData] = useState<AppList | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [view, setView] = useState<AppsView>(savedAppsView);

  const load = async () => {
    try {
      const { data: list } = await api.get<AppList>('/hosted-apps');
      setData(list);
      setError('');
    } catch (e) {
      setError(errorMessage(e, 'Could not load your apps.'));
    }
  };
  useEffect(() => {
    void load();
    // Building and waking apps change on their own; keep the list current.
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  const apps = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.apps ?? []).filter((app) => {
      if (needle && !`${app.title} ${app.slug} ${app.description}`.toLowerCase().includes(needle)) return false;
      switch (filter) {
        case 'all': return true;
        case 'online': return app.status === 'online';
        case 'asleep': return app.status === 'asleep';
        case 'pinned': return app.pinned;
        case 'mine': return app.role === 'owner';
      }
    }).sort((a, b) => Number(b.pinned) - Number(a.pinned));
  }, [data, filter, query]);

  const pickView = (next: AppsView) => { setView(next); saveAppsView(next); };

  if (error && !data) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!data) return <LoadingState label="loading apps…" />;
  if (data.apps.length === 0) {
    return (
      <EmptyState icon={<AppWindow size={20} />} title="No apps yet"
        desc={data.runtime === null
          ? 'Apps run on the agents’ sandboxed computer. Turn it on in Settings > Computer, then ask an agent to build one.'
          : 'Describe a tool, dashboard or form, and an agent builds it and gives it a link your team can open.'}
        action={<Link to="/apps/new" className="rounded-md bg-accent px-4 py-2 text-[13px] font-semibold text-accent-foreground">Create app</Link>} />
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center">
        <div role="tablist" aria-label="Filter apps" className="-mx-4 flex flex-none gap-1.5 overflow-x-auto px-4 md:mx-0 md:px-0">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)}
              className={cn('h-9 flex-none whitespace-nowrap rounded-full border px-3 text-[12.5px] md:h-8',
                filter === f.id ? 'border-fg bg-fg text-bg' : 'border-line text-fg-dim hover:text-fg')}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 md:ml-auto">
          <label className="relative flex-1 md:w-60 md:flex-none">
            <span className="sr-only">Search apps</span>
            <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-dim" aria-hidden />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search apps" type="search"
              className="h-10 w-full rounded-md border border-line bg-panel pl-8 pr-3 text-[13px] outline-none focus:border-fg-dim md:h-8" />
          </label>
          <div role="radiogroup" aria-label="Show apps as" className="hidden flex-none overflow-hidden rounded-md border border-line md:flex">
            {([['cards', LayoutGrid, 'Cards'], ['table', Rows3, 'Table']] as const).map(([id, Icon, label]) => (
              <button key={id} type="button" role="radio" aria-checked={view === id} title={label} aria-label={label}
                onClick={() => pickView(id)}
                className={cn('grid h-8 w-9 place-items-center', view === id ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:text-fg')}>
                <Icon size={15} aria-hidden />
              </button>
            ))}
          </div>
        </div>
      </div>
      {apps.length === 0 ? (
        <p className="py-16 text-center text-[13px] text-fg-dim">Nothing matches.</p>
      ) : (
        <>
          {/* Phones always get a compact list; the toggle is for wider screens. */}
          <div className="mt-2 md:hidden">{apps.map((app) => <PhoneRow key={app.id} app={app} />)}</div>
          <div className="mt-4 hidden md:block">
            {view === 'table' ? <AppTable apps={apps} /> : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {apps.map((app) => <AppCard key={app.id} app={app} />)}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Apps: live tools agents built, plus the pages, images and documents they made (the old Artifacts page). */
export default function Apps() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get('tab'))?.id ?? 'apps') as Tab;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="app-page-header flex items-end gap-3 border-b border-line pb-3 pl-16 pr-4 pt-4 md:px-6 md:pt-5">
        <div className="min-w-0">
          <h1 className="text-[20px] font-semibold md:text-[22px]">Apps</h1>
          <p className="mt-1 truncate font-mono text-[11.5px] text-fg-dim">
            <span className="hidden md:inline">live tools your agents built, plus the pages, images and documents they made</span>
            <span className="md:hidden">tools your agents built</span>
          </p>
        </div>
        <Link to="/apps/new" className="ml-auto inline-flex h-9 flex-none items-center gap-1.5 rounded-md bg-accent px-3 text-[13px] font-semibold text-accent-foreground">
          <Plus size={15} aria-hidden /><span className="hidden sm:inline">Create app</span><span className="sm:hidden">New</span>
        </Link>
      </header>
      <nav role="tablist" aria-label="What to show" className="flex flex-none gap-1 overflow-x-auto border-b border-line px-2 md:px-5">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
            onClick={() => setParams(t.id === 'apps' ? {} : { tab: t.id }, { replace: true })}
            className={cn('-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-[13.5px]',
              tab === t.id ? 'border-accent font-medium text-fg' : 'border-transparent text-fg-dim hover:text-fg')}>
            {t.label}
          </button>
        ))}
      </nav>
      {tab === 'apps' ? <AppsTab /> : <div className="min-h-0 flex-1"><Artifacts kind={tab} /></div>}
    </div>
  );
}
