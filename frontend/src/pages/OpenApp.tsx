import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ExternalLink, X } from 'lucide-react';
import { AppBridgeDialogs, useAppBridge } from '@/components/hosted-apps/AppBridge';
import { AppStatusBadge } from '@/components/hosted-apps/parts';
import { api, errorMessage } from '@/lib/api';
import { desktop, isDesktop, windowAction } from '@/lib/desktop';
import { installedChanged, useInstalledApp } from '@/lib/installedApps';
import { APP_FRAME_SANDBOX, openUrl, type AppSummary } from '@/lib/hostedApps';
import { tr } from '@/lib/i18n';

/** /apps/open/:slug, the app's stable link: signs you in, then shows the running app full
 *  screen under a thin bar with a way back to Cognitrix (the button, or Esc).
 *  With ?window=1 it is the app's own window (Open button), and the way out closes it. */
export default function OpenApp() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const ownWindow = params.get('window') === '1';
  const trayItem = params.get('tray') || undefined;
  const [url, setUrl] = useState('');
  const [app, setApp] = useState<AppSummary | null>(null);
  const [error, setError] = useState('');
  const frame = useRef<HTMLIFrameElement>(null);
  const { installed } = useInstalledApp(slug);
  const { ask, confirm } = useAppBridge({ frame, app, ownWindow, installed, trayItem });
  const [removed, setRemoved] = useState(false);
  const uninstall = async () => {
    try {
      await desktop.uninstallApp(slug);
      installedChanged();
      setRemoved(true);
    } catch {
      /* the shortcut stays; Settings › Desktop app can remove it too */
    }
  };

  useEffect(() => {
    let live = true;
    openUrl(slug).then((u) => { if (live) setUrl(u); })
      .catch((e) => { if (live) setError(errorMessage(e, tr('ui.openApp.youCanTOpenThis'))); });
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
          <h1 className="text-[17px] font-semibold">{tr('ui.openApp.thisAppDidnTOpen')}</h1>
          <p className="mt-2 text-[13.5px] text-fg-dim">{error}</p>
          {installed && !removed && (
            <button type="button" onClick={() => void uninstall()}
              className="mt-4 mr-2 inline-flex h-9 items-center rounded-md border border-line px-3 text-[13px] font-medium hover:bg-panel-2">{tr('ui.openApp.removeItFromThisComputer')}</button>
          )}
          {removed && <p role="status" className="mt-3 text-[13px] text-fg-dim">{tr('ui.openApp.removedItsShortcutIsGone')}</p>}
          <Link to="/apps" className="mt-4 inline-flex h-9 items-center rounded-md border border-line px-3 text-[13px] font-medium hover:bg-panel-2">{tr('ui.openApp.goToApps')}</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-dvh flex-col bg-bg text-fg">
      <header className="flex h-11 flex-none items-center gap-2 border-b border-line bg-panel px-2 md:px-3">
        {ownWindow ? (
          <button type="button" onClick={leave} title={tr('ui.openApp.closeThisWindowEsc')}
            className="inline-flex h-9 flex-none items-center gap-1.5 rounded-md px-2 text-[13px] font-medium text-fg-dim hover:bg-panel-2 hover:text-fg">
            <X size={15} aria-hidden />{tr('ui.openApp.close')}
          </button>
        ) : (
          <Link to={back} className="inline-flex h-9 flex-none items-center gap-1.5 rounded-md px-2 text-[13px] font-medium text-fg-dim hover:bg-panel-2 hover:text-fg"
            title={tr('ui.openApp.backToCognitrixEsc')}>
            <ArrowLeft size={15} aria-hidden /><span>{tr('ui.openApp.back')}<span className="hidden sm:inline">{' '}{tr('ui.openApp.toApps')}</span></span>
          </Link>
        )}
        <span className="h-4 w-px flex-none bg-line" aria-hidden />
        <h1 className="min-w-0 truncate text-[13.5px] font-semibold">{app?.title || tr('ui.openApp.app')}</h1>
        {app && <AppStatusBadge app={app} className="hidden sm:inline-flex" />}
        <span className="ml-auto hidden font-mono text-[11px] text-fg-dim md:inline">{ownWindow ? tr('ui.openApp.escToClose') : tr('ui.openApp.escToGoBack')}</span>
        {url && !isDesktop() && (
          <a href={url} target="_blank" rel="noopener noreferrer" aria-label={tr('ui.openApp.openInANewTab')} title={tr('ui.openApp.openInANewTab')}
            className="grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg max-md:ml-auto">
            <ExternalLink size={15} aria-hidden />
          </a>
        )}
      </header>
      {url ? (
        <iframe ref={frame} src={url} title={app?.title || tr('ui.openApp.app')} sandbox={APP_FRAME_SANDBOX} className="min-h-0 w-full flex-1 border-0 bg-white" />
      ) : (
        <div className="grid flex-1 place-items-center px-4 text-center text-[13.5px] text-fg-dim">
          {tr('ui.openApp.openingTheAppItMay')}
        </div>
      )}
      <AppBridgeDialogs title={app?.title || slug} ask={ask} confirm={confirm} />
    </div>
  );
}
