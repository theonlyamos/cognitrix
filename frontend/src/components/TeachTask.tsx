import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import {
  draftTaughtSkill, saveTaughtSkill, teachingStatus,
  type TaughtSkill, type TaughtStep,
} from '@/lib/sandbox';

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
      return `Type "${step.text ?? ''}"${step.target ? ` into ${step.target}` : ''}${where}`;
    case 'key':
      return `Press ${step.keys}${where}`;
    case 'scroll':
      return `Scroll ${step.direction}${(step.amount ?? 1) > 1 ? ` ×${step.amount}` : ''}${where}`;
    case 'window_opened':
      return `A window opened: ${step.window || step.app || ''}`;
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
      {error && <p role="alert" className="text-danger-ink">Recording stopped: {error}</p>}
      {steps.length === 0 ? (
        <p>Nothing recorded yet. Do the task on the screen the way you'd want the agent to do it.</p>
      ) : (
        <>
          <p>{steps.length} step{steps.length === 1 ? '' : 's'} so far</p>
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
      setError(errorMessage(err, "Couldn't write the skill."));
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
      setError(errorMessage(err, "Couldn't save the skill."));
    } finally {
      setBusy('');
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Teach a task"
      className="absolute inset-0 z-20 flex flex-col overflow-y-auto bg-panel p-4 text-[12.5px]"
    >
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-3">
        <div className="flex items-center gap-2">
          <h3 className="mr-auto text-[14px] font-semibold">Teach {agentName} this task</h3>
          <button type="button" className={quietButtonClass} onClick={onClose}>{saved ? 'Close' : 'Discard'}</button>
        </div>

        {saved && skill ? (
          <div className="rounded-lg border border-line bg-panel-2 p-3.5 leading-relaxed">
            <p className="font-medium">Saved as your skill <code>/{skill.name}</code>.</p>
            <p className="mt-1 text-fg-dim">
              Your agents can now do this on their computer. Ask in a chat, for example “{skill.description}”, or type <code>/{skill.name}</code>. You can edit it later in Settings, Skills.
            </p>
          </div>
        ) : (
          <>
            <details className="rounded-lg border border-line bg-panel-2 p-2.5" open={!skill}>
              <summary className="cursor-pointer text-fg-dim">What was recorded ({real.length} step{real.length === 1 ? '' : 's'})</summary>
              {real.length === 0 ? (
                <p className="mt-2 text-fg-dim">Nothing was recorded. Close this, press Teach again and do the task on the screen.</p>
              ) : (
                <ol className="mt-2 list-decimal space-y-0.5 pl-5 font-mono text-[11.5px]">
                  {steps.map((s, i) => <li key={i} className={cn(s.kind === 'window_opened' && 'list-none text-fg-dim')}>{stepLine(s)}</li>)}
                </ol>
              )}
              <p className="mt-2 text-[11.5px] text-fg-dim">Passwords are never recorded; the agent will ask you to sign in instead.</p>
            </details>

            {!skill && real.length > 0 && (
              <>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">What is this task?</span>
                  <input className={fieldClass} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Download this month's invoice" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">Anything the agent should know? <span className="font-normal text-fg-dim">(optional)</span></span>
                  <textarea className={cn(fieldClass, 'min-h-[64px]')} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. The search term changes each time. Skip the cookie banner." />
                </label>
                <div>
                  <button type="button" className={primaryButtonClass} disabled={!!busy} onClick={() => void draft()}>
                    {busy === 'drafting' ? 'Writing the skill…' : 'Write the skill'}
                  </button>
                </div>
              </>
            )}

            {skill && (
              <>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">Name</span>
                  <input className={cn(fieldClass, 'font-mono')} value={skill.name} onChange={(e) => setSkill({ ...skill, name: e.target.value })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">When to use it</span>
                  <input className={fieldClass} value={skill.description} onChange={(e) => setSkill({ ...skill, description: e.target.value })} />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-medium">Instructions</span>
                  <textarea className={cn(fieldClass, 'min-h-[260px] font-mono text-[12px]')} value={skill.body} onChange={(e) => setSkill({ ...skill, body: e.target.value })} />
                </label>
                <div className="flex gap-2">
                  <button type="button" className={primaryButtonClass} disabled={!!busy} onClick={() => void save()}>
                    {busy === 'saving' ? 'Saving…' : 'Save skill'}
                  </button>
                  <button type="button" className={buttonClass} disabled={!!busy} onClick={() => void draft()}>Write it again</button>
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
