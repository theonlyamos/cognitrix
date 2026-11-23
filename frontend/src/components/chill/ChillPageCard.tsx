import { useEffect, useState } from 'react';
import { Maximize2 } from 'lucide-react';
import { ArtifactCard } from '@/components/artifacts/ArtifactCard';
import { ArtifactFrame } from '@/components/artifacts/ArtifactFrame';
import { api } from '@/lib/api';
import { requestOpenArtifact, type PagePower } from '@/lib/artifactPages';
import { tr } from '@/lib/i18n';

interface Props { id: string; title: string; toolName: string; result: string }

/** A page the assistant made, shown small in the chat. Click to fill the window. */
export function ChillPageCard({ id, title, toolName, result }: Props) {
  const [frame, setFrame] = useState<{ frame_url: string; allowed_powers?: PagePower[] } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    setFailed(false);
    api.get<{ frame_url: string; allowed_powers?: PagePower[] }>(`/artifact-pages/${id}`)
      .then(({ data }) => { if (alive) setFrame(data); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
    // A new version in the same chat changes the result text.
  }, [id, result]);
  if (failed) return <ArtifactCard toolName={toolName} result={result} />;
  const open = () => { requestOpenArtifact(id); };
  return (
    <div role="button" tabIndex={0} onClick={open} aria-label={tr('ui.producedFiles.openName', { name: title })}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
      className="group relative w-full max-w-md cursor-pointer overflow-hidden rounded-[10px] border border-line bg-panel transition-colors hover:border-fg-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
      {/* The page at twice the size, scaled down. It can't be clicked or tabbed into here. */}
      <div aria-hidden className="pointer-events-none relative aspect-video overflow-hidden bg-panel-2" {...{ inert: '' }}>
        {frame && (
          <div className="absolute left-0 top-0 h-[200%] w-[200%] origin-top-left scale-50">
            <ArtifactFrame url={frame.frame_url} title={title} pageId={id} allowed={frame.allowed_powers ?? []} previewOnly />
          </div>
        )}
      </div>
      <span aria-hidden className="pointer-events-none absolute right-2 top-2 grid h-7 w-7 place-items-center rounded-md border border-line bg-panel/90 text-fg-dim">
        <Maximize2 size={13} />
      </span>
      <div className="truncate border-t border-line px-3 py-2.5 text-[13px] font-medium">{title}</div>
    </div>
  );
}
