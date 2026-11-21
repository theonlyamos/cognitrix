import { useState } from 'react';
import { HARNESS_INSTALL, HARNESS_PROVIDERS, PROVIDER_LABELS } from '@/lib/providers';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { Group, SettingRow, StatusPill } from './ui';
import { tr } from '@/lib/i18n';

/** The command to paste: what comes before ", then run" or a bracketed aside. */
export const installCommand = (line: string) => line.split(/, then | \(/)[0].trim();

function CopyCommand({ line, name }: { line: string; name: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(installCommand(line));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch { /* no clipboard here: the command is on screen to select */ }
  };
  return (
    <span className="flex flex-wrap items-start gap-x-2 gap-y-1">
      <code className="min-w-0 break-words font-mono text-[11.5px]">{line}</code>
      <button type="button" onClick={() => void copy()} aria-label={tr('ui.settings.codingAgents.copyFor', { name })}
        className="flex-none text-[12px] text-accent-ink underline-offset-2 hover:underline">
        {copied ? tr('ui.settings.codingAgents.copied') : tr('ui.settings.codingAgents.copy')}
      </button>
    </span>
  );
}

/** Settings › Models and keys: the coding agent CLIs an agent can run on
 *  (cognitrix/providers/harness.py HARNESSES), signed in with the person's own
 *  plan instead of a key. Whether each is installed comes from /system/info. */
export function CodingAgents() {
  const { info, reload } = useSystemInfo();
  return (
    <div className="mb-6 space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-[13.5px] font-medium">{tr('ui.settings.codingAgents.title')}</div>
        <button type="button" onClick={reload} className="text-[12.5px] text-accent-ink underline-offset-2 hover:underline">
          {tr('ui.settings.codingAgents.checkAgain')}
        </button>
      </div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">{tr('ui.settings.codingAgents.detail')}</p>
      <Group>
        {HARNESS_PROVIDERS.map((id) => {
          const installed = info?.harnesses?.includes(id);
          return (
            <SettingRow
              key={id}
              label={PROVIDER_LABELS[id] || id}
              detail={installed ? undefined : <CopyCommand line={HARNESS_INSTALL[id]} name={PROVIDER_LABELS[id] || id} />}
            >
              {info && (installed
                ? <StatusPill tone="ok">{tr('ui.settings.codingAgents.installed')}</StatusPill>
                : <StatusPill tone="off">{tr('ui.settings.codingAgents.notInstalled')}</StatusPill>)}
            </SettingRow>
          );
        })}
      </Group>
    </div>
  );
}
