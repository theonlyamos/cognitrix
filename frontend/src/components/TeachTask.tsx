import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import {
  draftTaughtSkill, saveTaughtSkill, teachingStatus,
  type TaughtSkill, type TaughtStep,
} from '@/lib/sandbox';
import { tr } from '@/lib/i18n';

const fieldClass = 'w-full rounded-lg border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] outline-none focus:border-accent';

/** One step as a short line, e.g. "Click button 'Sign in' in chromium". */
function stepLine(step: TaughtStep): string {
  const where = step.app && step.app !== 'desktop' ? ` in ${step.app}` : '';
  switch (step.kind) {
    case 'click':
    case 'double_click':
    case 'right_click':
    case 'middle_click': {
      const verb = { click: 'Click', double_click: 'Double-click', right_click: 'Right-click', middle_click: 'Middle-click' }[step.kind];
      return `${verb} ${step.target || (step.app === 'desktop' ? 'the desktop' : 'the window')}${where}`;
    }
    case 'type':
      return tr('ui.teachTask.typeTextValueWhere', { text: step.text ?? '', value: step.target ? ` into ${step.target}` : '', where });
    case 'key':
      return tr('ui.teachTask.pressKeysWhere', { keys: step.keys, where });
    case 'scroll':
      return tr('ui.teachTask.scrollDirectionValueWhere', { direction: step.direction, value: (step.amount ?? 1) > 1 ? ` ×${step.amount}` : '', where });
    case 'window_opened':
      return tr('ui.teachTask.aWindowOpenedWindow', { window: step.window || step.app || '' });
    default:
      return '';
  }
}

/** While recording: the last few steps, so the person sees it's working. */
export function TeachLive({ desktop }: { desktop: string }) {
  const [steps, setSteps] = useState<TaughtStep[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const got = await teachingStatus(desktop);
        if (!stop) {
          setSteps(got.steps);
          setError(got.error || '');
        }
      } catch {
        // The next tick tries again.
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1500);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [desktop]);
  const recent = steps.slice(-3);
  return (
    <div className="flex min-w-0 flex-col gap-0.5 px-3 pb-2 text-[12px] text-fg-dim" aria-live="polite">
      {error && <p role="alert" className="text-danger-ink">{tr('ui.teachTask.recordingStoppedError', { error })}</p>}
      {steps.length === 0 ? (
        <p>{tr('ui.teachTask.nothingRecordedYetDoThe')}</p>
      ) : (
        <>
          <p>{steps.length}{' '}{tr('ui.teachTask.step')}{steps.length === 1 ? '' : 's'}{' '}{tr('ui.teachTask.soFar')}</p>
          <ol className="min-w-0" start={steps.length - recent.length + 1}>
            {recent.map((s, i) => <li key={i} className="truncate font-mono text-[11.5px]">{stepLine(s)}</li>)}
          </ol>
        </>
      )}
    </div>
  );
}

