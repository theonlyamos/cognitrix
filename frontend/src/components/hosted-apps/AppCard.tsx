import { AppWindow } from 'lucide-react';
import { useInRouterContext, useNavigate } from 'react-router-dom';
import { publishedAppFrom } from '@/lib/hostedApps';

type Published = NonNullable<ReturnType<typeof publishedAppFrom>>;

function Card({ published, goTo }: { published: Published; goTo: (path: string) => void }) {
  return (
    <button type="button" onClick={() => goTo(`/apps/${published.id}`)}
      className="flex w-full max-w-md items-center gap-3 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-left transition-colors hover:border-fg-dim">
      <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-accent/15 text-accent-ink"><AppWindow size={17} aria-hidden /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-semibold">{published.title}</span>
        <span className="block truncate font-mono text-[11px] text-fg-dim">App · version {published.version} is live</span>
      </span>
      <span className="flex-none rounded-md border border-line px-2 py-1 text-[12px] font-medium">Open</span>
    </button>
  );
}

function RoutedCard({ published }: { published: Published }) {
  const navigate = useNavigate();
  return <Card published={published} goTo={navigate} />;
}

/** The card under a Publish App tool call: opens the app's page. */
export function AppCard({ toolName, result }: { toolName: string; result?: string | null }) {
  const inRouter = useInRouterContext();
  const published = publishedAppFrom(toolName, result ?? undefined);
  if (!published) return null;
  return inRouter ? <RoutedCard published={published} /> : <Card published={published} goTo={(path) => window.location.assign(path)} />;
}
