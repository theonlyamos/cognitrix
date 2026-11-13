import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Globe } from 'lucide-react';
import { ArtifactFrame } from '@/components/artifacts/ArtifactFrame';
import { api, errorMessage } from '@/lib/api';
import { ago } from '@/lib/artifactPages';

interface PublicPage { title: string; version: number; updated: string; owner: string; agent: string; frame_url: string; downloads: boolean }

/** /s/:token: an artifact anyone with the link can read, without signing in. */
export default function PublicArtifact() {
  const { token = '' } = useParams();
  const [page, setPage] = useState<PublicPage | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    const load = () => api.get<PublicPage>(`/public/artifacts/${encodeURIComponent(token)}`)
      .then(({ data }) => { if (live) { setPage(data); setError(''); document.title = `${data.title} · Cognitrix`; } })
      .catch((e) => { if (live) { setPage(null); setError(errorMessage(e, 'This link was turned off or never existed.')); } });
    void load();
    // The owner may publish a new version or turn the link off.
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 30000);
    return () => { live = false; window.clearInterval(timer); };
  }, [token]);

  return (
    <div className="flex h-[100dvh] flex-col bg-bg text-fg">
      <header className="flex h-12 flex-none items-center gap-2 border-b border-line px-3 md:px-4">
        <Globe size={15} className="flex-none text-fg-dim" aria-hidden />
        <h1 className="min-w-0 flex-1 truncate text-[14px] font-semibold">{page?.title ?? 'Shared artifact'}</h1>
        {page && (
          <span className="hidden flex-none font-mono text-[11px] text-fg-dim sm:inline">
            {page.owner ? `shared by ${page.owner} · ` : ''}v{page.version} · {ago(page.updated)}
          </span>
        )}
        <a href="/" className="flex-none rounded-md border border-line px-2.5 py-1 font-mono text-[11px] text-fg-dim hover:text-fg">Cognitrix</a>
      </header>
      <main className="min-h-0 flex-1">
        {error ? (
          <div role="alert" className="mx-auto flex h-full max-w-sm flex-col items-center justify-center px-6 text-center">
            <h2 className="text-lg font-semibold">This page isn't available</h2>
            <p className="mt-2 text-sm text-fg-dim">{error}</p>
          </div>
        ) : page ? (
          <ArtifactFrame key={page.frame_url} url={page.frame_url} title={page.title} allowed={page.downloads ? ['download'] : []} />
        ) : (
          <div role="status" className="flex h-full items-center justify-center font-mono text-[12px] text-fg-dim">loading…</div>
        )}
      </main>
    </div>
  );
}