/** After recording: name the task, let a model write the skill, edit, save. */
export function TeachReview({ desktop, agentId, agentName, steps, onClose }: {
  desktop: string;
  agentId?: string | null;
  agentName: string;
  steps: TaughtStep[];
  onClose: () => void;
}) {
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [skill, setSkill] = useState<TaughtSkill | null>(null);
  const [busy, setBusy] = useState<'' | 'drafting' | 'saving'>('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const real = steps.filter((s) => s.kind !== 'window_opened');

  async function draft() {
    setBusy('drafting');
    setError('');
    try {
      setSkill(await draftTaughtSkill({ steps, title, note, desktop, agentId }));
    } catch (err) {
      setError(errorMessage(err, tr('ui.teachTask.couldnTWriteTheSkill')));
    } finally {
      setBusy('');
    }
  }

  async function save() {
    if (!skill) return;
    setBusy('saving');
    setError('');
    try {
      await saveTaughtSkill(skill);
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, tr('ui.teachTask.couldnTSaveTheSkill')));
    } finally {
      setBusy('');
    }
  }

  return (
    <div
      role="dialog"
      aria-label={tr('ui.teachTask.teachATask')}
      className="absolute inset-0 z-20 flex flex-col overflow-y-auto bg-panel p-4 text-[12.5px]"
    >
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-3">
        <div className="flex items-center gap-2">
          <h3 className="mr-auto text-[14px] font-semibold">{tr('ui.teachTask.teachAgentnameThisTask', { agentName })}</h3>
          <button type="button" className={quietButtonClass} onClick={onClose}>{saved ? tr('ui.teachTask.close') : tr('ui.teachTask.discard')}</button>
        </div>

        {saved && skill ? (
          <div className="rounded-lg border border-line bg-panel-2 p-3.5 leading-relaxed">
            <p className="font-medium">{tr('ui.teachTask.savedAsYourSkill')}{' '}<code>/{skill.name}</code>.</p>
            <p className="mt-1 text-fg-dim">
              {tr('ui.teachTask.yourAgentsCanNowDo', { description: skill.description })}{' '}<code>/{skill.name}</code>{tr('ui.teachTask.youCanEditItLater')}
            </p>
          </div>
        ) : (
          <>
            <details className="rounded-lg border border-line bg-panel-2 p-2.5" open={!skill}>
              <summary className="cursor-pointer text-fg-dim">{tr('ui.teachTask.whatWasRecorded')}{real.length}{' '}{tr('ui.teachTask.step')}{real.length === 1 ? '' : 's'})</summary>
              {real.length === 0 ? (
                <p className="mt-2 text-fg-dim">{tr('ui.teachTask.nothingWasRecordedCloseThis')}</p>
              ) : (
                <ol className="mt-2 list-decimal space-y-0.5 pl-5 font-mono text-[11.5px]">
                  {steps.map((s, i) => <li key={i} className={cn(s.kind === 'window_opened' && 'list-none text-fg-dim')}>{stepLine(s)}</li>)}
                </ol>
              )}
              <p className="mt-2 text-[11.5px] text-fg-dim">{tr('ui.teachTask.passwordsAreNeverRecordedThe')}</p>
            </details>

            {!skill && real.length > 0 && (
              <>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">{tr('ui.teachTask.whatIsThisTask')}</span>
                  <input className={fieldClass} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder={tr('ui.teachTask.eGDownloadThisMonth')} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">{tr('ui.teachTask.anythingTheAgentShouldKnow')}{' '}<span className="font-normal text-fg-dim">(optional)</span></span>
                  <textarea className={cn(fieldClass, 'min-h-[64px]')} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder={tr('ui.teachTask.eGTheSearchTerm')} />
                </label>
                <div>
                  <button type="button" className={primaryButtonClass} disabled={!!busy} onClick={() => void draft()}>
                    {busy === 'drafting' ? tr('ui.teachTask.writingTheSkill') : tr('ui.teachTask.writeTheSkill')}
                  </button>
                </div>
              </>
            )}

            {skill && (
              <>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">{tr('ui.teachTask.name')}</span>
                  <input className={cn(fieldClass, 'font-mono')} value={skill.name} onChange={(e) => setSkill({ ...skill, name: e.target.value })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">{tr('ui.teachTask.whenToUseIt')}</span>
                  <input className={fieldClass} value={skill.description} onChange={(e) => setSkill({ ...skill, description: e.target.value })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">{tr('ui.teachTask.instructions')}</span>
                  <textarea className={cn(fieldClass, 'min-h-[260px] font-mono text-[12px]')} value={skill.body} onChange={(e) => setSkill({ ...skill, body: e.target.value })} />
                </label>
                <div className="flex gap-2">
                  <button type="button" className={primaryButtonClass} disabled={!!busy} onClick={() => void save()}>
                    {busy === 'saving' ? tr('ui.teachTask.saving') : tr('ui.teachTask.saveSkill')}
                  </button>
                  <button type="button" className={buttonClass} disabled={!!busy} onClick={() => void draft()}>{tr('ui.teachTask.writeItAgain')}</button>
                </div>
              </>
            )}
          </>
        )}
        {error && <p role="alert" className="text-danger-ink">{error}</p>}
      </div>
    </div>
  );
}
