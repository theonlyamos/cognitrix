import { useTheme, type ThemeMode } from '@/context/ThemeContext';
import { setReduceMotion, setSendKey, useReduceMotion, useSendKey, type SendKey } from '@/lib/preferences';
import { Toggle } from '@/pages/Notifications';
import { LANGUAGES, getLanguageChoice, setLanguage, useLanguage, useT, type LanguageCode, type MessageKey } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { ThemeLibrary, ThemeSwatch } from './Themes';
import { Group, Segmented, SettingRow, SubHeading } from './ui';

const MODES: { value: ThemeMode; label: MessageKey }[] = [
  { value: 'system', label: 'appearance.theme.system' },
  { value: 'light', label: 'appearance.theme.light' },
  { value: 'dark', label: 'appearance.theme.dark' },
];

export function AppearanceSettings() {
  const { mode, setMode, themes, selection } = useTheme();
  const lightTheme = themes.find((x) => x.id === selection.light && x.light) ?? themes[0];
  const darkTheme = themes.find((x) => x.id === selection.dark && x.dark) ?? themes[0];
  const sendKey = useSendKey();
  const reduceMotion = useReduceMotion();
  const t = useT();
  useLanguage();
  const choice = getLanguageChoice();

  return (
    <>
    <Group>
      <div className="p-3.5">
        <div className="mb-2.5 text-[13.5px] font-medium">{t('appearance.theme')}</div>
        <div role="radiogroup" aria-label={t('appearance.theme')} className="grid grid-cols-3 gap-2.5">
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
                    <span className="absolute inset-0"><ThemeSwatch theme={lightTheme} appearance="light" /></span>
                    <span className="absolute inset-0 [clip-path:polygon(100%_0,100%_100%,0_100%)]"><ThemeSwatch theme={darkTheme} appearance="dark" /></span>
                  </>
                ) : (
                  <ThemeSwatch theme={m.value === 'light' ? lightTheme : darkTheme} appearance={m.value} />
                )}
              </span>
              <span className="mt-1.5 block text-[12.5px]">{t(m.label)}</span>
            </button>
          ))}
        </div>
      </div>
    </Group>
    <SubHeading className="!mt-6">THEMES</SubHeading>
    <ThemeLibrary />
    <SubHeading className="!mt-6">MORE</SubHeading>
    <Group>
      <SettingRow label={t('appearance.language')} detail={t('appearance.language.detail')}>
        <select
          aria-label={t('appearance.language')}
          value={choice}
          onChange={(e) => setLanguage(e.target.value as LanguageCode | '')}
          className="h-9 rounded-lg border border-line bg-panel-2 px-2 text-[12.5px] outline-none focus:border-accent md:h-8"
        >
          <option value="">{t('appearance.language.auto')}</option>
          {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
        </select>
      </SettingRow>
      <SettingRow label={t('appearance.sendWith')} detail={t(sendKey === 'enter' ? 'appearance.sendWith.shiftEnter' : 'appearance.sendWith.enter')}>
        <Segmented<SendKey>
          label={t('appearance.sendWith')}
          value={sendKey}
          onChange={setSendKey}
          options={[{ value: 'enter', label: 'Enter' }, { value: 'ctrl-enter', label: 'Ctrl+Enter' }]}
        />
      </SettingRow>
      <SettingRow label={t('appearance.reduceMotion')} detail={t('appearance.reduceMotion.detail')}>
        <Toggle label={t('appearance.reduceMotion')} checked={reduceMotion} onChange={setReduceMotion} />
      </SettingRow>
    </Group>
    </>
  );
}
