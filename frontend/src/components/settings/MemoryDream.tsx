import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Toggle } from '@/pages/Notifications';
import { timeAgo } from '@/lib/agentDraft';
import { cn } from '@/lib/utils';
import {
  answerDreamProposal, dreamNow, dreamStatus, getMemoryProfile, memoryHistory, notionBackupNow, notionBackupStatus,
  restoreFromNotion, saveMemoryProfile, saveMemorySettings, setNotionBackup, undoDream, undoMemoryChange,
  type DreamProposal, type DreamRun, type DreamStatus, type MemoryHistoryEntry, type MemoryProfile, type NotionBackupState,
} from '@/lib/memories';
import { Group, SettingRow, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

const PROFILE_MAX = 1200;

/** "About you": the short summary every agent reads first. Dream mode keeps it current unless you lock it. */
export function AboutYouCard({ disabled }: { disabled?: boolean }) {
  const [profile, setProfile] = useState<MemoryProfile | null>(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getMemoryProfile().then(setProfile).catch(() => {}); }, []);

  const save = async (value: string, locked?: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setProfile(await saveMemoryProfile(value, locked));
      setEditing(false);
    } catch (e) {
      setError(errorMessage(e, tr('ui.settings.memoryDream.couldNotSaveTheSummary')));
    } finally {
      setBusy(false);
    }
  };

  if (!profile) return null;
  return (
    <section aria-label={tr('ui.settings.memoryDream.aboutYou')} className={cn('!mt-6', disabled && 'opacity-60')}>
      <div className="flex flex-wrap items-center gap-2">
        <SubHeading className="!mb-0 !mt-0 flex-1">{tr('ui.settings.memoryDream.aboutYou_d8dd')}</SubHeading>
        {!editing && profile.text && (
          <button type="button" disabled={busy} className={quietButtonClass}
            title={profile.locked ? tr('ui.settings.memoryDream.letDreamModeKeepThis') : tr('ui.settings.memoryDream.keepThisSummaryAsIt')}
            onClick={() => void save(profile.text, !profile.locked)}>
            {profile.locked ? tr('ui.settings.memoryDream.unlock') : tr('ui.settings.memoryDream.lock')}
          </button>
        )}
        {!editing && (
          <button type="button" className={quietButtonClass} onClick={() => { setText(profile.text); setEditing(true); }}>
            {profile.text ? tr('ui.settings.memoryDream.edit') : tr('ui.settings.memoryDream.writeOne')}
          </button>
        )}
      </div>
      <p className="mb-2 mt-1 text-[12px] text-fg-dim">
        {tr('ui.settings.memoryDream.aFewLinesEveryAgent')}{profile.locked ? tr('ui.settings.memoryDream.butYouLockedItSo') : tr('ui.settings.memoryDream.andKeepsItCurrent')}
      </p>
      {editing ? (
        <form className="space-y-2 rounded-[10px] border border-line bg-bg p-3" aria-label={tr('ui.settings.memoryDream.editTheSummaryAboutYou')}
          onSubmit={(e) => { e.preventDefault(); void save(text.trim(), true); }}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} autoFocus aria-label={tr('ui.settings.memoryDream.summaryAboutYou')}
            placeholder={tr('ui.settings.memoryDream.eGAmosBuildsCognitrix')}
            className="w-full resize-y rounded-lg border border-line bg-panel p-2.5 text-[13px] text-fg placeholder:text-fg-dim focus:border-accent focus:outline-none" />
          <div className="flex items-center gap-2">
            <span className={cn('flex-1 text-[11.5px]', text.length > PROFILE_MAX ? 'text-danger-ink' : 'text-fg-dim')}>{text.length}/{PROFILE_MAX}</span>
            <button type="button" onClick={() => setEditing(false)} className={quietButtonClass}>{tr('ui.settings.memoryDream.cancel')}</button>
            <button type="submit" disabled={busy || text.length > PROFILE_MAX} className={buttonClass}>{tr('ui.settings.memoryDream.save')}</button>
          </div>
        </form>
      ) : profile.text ? (
        <div className="rounded-[12px] border border-line bg-panel px-3.5 py-3">
          <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed">{profile.text}</p>
          <p className="mt-1.5 text-[11.5px] text-fg-dim">
            {profile.locked ? tr('ui.settings.memoryDream.locked') : profile.by_dream ? tr('ui.settings.memoryDream.writtenByDreamMode') : tr('ui.settings.memoryDream.writtenByYou')}
            {profile.updated ? ` · ${timeAgo(profile.updated)}` : ''}
          </p>
        </div>
      ) : (
        <p className="rounded-[10px] border border-line bg-panel px-3.5 py-3 text-[13px] text-fg-dim">
          {tr('ui.settings.memoryDream.noSummaryYetDreamMode')}
        </p>
      )}
      {error && <p role="alert" className="mt-1 text-[12.5px] text-danger-ink">{error}</p>}
    </section>
  );
}

