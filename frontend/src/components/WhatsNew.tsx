import { useCallback, useEffect, useId, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import MarkdownMessage from '@/components/MarkdownMessage';
import { AGENTS_CHANGED_EVENT } from '@/lib/providers';
import type { StarterAgent } from '@/components/AssistantSetup';

/** Fire this to open the What's new window from anywhere (the account menu). */
export const OPEN_WHATS_NEW_EVENT = 'cognitrix:open-whats-new';

export interface Release {
  version: string;
  notes: string;
  url?: string;
}

export interface WhatsNewStatus {
  version: string;
  show?: boolean;
  releases: Release[];
  starter_team?: StarterAgent[];
  releases_url?: string;
}

/**
 * Opens a "What's new" window once after the app is updated (desktop
 * self-update, pip upgrade or a new Docker image), with the release notes of
 * every version since the one this person last saw. Also offers to add any
 * starter agents they're missing. The account menu's "What's new" reopens it.
 */
export default function WhatsNew() {
  const [status, setStatus] = useState<WhatsNewStatus | null>(null);
  // Opened by the app after an update (marks the version seen on close), or
  // from the menu (just a look).
  const [afterUpdate, setAfterUpdate] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<WhatsNewStatus>('/whats-new')
      .then((r) => {
        if (cancelled || !r.data?.show || !r.data.releases?.length) return;
        setAfterUpdate(true);
        setStatus(r.data);
      })
      // An older engine, or an API key session: nothing to show.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const open = () => {
      api
        .get<WhatsNewStatus>('/whats-new/notes')
        .then((r) => {
          if (!r.data?.releases?.length) return;
          setAfterUpdate(false);
          setStatus(r.data);
        })
        .catch(() => {});
    };
    window.addEventListener(OPEN_WHATS_NEW_EVENT, open);
    return () => window.removeEventListener(OPEN_WHATS_NEW_EVENT, open);
  }, []);

  const close = useCallback(() => {
    if (afterUpdate) void api.post('/whats-new/seen').catch(() => {});
    setStatus(null);
    setAfterUpdate(false);
  }, [afterUpdate]);

  if (!status) return null;
  return <WhatsNewDialog status={status} afterUpdate={afterUpdate} onClose={close} />;
}

export function WhatsNewDialog({
  status,
  afterUpdate,
  onClose,
}: {
  status: WhatsNewStatus;
  afterUpdate: boolean;
  onClose: () => void;
}) {
  const titleId = useId();
  const [starters, setStarters] = useState<StarterAgent[]>(status.starter_team ?? []);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const addStarters = async () => {
    setError('');
    setAdding(true);
    try {
      const res = await api.post<{ created?: { id: string; name: string }[] }>('/onboarding/starter-team');
      setAdded(res.data?.created?.length ?? starters.length);
      setStarters([]);
      window.dispatchEvent(new CustomEvent(AGENTS_CHANGED_EVENT));
    } catch (e) {
      setError(errorMessage(e, 'Could not add the starter team.'));
    } finally {
      setAdding(false);
    }
  };

  const releases = status.releases;
  const oldest = releases[releases.length - 1]?.version;
  const subtitle = !afterUpdate
    ? `You're on version ${status.version}.`
    : releases.length > 1
      ? `Cognitrix is now on version ${status.version}. Here's everything new since ${oldest}.`
      : `Cognitrix is now on version ${status.version}. Here's what changed.`;

  return (
    <div
      className="fixed inset-0 z-[74] flex items-start justify-center overflow-y-auto bg-[rgba(5,6,7,.6)] p-4 sm:items-center"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="my-8 flex max-h-[min(720px,calc(100dvh-4rem))] w-[600px] max-w-full flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-6 pb-4 pt-6">
          <div className="min-w-0 space-y-1.5">
            <div className="font-mono text-[10.5px] tracking-[0.14em] text-accent-ink">WHAT'S NEW</div>
            <h2 id={titleId} className="text-[22px] font-semibold leading-tight">
              {afterUpdate ? 'Cognitrix was updated' : "What's new in Cognitrix"}
            </h2>
            <p className="text-[13.5px] leading-relaxed text-fg-dim">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-2 -mt-1 grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg"
          >
            <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {(starters.length > 0 || added > 0) && (
            <div className="mb-5 flex flex-col gap-2.5 rounded border border-line bg-panel-2 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between">
              {added > 0 ? (
                <p role="status" className="text-[13px] leading-relaxed text-fg-dim">
                  <span className="font-medium text-fg">Starter team added.</span> {added === 1 ? 'One specialist is' : `${added} specialists are`} on
                  the Agents page, and your assistant can hand work to them.
                </p>
              ) : (
                <>
                  <p className="min-w-0 text-[13px] leading-relaxed text-fg-dim">
                    <span className="block font-medium text-fg">Add the starter team</span>
                    {starters.map((a) => a.name).join(', ')}: specialists your assistant can hand bigger jobs to. They run on
                    your assistant's provider, and you can change or delete them later.
                  </p>
                  <Button type="button" size="sm" className="shrink-0" onClick={() => void addStarters()} disabled={adding}>
                    {adding ? 'Adding…' : starters.length === 1 ? `Add ${starters[0].name}` : `Add ${starters.length} agents`}
                  </Button>
                </>
              )}
            </div>
          )}
          {error && (
            <p role="alert" className="mb-4 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}

          <div className="space-y-6">
            {releases.map((r) => (
              <section key={r.version} aria-label={`Version ${r.version}`}>
                {releases.length > 1 && (
                  <div className="mb-2 flex items-center gap-3">
                    <span className="font-mono text-[11px] tracking-[0.1em] text-fg-dim">VERSION {r.version}</span>
                    <span className="h-px flex-1 bg-line" />
                  </div>
                )}
                <div className="md text-[13.5px] [&_ol]:list-decimal [&_ul]:list-disc">
                  <MarkdownMessage content={r.notes} />
                </div>
              </section>
            ))}
          </div>
        </div>

        <div className="flex flex-col-reverse gap-2 border-t border-line bg-panel-2/60 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          {status.releases_url ? (
            <a
              href={status.releases_url}
              target="_blank"
              rel="noreferrer"
              className="text-center text-[12.5px] text-fg-dim underline-offset-2 hover:text-fg hover:underline sm:text-left"
            >
              All release notes
            </a>
          ) : (
            <span />
          )}
          <Button type="button" onClick={onClose} autoFocus>
            Got it
          </Button>
        </div>
      </div>
    </div>
  );
}
