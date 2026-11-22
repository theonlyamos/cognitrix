import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { Toggle } from '@/pages/Notifications';
import { timeAgo } from '@/lib/agentDraft';
import { cn } from '@/lib/utils';
import {
  removeLearned, reviewNow, reviewStatus, saveMemorySettings,
  type LearnedItem, type ReviewRun, type ReviewStatus,
} from '@/lib/memories';
import { Group, SettingRow, SubHeading, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

const SHOWN = 8;

function runText(run: ReviewRun): string {
  if (run.status === 'running') return tr('ui.settings.memoryReview.reviewing');
  if (run.status === 'failed') return tr('ui.settings.memoryReview.couldnTFinish');
  if (run.status === 'skipped') return run.summary || tr('ui.settings.memoryReview.skipped');
  return run.summary || tr('ui.settings.memoryReview.done');
}

function kindLabel(item: LearnedItem): string {
  if (item.kind === 'skill') return item.action === 'updated' ? tr('ui.settings.memoryReview.skillImproved') : tr('ui.settings.memoryReview.newSkill');
  if (item.kind === 'knowledge') return item.action === 'updated' ? tr('ui.settings.memoryReview.noteUpdated') : tr('ui.settings.memoryReview.note');
  return item.action === 'proposed' ? tr('ui.settings.memoryReview.memoryToApprove') : tr('ui.settings.memoryReview.memory');
}

function removeLabel(item: LearnedItem): string {
  return item.action === 'updated' ? tr('ui.settings.memoryReview.undo') : tr('ui.settings.memoryReview.remove');
}

function removeTitle(item: LearnedItem): string {
  if (item.action === 'updated') return tr('ui.settings.memoryReview.putItBackAsIt');
  if (item.kind === 'skill') return tr('ui.settings.memoryReview.deleteThisSkill');
  if (item.kind === 'knowledge') return tr('ui.settings.memoryReview.deleteThisNoteFromKnowledge');
  return tr('ui.settings.memoryReview.forgetThis');
}

/** Chat review: each night (and on request) learn skills, facts and notes from finished chats and tasks. */
export function ReviewSection({ enabled, onChanged }: { enabled: boolean; onChanged: () => void }) {
  const [status, setStatus] = useState<ReviewStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const wasRunning = useRef(false);

  const load = useCallback(() => reviewStatus().then((s) => {
    setStatus(s);
    if (wasRunning.current && !s.running) {
      const last = (s.runs ?? [])[0];
      if (last) setNotice(runText(last));
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

  if (!status) return null;
  const running = status.running;
  const last = (status.runs ?? [])[0];
  const learned = (status.learned ?? []).filter((item) => !item.removed);
  const shown = showAll ? learned : learned.slice(0, SHOWN);
  const hour = `${String(status.hour).padStart(2, '0')}:00`;

  return (
    <section aria-label={tr('ui.settings.memoryReview.learnFromFinishedChats')} className="!mt-6">
      <SubHeading className="!mt-0">{tr('ui.settings.memoryReview.learnFromFinishedChats_8c9b')}</SubHeading>
      <Group>
        <SettingRow
          label={tr('ui.settings.memoryReview.reviewChatsEachNight')}
          detail={tr('ui.settings.memoryReview.aroundHourYourTimeJust', { hour })}
        >
          <Toggle label={tr('ui.settings.memoryReview.reviewChatsEachNight')} checked={enabled} disabled={busy}
            onChange={(review_enabled) => void act(() => saveMemorySettings({ review_enabled }), tr('ui.settings.memoryReview.couldnTChangeTheSetting'))} />
        </SettingRow>
        <SettingRow
          label={tr('ui.settings.memoryReview.reviewChatsNow')}
          detail={running ? tr('ui.settings.memoryReview.readingYourFinishedChatsAnd')
            : last ? tr('ui.settings.memoryReview.lastReviewTimeagoRuntext', { timeAgo: timeAgo(last.finished || last.started), runText: runText(last) }) : tr('ui.settings.memoryReview.notReviewedYet')}
        >
          <button type="button" className={primaryButtonClass} disabled={busy || running}
            onClick={() => void act(async () => {
              setNotice(null);
              await reviewNow();
              wasRunning.current = true;
            }, tr('ui.settings.memoryReview.couldnTStartTheReview'))}>
            {running ? tr('ui.settings.memoryReview.reviewing') : tr('ui.settings.memoryReview.reviewNow')}
          </button>
        </SettingRow>
      </Group>
      {notice && (
        <p role="status" className="mt-2 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px] text-fg-dim">
          {notice} <button type="button" className="ml-1 text-accent-ink hover:underline" onClick={() => setNotice(null)}>{tr('ui.settings.memoryReview.dismiss')}</button>
        </p>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}

      {learned.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-[12.5px] font-medium">{tr('ui.settings.memoryReview.whatTheReviewKept')}</p>
          <ul className="divide-y divide-line rounded-[12px] border border-line bg-panel">
            {shown.map((item) => (
              <LearnedRow key={item.id} item={item} busy={busy}
                onRemove={() => void act(() => removeLearned(item.id), tr('ui.settings.memoryReview.couldnTRemoveIt'))} />
            ))}
          </ul>
          {learned.length > SHOWN && (
            <button type="button" className="mt-2 text-[12.5px] text-fg-dim hover:text-fg hover:underline"
              onClick={() => setShowAll((v) => !v)}>
              {showAll ? tr('ui.settings.memoryReview.showFewer') : tr('ui.settings.memoryReview.showAllLength', { length: learned.length })}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function LearnedRow({ item, busy, onRemove }: { item: LearnedItem; busy: boolean; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const title = item.kind === 'memory' ? item.content : item.kind === 'skill' ? item.name : item.description || item.name;
  const long = item.kind !== 'memory';
  return (
    <li className="space-y-1.5 px-3.5 py-2.5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1 basis-[240px]">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className={cn('rounded px-1.5 py-0.5 font-mono text-[10px] tracking-[0.06em]',
              item.kind === 'skill' ? 'bg-accent/15 text-accent-ink' : 'bg-panel-2 text-fg-dim')}>
              {kindLabel(item).toUpperCase()}
            </span>
            <span className="min-w-0 break-words text-[13px]">{title}</span>
          </div>
          {item.kind === 'skill' && item.description && (
            <p className="mt-0.5 text-[12px] text-fg-dim">{item.description}</p>
          )}
          <p className="mt-0.5 text-[11.5px] text-fg-dim">
            {item.source_kind === 'task' ? tr('ui.settings.memoryReview.fromTheTask') : tr('ui.settings.memoryReview.fromTheChat')}{' '}
            {item.source_link
              ? <Link to={item.source_link} className="hover:text-fg hover:underline">{item.source_title}</Link>
              : item.source_title}
            {' · '}{timeAgo(item.created)}
          </p>
        </div>
        <div className="flex items-center gap-1">
          {long && (
            <button type="button" className={quietButtonClass} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {open ? tr('ui.settings.memoryReview.hide') : tr('ui.settings.memoryReview.view')}
            </button>
          )}
          {item.kind === 'skill' && (
            <Link to="/settings/skills" className={quietButtonClass}>{tr('ui.settings.memoryReview.edit')}</Link>
          )}
          <button type="button" disabled={busy} className={quietButtonClass} title={removeTitle(item)} onClick={onRemove}>
            {removeLabel(item)}
          </button>
        </div>
      </div>
      {open && long && (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-2.5 text-[12px] leading-relaxed">{item.content}</pre>
      )}
    </li>
  );
}
