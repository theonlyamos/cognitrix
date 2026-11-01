import { useTheme, type ThemeMode } from '@/context/ThemeContext';
import { setReduceMotion, setSendKey, useReduceMotion, useSendKey, type SendKey } from '@/lib/preferences';
import { Toggle } from '@/pages/Notifications';
import { cn } from '@/lib/utils';
import { Group, Segmented, SettingRow } from './ui';

// Fixed swatches: each preview shows that theme whatever theme is on screen.
const SWATCH = {
  light: { side: '#eeeee9', page: '#ffffff', line: '#c6f24e', text: '#16181a' },
  dark: { side: '#1c1f23', page: '#0b0c0e', line: '#c6f24e', text: '#f4f4f2' },
};

function Preview({ kind }: { kind: 'light' | 'dark' }) {
  const s = SWATCH[kind];
  return (
    <span className="grid h-full w-full grid-cols-[28%_1fr]" style={{ background: s.page }}>
      <span style={{ background: s.side }} />
      <span className="flex flex-col gap-1.5 p-2">
        <span className="h-1.5 w-1/2 rounded-full" style={{ background: s.line }} />
        <span className="h-1 w-3/4 rounded-full opacity-40" style={{ background: s.text }} />
        <span className="h-1 w-2/3 rounded-full opacity-40" style={{ background: s.text }} />
      </span>
    </span>
  );
}

const MODES: { value: ThemeMode; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
];

export function AppearanceSettings() {
  const { mode, setMode } = useTheme();
  const sendKey = useSendKey();
  const reduceMotion = useReduceMotion();

  return (
    <Group>
      <div className="p-3.5">
        <div className="mb-2.5 text-[13.5px] font-medium">Theme</div>
        <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2.5">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={mode === m.value}
              onClick={() => setMode(m.value)}
              className={cn(
                'rounded-[10px] border p-1.5 text-center transition-colors',
                mode === m.value ? 'border-accent ring-1 ring-accent' : 'border-line hover:border-fg-dim',
              )}
            >
              <span className="relative block h-14 overflow-hidden rounded-md border border-line">
                {m.value === 'system' ? (
                  <>
                    <span className="absolute inset-0"><Preview kind="light" /></span>
                    <span className="absolute inset-0 [clip-path:polygon(100%_0,100%_100%,0_100%)]"><Preview kind="dark" /></span>
                  </>
                ) : (
                  <Preview kind={m.value} />
                )}
              </span>
              <span className="mt-1.5 block text-[12.5px]">{m.label}</span>
            </button>
          ))}
        </div>
      </div>
      <SettingRow label="Send messages with" detail={sendKey === 'enter' ? 'Shift+Enter adds a new line' : 'Enter adds a new line'}>
        <Segmented<SendKey>
          label="Send messages with"
          value={sendKey}
          onChange={setSendKey}
          options={[{ value: 'enter', label: 'Enter' }, { value: 'ctrl-enter', label: 'Ctrl+Enter' }]}
        />
      </SettingRow>
      <SettingRow label="Reduce motion" detail="Turns off animations and smooth scrolling">
        <Toggle label="Reduce motion" checked={reduceMotion} onChange={setReduceMotion} />
      </SettingRow>
    </Group>
  );
}
