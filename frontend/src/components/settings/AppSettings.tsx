import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import {
  getAppSettings, saveAppSettings,
  type AppSetting, type AppSettingGroup, type AppSettingsPage, type AppSettingsPageId,
} from '@/lib/appSettings';
import { Toggle } from '@/pages/Notifications';
import { tr } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { Group, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from './ui';

const fieldClass =
  'h-10 w-full min-w-0 rounded-lg border border-line bg-panel-2 px-3 text-[13.5px] outline-none focus:border-accent disabled:opacity-60 md:h-9';
const areaClass =
  'w-full min-w-0 rounded-lg border border-line bg-panel-2 px-3 py-2 font-mono text-[12.5px] leading-relaxed outline-none focus:border-accent disabled:opacity-60';

type Drafts = Record<string, string>;

const isOn = (raw: string) => ['1', 'true', 'yes', 'on'].includes(raw.trim().toLowerCase());

function Control({ s, draft, setDraft, disabled }: {
  s: AppSetting;
  draft: string | undefined;
  setDraft: (v: string | undefined) => void;
  disabled: boolean;
}) {
  const value = draft ?? s.value;
  const hint = s.placeholder || s.default;
  switch (s.kind) {
    case 'toggle':
      return <Toggle checked={isOn(value || s.default)} onChange={(on) => setDraft(on ? '1' : '0')} label={s.label} disabled={disabled} />;
    case 'select': {
      const shown = value || (s.options.some((o) => o.value === '') ? '' : s.default);
      return (
        <select aria-label={s.label} value={shown} disabled={disabled} onChange={(e) => setDraft(e.target.value)} className={cn(fieldClass, 'w-auto max-w-full')}>
          {s.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    }
    case 'textarea':
    case 'list':
      return (
        <textarea aria-label={s.label} value={value} disabled={disabled} placeholder={hint} rows={3}
          onChange={(e) => setDraft(e.target.value)} className={areaClass} />
      );
    case 'secret':
      if (s.is_set && draft === undefined) {
        return (
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone="ok">{tr('ui.settings.appSettings.set')}</StatusPill>
            {s.hint && <span className="font-mono text-[11.5px] text-fg-dim">{s.hint}</span>}
            {!disabled && (
              <>
                <button type="button" className={quietButtonClass} onClick={() => setDraft(' ')}>{tr('ui.settings.appSettings.replace')}</button>
                <button type="button" className={quietButtonClass} onClick={() => setDraft('')}>{tr('ui.settings.appSettings.remove')}</button>
              </>
            )}
          </div>
        );
      }
      if (s.is_set && draft === '') {
        return (
          <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-dim">
            {tr('ui.settings.appSettings.removedOnSave')}
            <button type="button" className={quietButtonClass} onClick={() => setDraft(undefined)}>{tr('ui.settings.appSettings.undo')}</button>
          </div>
        );
      }
      return s.multiline ? (
        <textarea aria-label={s.label} value={(draft ?? '').trimStart()} disabled={disabled} rows={4} autoComplete="off" spellCheck={false}
          onChange={(e) => setDraft(e.target.value)} className={areaClass} />
      ) : (
        <input type="password" aria-label={s.label} value={(draft ?? '').trimStart()} disabled={disabled} autoComplete="new-password"
          placeholder={s.is_set ? '' : tr('ui.settings.appSettings.notSet')}
          onChange={(e) => setDraft(e.target.value)} className={fieldClass} />
      );
    default:
      return (
        <input type={s.kind === 'number' ? 'number' : 'text'} inputMode={s.kind === 'number' ? 'decimal' : undefined} min={s.kind === 'number' ? 0 : undefined}
          aria-label={s.label} value={value} disabled={disabled} placeholder={hint}
          onChange={(e) => setDraft(e.target.value)} className={fieldClass} />
      );
  }
}

function Row({ s, draft, setDraft, disabled }: {
  s: AppSetting;
  draft: string | undefined;
  setDraft: (v: string | undefined) => void;
  disabled: boolean;
}) {
  const inline = s.kind === 'toggle' || s.kind === 'select';
  const detail = (
    <>
      {s.help}
      {s.restart && <span className="block text-fg-dim/80">{tr('ui.settings.appSettings.appliesAfterARestart')}</span>}
    </>
  );
  return (
    <div className={cn('flex flex-wrap gap-x-3 gap-y-2 px-3.5 py-3', inline ? 'items-center' : 'flex-col')}>
      <div className="min-w-0 flex-1 basis-[200px]">
        <div className="text-[13.5px] font-medium">{s.label}</div>
        {(s.help || s.restart) && <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">{detail}</div>}
      </div>
      <div className={cn('max-w-full', inline ? 'flex-none' : 'w-full md:max-w-[560px]')}>
        <Control s={s} draft={draft} setDraft={setDraft} disabled={disabled} />
      </div>
    </div>
  );
}

function GroupCard({ group, canEdit, page, onSaved }: {
  group: AppSettingGroup;
  canEdit: boolean;
  page: AppSettingsPageId;
  onSaved: (next: AppSettingsPage) => void;
}) {
  const [drafts, setDrafts] = useState<Drafts>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const changed = Object.entries(drafts).filter(([name, v]) => {
    const s = group.settings.find((x) => x.name === name);
    if (!s) return false;
    if (s.kind === 'secret') return v.trim() !== '' || (v === '' && s.is_set);
    return v !== s.value;
  });

  const setDraft = (name: string, v: string | undefined) => {
    setMessage(null);
    setDrafts((cur) => {
      const next = { ...cur };
      if (v === undefined) delete next[name];
      else next[name] = v;
      return next;
    });
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      onSaved(await saveAppSettings(page, Object.fromEntries(changed.map(([n, v]) => [n, v.trim() === '' ? '' : v]))));
      setDrafts({});
      setMessage({ ok: true, text: tr('ui.settings.appSettings.saved') });
    } catch (e) {
      setMessage({ ok: false, text: errorMessage(e, tr('ui.settings.appSettings.couldNotSave')) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <SubHeading className="mt-7">{group.title.toUpperCase()}</SubHeading>
      <Group>
        {group.settings.map((s) => (
          <Row key={s.name} s={s} draft={drafts[s.name]} setDraft={(v) => setDraft(s.name, v)} disabled={!canEdit || busy} />
        ))}
        {(changed.length > 0 || message) && (
          <div className="flex flex-wrap items-center justify-end gap-2 px-3.5 py-2.5">
            {message && <span className={cn('mr-auto text-[12.5px]', message.ok ? 'text-fg-dim' : 'text-danger')} role="status">{message.text}</span>}
            {changed.length > 0 && (
              <>
                <button type="button" className={buttonClass} disabled={busy} onClick={() => { setDrafts({}); setMessage(null); }}>{tr('ui.settings.appSettings.cancel')}</button>
                <button type="button" className={primaryButtonClass} disabled={busy} onClick={() => void save()}>
                  {busy ? tr('ui.settings.appSettings.saving') : tr('ui.settings.appSettings.save')}
                </button>
              </>
            )}
          </div>
        )}
      </Group>
    </>
  );
}

/** The app-wide settings that belong on one Settings section. Admins edit
 *  them; everyone else sees them read-only (never a secret's value). */
export function AppSettings({ page }: { page: AppSettingsPageId }) {
  const [data, setData] = useState<AppSettingsPage | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    getAppSettings(page)
      .then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(errorMessage(e, tr('ui.settings.appSettings.couldNotLoad'))); });
    return () => { live = false; };
  }, [page]);

  if (error) return <p className="mt-7 text-[12.5px] text-danger">{error}</p>;
  if (!data) return null;
  return (
    <div>
      {!data.can_edit && <p className="mt-7 text-[12.5px] text-fg-dim">{tr('ui.settings.appSettings.onlyAnAdminCanChange')}</p>}
      {data.groups.map((g) => (
        <GroupCard key={g.title} group={g} canEdit={data.can_edit} page={page} onSaved={setData} />
      ))}
    </div>
  );
}
