import { LayoutPanelTop } from 'lucide-react';
import { useInRouterContext, useNavigate } from 'react-router-dom';
import { publishedFrom, requestOpenArtifact } from '@/lib/artifactPages';

type Published = NonNullable<ReturnType<typeof publishedFrom>>;

function Card({ published, goTo }: { published: Published; goTo: (path: string) => void }) {
  const open = () => {
    // A chat on screen opens it in its side panel; anywhere else, go to its page.
    if (!requestOpenArtifact(published.id)) goTo(`/artifacts/${published.id}`);
  };
  return (
    <button type="button" onClick={open}
      className="flex w-full max-w-md items-center gap-3 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-left transition-colors hover:border-fg-dim">
      <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-accent/15 text-accent-ink"><LayoutPanelTop size={17} aria-hidden /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-semibold">{published.title}</span>
        <span className="block truncate font-mono text-[11px] text-fg-dim">Artifact · version {published.version}</span>
      </span>
      <span className="flex-none rounded-md border border-line px-2 py-1 text-[12px] font-medium">Open</span>
    </button>
  );
}

function RoutedCard({ published }: { published: Published }) {
  const navigate = useNavigate();
  return <Card published={published} goTo={navigate} />;
}

/** The card under a Publish Artifact tool call: opens the page beside the chat. */
export function ArtifactCard({ toolName, result }: { toolName: string; result?: string | null }) {
  const inRouter = useInRouterContext();
  const published = publishedFrom(toolName, result ?? undefined);
  if (!published) return null;
  return inRouter ? <RoutedCard published={published} /> : <Card published={published} goTo={(path) => window.location.assign(path)} />;
}
