import { HARNESS_INSTALL, HARNESS_PROVIDERS, PROVIDER_LABELS } from '@/lib/providers';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { Group, SettingRow, StatusPill } from './ui';
import { tr } from '@/lib/i18n';

/** Settings › Models and keys: the coding agent CLIs an agent can run on
 *  (cognitrix/providers/harness.py HARNESSES), signed in with the person's own
 *  plan instead of a key. Whether each is installed comes from /system/info. */
export function CodingAgents() {
  const { info } = useSystemInfo();
  return (
    <div className="mb-6 space-y-2">
      <div className="text-[13.5px] font-medium">{tr('ui.settings.codingAgents.title')}</div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">{tr('ui.settings.codingAgents.detail')}</p>
      <Group>
        {HARNESS_PROVIDERS.map((id) => {
          const installed = info?.harnesses?.includes(id);
          return (
            <SettingRow
              key={id}
              label={PROVIDER_LABELS[id] || id}
              detail={installed ? undefined : <code className="break-words font-mono text-[11.5px]">{HARNESS_INSTALL[id]}</code>}
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