function statusText(run: DreamRun): string {
  if (run.status === 'running') return 'Tidying…';
  if (run.status === 'failed') return tr('ui.settings.memoryDream.couldNotFinish');
  if (run.status === 'skipped') return 'Skipped';
  return 'Done';
}

function actionLabel(p: DreamProposal): string {
  switch (p.action) {
    case 'merge': return tr('ui.settings.memoryDream.mergeIntoOne');
    case 'update': return 'Change';
    case 'delete': return 'Forget';
    case 'add': return 'Remember';
    default: return tr('ui.settings.memoryDream.theseDisagree');
  }
}

/** Dream mode: nightly tidying, the Tidy now button, changes waiting for your OK, and undo. */
export function DreamSection({ enabled, memoryOn, onChanged }: { enabled: boolean; memoryOn: boolean; onChanged: () => void }) {
  const [status, setStatus] = useState<DreamStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showRuns, setShowRuns] = useState(false);
  const wasRunning = useRef(false);

  const load = useCallback(() => dreamStatus().then((s) => {
    setStatus(s);
    if (wasRunning.current && !s.running) {
      const last = (s.runs ?? [])[0];
      if (last) setNotice(last.summary || statusText(last));
      onChanged();
    }
    wasRunning.current = s.running;
  }).catch(() => {}), [onChanged]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!status?.running) return undefined;
    const timer = window.setInterval(() => { void load(); }, 3000);
    return () => window.clearInterval(timer);
  }, [status?.running, load]);

  const act = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  const tidyNow = () => act(async () => {
    setNotice(null);
    await dreamNow();
    wasRunning.current = true;
  }, tr('ui.settings.memoryDream.couldNotStartTidying'));

  if (!status) return null;
  const running = status.running;
  const runs = status.runs ?? [];
  const proposals = status.proposals ?? [];
  const last = runs[0];
  const hour = `${String(status.hour).padStart(2, '0')}:00`;

  return (
    <section aria-label={tr('ui.settings.memoryDream.dreamMode')} className="!mt-6">
      <SubHeading className="!mt-0">{tr('ui.settings.memoryDream.dreamMode_8e40')}</SubHeading>
      <Group>
        <SettingRow
          label={tr('ui.settings.memoryDream.tidyMemoryEachNight')}
          detail={tr('ui.settings.memoryDream.aroundHourYourTimeMerges', { hour })}
        >
          <Toggle label={tr('ui.settings.memoryDream.tidyMemoryEachNight')} checked={enabled} disabled={busy || !memoryOn}
            onChange={(dream_enabled) => void act(() => saveMemorySettings({ dream_enabled }), tr('ui.settings.memoryDream.couldNotChangeTheSetting'))} />
        </SettingRow>
        <SettingRow
          label={tr('ui.settings.memoryDream.tidyNow')}
          detail={running ? tr('ui.settings.memoryDream.tidyingYourMemoryThisTakes')
            : last ? tr('ui.settings.memoryDream.lastTidiedTimeagoSummary', { timeAgo: timeAgo(last.finished || last.started), summary: last.summary || statusText(last) }) : tr('ui.settings.memoryDream.notTidiedYet')}
        >
          <button type="button" className={primaryButtonClass} disabled={busy || running || !memoryOn} onClick={() => void tidyNow()}>
            {running ? tr('ui.settings.memoryDream.tidying') : tr('ui.settings.memoryDream.tidyNow')}
          </button>
        </SettingRow>
      </Group>
      {notice && (
        <p role="status" className="mt-2 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px] text-fg-dim">
          {notice} <button type="button" className="ml-1 text-accent-ink hover:underline" onClick={() => setNotice(null)}>{tr('ui.settings.memoryDream.dismiss')}</button>
        </p>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}

      {proposals.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-[12.5px] font-medium">{tr('ui.settings.memoryDream.suggestedChangesLength', { length: proposals.length })}</p>
          <ul className="divide-y divide-line rounded-[12px] border border-accent/40 bg-panel">
            {proposals.map((p) => (
              <ProposalRow key={p.id} proposal={p} busy={busy}
                onAnswer={(approve, keepId) => void act(() => answerDreamProposal(p.id, approve, keepId), tr('ui.settings.memoryDream.couldNotApplyThatChange'))} />
            ))}
          </ul>
        </div>
      )}

      {runs.length > 0 && (
        <div className="mt-3">
          <button type="button" className="text-[12.5px] text-fg-dim hover:text-fg hover:underline" aria-expanded={showRuns}
            onClick={() => setShowRuns((v) => !v)}>
            {showRuns ? tr('ui.settings.memoryDream.hidePastTidyUps') : tr('ui.settings.memoryDream.pastTidyUps')}
          </button>
          {showRuns && (
            <ul className="mt-2 divide-y divide-line rounded-[12px] border border-line bg-panel">
              {runs.map((run) => (
                <li key={run.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-3.5 py-2.5">
                  <div className="min-w-0 flex-1 basis-[240px]">
                    <div className="text-[13px]">{run.summary || statusText(run)}</div>
                    <div className="mt-0.5 text-[11.5px] text-fg-dim">
                      {run.trigger === 'night' ? tr('ui.settings.memoryDream.nightly') : tr('ui.settings.memoryDream.youAsked')} · {timeAgo(run.started)} · {statusText(run)}
                    </div>
                  </div>
                  {run.status === 'done' && (
                    <button type="button" disabled={busy} className={quietButtonClass}
                      title={tr('ui.settings.memoryDream.putBackEverythingThisTidy')}
                      onClick={() => void act(async () => {
                        const n = await undoDream(run.id);
                        setNotice(n ? tr('ui.settings.memoryDream.undidNValue', { n, value: n === 1 ? 'change' : 'changes' }) : tr('ui.settings.memoryDream.nothingToUndo'));
                      }, tr('ui.settings.memoryDream.couldNotUndoIt'))}>
                      {tr('ui.settings.memoryDream.undo')}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

/** Notion backup: after every tidy, memories are copied to one page in the person's Notion, and can be restored from it. */
export function NotionBackupSection({ memoryOn, refresh, onChanged }: { memoryOn: boolean; refresh?: unknown; onChanged: () => void }) {
  const [state, setState] = useState<NotionBackupState | null>(null);
  const [busy, setBusy] = useState<'backup' | 'restore' | 'toggle' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { notionBackupStatus().then(setState).catch(() => {}); }, [refresh]);

  const act = async (kind: 'backup' | 'restore' | 'toggle', fn: () => Promise<void>, fallback: string) => {
    setBusy(kind);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(null);
    }
  };

  if (!state) return null;
  const lastOk = state.last_ok ? timeAgo(state.last_ok) : '';
  let detail: string;
  if (!state.connected) detail = tr('ui.settings.memoryNotion.connectToKeepACopy');
  else if (state.status === 'restore_first') detail = tr('ui.settings.memoryNotion.restoreFirst');
  else if (state.status === 'waiting_review') detail = tr('ui.settings.memoryNotion.waitingReview');
  else if (state.status === 'failed') detail = tr('ui.settings.memoryNotion.lastFailed');
  else if (state.status === 'not_connected') detail = tr('ui.settings.memoryNotion.signInAgain');
  else if (lastOk) detail = tr('ui.settings.memoryNotion.lastBackup', { timeAgo: lastOk, count: state.count });
  else detail = tr('ui.settings.memoryNotion.notYet');
  const skills = state.skills;
  let skillsDetail = tr('ui.settings.memoryNotion.skillsNotYet');
  if (skills?.status === 'restore_first') skillsDetail = tr('ui.settings.memoryNotion.skillsRestoreFirst');
  else if (skills?.status === 'failed') skillsDetail = tr('ui.settings.memoryNotion.skillsLastFailed');
  else if (skills?.status === 'not_connected') skillsDetail = tr('ui.settings.memoryNotion.signInAgain');
  else if (skills?.last_ok) skillsDetail = tr('ui.settings.memoryNotion.skillsLastBackup', { timeAgo: timeAgo(skills.last_ok), count: skills.count });

  return (
    <section aria-label={tr('ui.settings.memoryDream.notionBackup')} className="!mt-6">
      <SubHeading className="!mt-0">{tr('ui.settings.memoryDream.backUpToNotion')}</SubHeading>
      <p className="mb-2 text-[12px] text-fg-dim">
        {tr('ui.settings.memoryNotion.introWithSkills')}
      </p>
      <Group>
        <SettingRow label={tr('ui.settings.memoryNotion.backUpAutomatically')} detail={detail}>
          <Toggle label={tr('ui.settings.memoryNotion.backUpAutomatically')} checked={state.enabled} disabled={busy !== null || !memoryOn}
            onChange={(enabled) => void act('toggle', async () => { setState(await setNotionBackup(enabled)); }, tr('ui.settings.memoryDream.couldNotChangeTheSetting'))} />
        </SettingRow>
        {state.connected ? (
          <SettingRow
            label={tr('ui.settings.memoryDream.yourNotionBackup')}
            detail={state.page_url
              ? <a href={state.page_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.settings.memoryDream.openThePageInNotion')}</a>
              : tr('ui.settings.memoryDream.aPageCalledCognitrixMemory')}
          >
            <button type="button" className={buttonClass} disabled={busy !== null || !memoryOn}
              onClick={() => void act('backup', async () => {
                const next = await notionBackupNow();
                setState(next);
                if (next.status === 'ok' && (!next.skills || next.skills.status === 'ok')) {
                  setNotice(tr('ui.settings.memoryNotion.backedUpWithSkills', { count: next.count, skills: next.skills?.count ?? 0 }));
                } else setError(tr('ui.settings.memoryDream.theBackupToNotionDid'));
              }, tr('ui.settings.memoryDream.couldNotBackUpTo'))}>
              {busy === 'backup' ? tr('ui.settings.memoryDream.backingUp') : tr('ui.settings.memoryDream.backUpNow')}
            </button>
            <button type="button" className={state.status === 'restore_first' ? primaryButtonClass : buttonClass} disabled={busy !== null || !memoryOn}
              onClick={() => void act('restore', async () => {
                const result = await restoreFromNotion();
                setState(result.state);
                const skillsBack = result.skills ?? { restored: 0, already: 0, kept: 0, left_out: 0 };
                setNotice(`${tr('ui.settings.memoryNotion.restored', { restored: result.restored, already: result.already, waiting: result.waiting })} ${tr('ui.settings.memoryNotion.skillsRestored', { restored: skillsBack.restored, already: skillsBack.already + skillsBack.kept })}`);
                onChanged();
              }, tr('ui.settings.memoryDream.couldNotRestoreFromNotion'))}>
              {busy === 'restore' ? tr('ui.settings.memoryDream.restoring') : tr('ui.settings.memoryDream.restoreFromNotion')}
            </button>
          </SettingRow>
        ) : null}
        {state.connected ? (
          <SettingRow
            label={tr('ui.settings.memoryNotion.skillsLabel')}
            detail={<>
              {skillsDetail}
              {skills?.page_url ? <> <a href={skills.page_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.settings.memoryNotion.openSkillsPage')}</a></> : null}
            </>}
          />
        ) : (
          <SettingRow label={tr('ui.settings.memoryDream.notionIsNotConnected')} detail={tr('ui.settings.memoryDream.connectItOnceAndThe')}>
            <a href="/settings/apps" className={buttonClass}>{tr('ui.settings.memoryDream.connectNotion')}</a>
          </SettingRow>
        )}
      </Group>
      {notice && (
        <p role="status" className="mt-2 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px] text-fg-dim">
          {notice} <button type="button" className="ml-1 text-accent-ink hover:underline" onClick={() => setNotice(null)}>{tr('ui.settings.memoryDream.dismiss')}</button>
        </p>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
    </section>
  );
}

function ProposalRow({ proposal, busy, onAnswer }: {
  proposal: DreamProposal;
  busy: boolean;
  onAnswer: (approve: boolean, keepId?: string) => void;
}) {
  const p = proposal;
  const [keep, setKeep] = useState<string>('');
  const existing = p.memories.filter((m) => m.content !== null);
  return (
    <li className="space-y-1.5 px-3.5 py-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded bg-accent/15 px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em] text-accent-ink">{actionLabel(p).toUpperCase()}</span>
        {p.reason && <span className="text-[12px] text-fg-dim">{p.reason}</span>}
      </div>
      {p.action === 'conflict' ? (
        <fieldset className="space-y-1">
          <legend className="text-[12px] text-fg-dim">{tr('ui.settings.memoryDream.whichOneIsRight')}</legend>
          {existing.map((m) => (
            <label key={m.id} className="flex items-start gap-2 text-[13px]">
              <input type="radio" name={`keep-${p.id}`} value={m.id} checked={keep === m.id} onChange={() => setKeep(m.id)} className="mt-1" />
              <span>{m.content}</span>
            </label>
          ))}
        </fieldset>
      ) : (
        <>
          {existing.length > 0 && (
            <ul className="space-y-0.5 text-[13px] text-fg-dim">
              {existing.map((m) => <li key={m.id} className={cn(p.action !== 'add' && p.content && 'line-through decoration-fg-dim/50')}>{m.content}</li>)}
            </ul>
          )}
          {p.content && <p className="text-[13.5px]">{p.content}</p>}
          {p.valid_until && <p className="text-[11.5px] text-fg-dim">{tr('ui.settings.memoryDream.trueUntilValidUntil', { valid_until: p.valid_until })}</p>}
        </>
      )}
      <div className="flex items-center justify-end gap-1">
        <button type="button" disabled={busy} className={quietButtonClass} onClick={() => onAnswer(false)}>{tr('ui.settings.memoryDream.no')}</button>
        <button type="button" disabled={busy || (p.action === 'conflict' && !keep)} className={buttonClass}
          onClick={() => onAnswer(true, keep || undefined)}>
          {p.action === 'conflict' ? tr('ui.settings.memoryDream.keepThisOne') : tr('ui.settings.memoryDream.yes')}
        </button>
      </div>
    </li>
  );
}

function historyLine(c: MemoryHistoryEntry): string {
  const before = c.before?.content;
  const after = c.after?.content;
  if (c.action === 'create') return tr('ui.settings.memoryDream.savedAfter', { after: after ?? '' });
  if (c.action === 'delete') return tr('ui.settings.memoryDream.forgotBefore', { before: before ?? '' });
  if (c.action === 'approve') return tr('ui.settings.memoryDream.approvedAfter', { after: after ?? '' });
  if (before && after && before !== after) return tr('ui.settings.memoryDream.changedBeforeToAfter', { before, after });
  return tr('ui.settings.memoryDream.updatedAfter', { after: after ?? before ?? '' });
}

function byText(by: string): string {
  if (by === 'person') return 'You';
  if (by === 'dream') return tr('ui.settings.memoryDream.dreamMode');
  return tr('ui.settings.memoryDream.anAgent');
}

/** Recent changes to memory, newest first, each with Undo. */
export function MemoryHistory({ reloadKey, onChanged }: { reloadKey: number; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<MemoryHistoryEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    memoryHistory({ limit: 50 }).then(setRows).catch((e) => setError(errorMessage(e, tr('ui.settings.memoryDream.couldNotLoadTheHistory'))));
  }, [open, reloadKey]);

  const undo = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      await undoMemoryChange(id);
      setRows(await memoryHistory({ limit: 50 }));
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.settings.memoryDream.couldNotUndoThatChange')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <button type="button" className="text-fg-dim hover:text-fg hover:underline" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? tr('ui.settings.memoryDream.hideHistory') : tr('ui.settings.memoryDream.history')}
      </button>
      {open && (
        <div className="mt-2 basis-full">
          {error && <p role="alert" className="mb-1 text-[12.5px] text-danger-ink">{error}</p>}
          {rows === null ? <p className="text-[12.5px] text-fg-dim">{tr('ui.settings.memoryDream.loading')}</p> : rows.length === 0 ? (
            <p className="text-[12.5px] text-fg-dim">{tr('ui.settings.memoryDream.noChangesYet')}</p>
          ) : (
            <ul className="divide-y divide-line rounded-[12px] border border-line bg-panel">
              {rows.map((c) => (
                <li key={c.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-3.5 py-2.5">
                  <div className="min-w-0 flex-1 basis-[240px]">
                    <div className={cn('text-[13px]', c.undone && 'text-fg-dim line-through')}>{historyLine(c)}</div>
                    <div className="mt-0.5 text-[11.5px] text-fg-dim">
                      {byText(c.by)} · {timeAgo(c.created)}{c.note ? ` · ${c.note}` : ''}
                    </div>
                  </div>
                  {!c.undone && (
                    <button type="button" disabled={busy} className={quietButtonClass} onClick={() => void undo(c.id)}>{tr('ui.settings.memoryDream.undo')}</button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
