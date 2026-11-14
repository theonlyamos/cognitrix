import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ExternalLink, X } from 'lucide-react';
import { AppStatusBadge } from '@/components/hosted-apps/parts';
import { api, errorMessage } from '@/lib/api';
import { isDesktop, windowAction } from '@/lib/desktop';
import { APP_FRAME_SANDBOX, openUrl, type AppSummary } from '@/lib/hostedApps';

/** /apps/open/:slug, the app's stable link: signs you in, then shows the running app full
 *  screen under a thin bar with a way back to Cognitrix (the button, or Esc).
 *  With ?window=1 it is the app's own window (Open button), and the way out closes it. */
export default function OpenApp() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const ownWindow = useSearchParams()[0].get('window') === '1';
  const [url, setUrl] = useState('');
  const [app, setApp] = useState<AppSummary | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    openUrl(slug).then((u) => { if (live) setUrl(u); })
      .catch((e) => { if (live) setError(errorMessage(e, 'You can’t open this app.')); });
    api.get<AppSummary>(`/hosted-apps/${encodeURIComponent(slug)}`).then((r) => { if (live) setApp(r.data); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [slug]);

  const back = app ? `/apps/${app.id}` : '/apps';
  const leave = useCallback(() => {
    if (!ownWindow) {
      navigate(back);
    } else if (isDesktop()) {
      void windowAction('close');
    } else {
      window.close();
      // A tab the script didn't open can't close itself: go back to the app's page instead.
      window.setTimeout(() => { if (!window.closed) navigate(back); }, 150);
    }
  }, [back, navigate, ownWindow]);
  useEffect(() => {
    if (app) document.title = `${app.title} · Cognitrix`;
  }, [app]);
  useEffect(() => {
    // Esc leaves while focus is on Cognitrix (keys pressed inside the app stay with the app).
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') leave(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [leave]);

  if (error) {
    return (
      <div className="grid min-h-dvh place-items-center bg-bg px-4 text-fg">
        <div className="max-w-sm text-center">
          <h1 className="text-[17px] font-semibold">This app didn’t open</h1>
          <p className="mt-2 text-[13.5px] text-fg-dim">{error}</p>
          <Link to="/apps" className="mt-4 inline-flex h-9 items-center rounded-md border border-line px-3 text-[13px] font-medium hover:bg-panel-2">Go to Apps</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-dvh flex-col bg-bg text-fg">
      <header className="flex h-11 flex-none items-center gap-2 border-b border-line bg-panel px-2 md:px-3">
        {ownWindow ? (
          <button type="button" onClick={leave} title="Close this window (Esc)"
            className="inline-flex h-9 flex-none items-center gap-1.5 rounded-md px-2 text-[13px] font-medium text-fg-dim hover:bg-panel-2 hover:text-fg">
            <X size={15} aria-hidden />Close
          </button>
        ) : (
          <Link to={back} className="inline-flex h-9 flex-none items-center gap-1.5 rounded-md px-2 text-[13px] font-medium text-fg-dim hover:bg-panel-2 hover:text-fg"
            title="Back to Cognitrix (Esc)">
            <ArrowLeft size={15} aria-hidden /><span>Back<span className="hidden sm:inline"> to Apps</span></span>
          </Link>
        )}
        <span className="h-4 w-px flex-none bg-line" aria-hidden />
        <h1 className="min-w-0 truncate text-[13.5px] font-semibold">{app?.title || 'App'}</h1>
        {app && <AppStatusBadge app={app} className="hidden sm:inline-flex" />}
        <span className="ml-auto hidden font-mono text-[11px] text-fg-dim md:inline">{ownWindow ? 'Esc to close' : 'Esc to go back'}</span>
        {url && !isDesktop() && (
          <a href={url} target="_blank" rel="noopener noreferrer" aria-label="Open in a new tab" title="Open in a new tab"
            className="grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg max-md:ml-auto">
            <ExternalLink size={15} aria-hidden />
          </a>
        )}
      </header>
      {url ? (
        <iframe src={url} title={app?.title || 'App'} sandbox={APP_FRAME_SANDBOX} className="min-h-0 w-full flex-1 border-0 bg-white" />
      ) : (
        <div className="grid flex-1 place-items-center px-4 text-center text-[13.5px] text-fg-dim">
          Opening the app… it may take a few seconds to wake up.
        </div>
      )}
    </div>
  );
}
