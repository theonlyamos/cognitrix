import { useId } from 'react';
import { ASK_HARNESSES, HARNESS_INSTALL, HARNESS_MODES, PROVIDER_LABELS, isHarness } from '@/lib/providers';
import { useSystemInfo } from '@/hooks/useSystemInfo';

/** Under a provider picker set to a coding agent CLI: whether it's installed,
 *  how to get it, and what it may do to files. */
export default function HarnessNote({
  provider, mode, onModeChange, selectClass = '',
}: { provider: string; mode: string; onModeChange: (mode: string) => void; selectClass?: string }) {
  const { info } = useSystemInfo();
  const id = useId();
  if (!isHarness(provider)) return null;
  const label = PROVIDER_LABELS[provider] || provider;
  const installed = info?.harnesses?.includes(provider);
  // Only some CLIs can hand their permission prompts to Cognitrix.
  const modes = HARNESS_MODES.filter((m) => m.value !== 'ask' || ASK_HARNESSES.includes(provider));
  const current = modes.find((m) => m.value === mode) || modes.find((m) => m.value === 'edit')!;
  return (
    <div className="space-y-2 text-[12px] font-normal leading-relaxed text-fg-dim">
      <p>
        Runs {label} on this computer, signed in with your own plan or key, so usage comes out of that account.
        It works on files with {label}'s own tools in the agent's working folder, and can also use this agent's
        Cognitrix tools (memory, tasks, connected apps), which ask for approval the usual way. It works in the desktop
        app and the command line.
      </p>
      {info && !installed && (
        <p role="status" className="text-danger-ink">
          {label} isn't installed on the computer running Cognitrix. Install it with:{' '}
          <code className="font-mono text-fg">{HARNESS_INSTALL[provider]}</code>
        </p>
      )}
      {info && installed && <p>{label} is installed. If it isn't signed in yet: {HARNESS_INSTALL[provider].split(', then ').pop()}.</p>}
      <label htmlFor={id} className="flex flex-col gap-1 text-[13px] font-medium text-fg">
        What it can do
        <select id={id} value={current.value} onChange={(e) => onModeChange(e.target.value)} className={selectClass}>
          {modes.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        <span className="text-[12px] font-normal text-fg-dim">{current.detail}</span>
      </label>
    </div>
  );
}
