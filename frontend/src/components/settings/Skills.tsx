import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Group, SettingRow, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

interface MySkill { id: string; name: string; description: string; body: string; author: string; created_at: string | null }
interface ListedSkill { name: string; description: string; category?: string; scope?: 'mine' | 'everyone' }

const field = 'w-full rounded-lg border border-line bg-bg px-3 text-[13px] outline-none focus:border-accent';

function SkillForm({
  skill, busy, onSave, onCancel,
}: {
  skill: MySkill | null;
  busy: boolean;
  onSave: (v: { name: string; description: string; body: string }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(skill?.name ?? '');
  const [description, setDescription] = useState(skill?.description ?? '');
  const [body, setBody] = useState(skill?.body ?? '');
  return (
    <form
      className="flex flex-col gap-3 px-3.5 py-3.5"
      onSubmit={(e) => { e.preventDefault(); onSave({ name, description, body }); }}
    >
      <label className="flex flex-col gap-1.5 text-[12.5px] font-medium">
        {tr('ui.settings.skills.name')}
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} required
          placeholder="weekly-client-report" className={cn(field, 'h-9 font-mono')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[12.5px] font-medium">
        {tr('ui.settings.skills.whenToUseIt')}
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1024} required
          placeholder={tr('ui.settings.skills.oneSentenceYourAgentsRead')} className={cn(field, 'h-9')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[12.5px] font-medium">
        {tr('ui.settings.skills.instructions')}
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12} required
          placeholder={tr('ui.settings.skills.theStepsTheDecisionRules')}
          className={cn(field, 'resize-y py-2 font-mono text-[12.5px] leading-relaxed')} />
      </label>
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={primaryButtonClass}>{busy ? tr('ui.settings.skills.saving') : tr('ui.settings.skills.saveSkill')}</button>
        <button type="button" disabled={busy} onClick={onCancel} className={buttonClass}>{tr('ui.settings.skills.cancel')}</button>
      </div>
    </form>
  );
}

/** Settings › Skills: the person's own skills, and the shared ones everyone has. */
export function SkillsSettings() {
  const [mine, setMine] = useState<MySkill[] | null>(null);
  const [canShare, setCanShare] = useState(false);
  const [shared, setShared] = useState<ListedSkill[]>([]);
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const [confirm, setConfirm] = useState<{ id: string; action: 'delete' | 'share' } | null>(null);
  const [busy, setBusy] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [own, all] = await Promise.all([
        api.get<{ skills: MySkill[]; can_share: boolean }>('/skills/mine'),
        api.get<ListedSkill[]>('/skills'),
      ]);
      setMine(own.data.skills || []);
      setCanShare(Boolean(own.data.can_share));
      setShared((all.data || []).filter((s) => s.scope !== 'mine'));
    } catch (e) {
      setLoadError(errorMessage(e, tr('ui.settings.skills.couldNotLoadSkills')));
    }
  };
  useEffect(() => { void load(); }, []);

  // Real skills first. One-step helpers such as "mouse-click" are things agents
  // do on their own, not commands anyone types, so they stay folded.
  const skills = shared.filter((s) => s.category !== 'system');
  const actions = shared.filter((s) => s.category === 'system');

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setEditing(null);
      setConfirm(null);
      await load();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!mine) return <p className="text-[13px] text-fg-dim">{tr('ui.settings.skills.loading')}</p>;

  return (
    <>
      <div className="mb-2 flex items-center gap-2">
        <SubHeading className="mb-0 flex-1">{tr('ui.settings.skills.mySkills')}</SubHeading>
        {editing !== 'new' && (
          <button type="button" className={buttonClass} onClick={() => { setError(null); setEditing('new'); }}>{tr('ui.settings.skills.newSkill')}</button>
        )}
      </div>
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      <Group>
        {editing === 'new' && (
          <SkillForm skill={null} busy={busy} onCancel={() => setEditing(null)}
            onSave={(v) => void run(() => api.post('/skills/mine', v), tr('ui.settings.skills.couldNotSaveTheSkill'))} />
        )}
        {mine.length === 0 && editing !== 'new' && (
          <p className="px-3.5 py-4 text-[13px] leading-relaxed text-fg-dim">
            {tr('ui.settings.skills.youHaveNoSkillsOf')}
          </p>
        )}
        {mine.map((skill) => (editing === skill.id ? (
          <SkillForm key={skill.id} skill={skill} busy={busy} onCancel={() => setEditing(null)}
            onSave={(v) => void run(() => api.patch(`/skills/mine/${skill.id}`, v), tr('ui.settings.skills.couldNotSaveTheSkill'))} />
        ) : (
          <SettingRow
            key={skill.id}
            label={<span className="font-mono text-[13px]">/{skill.name}</span>}
            detail={<>{skill.description}{skill.author ? <span className="text-fg-dim">{' '}{tr('ui.settings.skills.learnedByAuthor', { author: skill.author })}</span> : null}</>}
          >
            {confirm?.id === skill.id ? (
              <>
                <span className="text-[12.5px] text-fg-dim">
                  {confirm.action === 'delete' ? tr('ui.settings.skills.deleteThisSkill') : tr('ui.settings.skills.everyAgentOnThisServer')}
                </span>
                <button type="button" disabled={busy} className={confirm.action === 'delete' ? cn(buttonClass, 'border-danger/40 text-danger-ink') : primaryButtonClass}
                  onClick={() => void run(
                    () => (confirm.action === 'delete' ? api.delete(`/skills/mine/${skill.id}`) : api.post(`/skills/mine/${skill.id}/share`)),
                    confirm.action === 'delete' ? tr('ui.settings.skills.couldNotDeleteTheSkill') : tr('ui.settings.skills.couldNotShareTheSkill'),
                  )}>
                  {confirm.action === 'delete' ? tr('ui.settings.skills.delete') : tr('ui.settings.skills.share')}
                </button>
                <button type="button" disabled={busy} className={quietButtonClass} onClick={() => setConfirm(null)}>{tr('ui.settings.skills.cancel')}</button>
              </>
            ) : (
              <>
                <button type="button" className={buttonClass} onClick={() => { setError(null); setEditing(skill.id); }}>{tr('ui.settings.skills.edit')}</button>
                {canShare && (
                  <button type="button" className={buttonClass} onClick={() => setConfirm({ id: skill.id, action: 'share' })}>{tr('ui.settings.skills.shareWithEveryone')}</button>
                )}
                <button type="button" className={quietButtonClass} onClick={() => setConfirm({ id: skill.id, action: 'delete' })}>{tr('ui.settings.skills.delete')}</button>
              </>
            )}
          </SettingRow>
        )))}
      </Group>

      <SubHeading className="mt-7">{tr('ui.settings.skills.sharedWithEveryone')}</SubHeading>
      <Group>
        {shared.length === 0 && <p className="px-3.5 py-4 text-[13px] text-fg-dim">{tr('ui.settings.skills.noSharedSkillsOnThis')}</p>}
        {skills.map((skill) => (
          <SettingRow key={skill.name} label={<span className="font-mono text-[13px]">/{skill.name}</span>} detail={skill.description} />
        ))}
      </Group>
      {actions.length > 0 && (
        <>
          <button type="button" aria-expanded={showActions} onClick={() => setShowActions((v) => !v)}
            className="mt-3 flex items-center gap-2 text-[13px] text-fg-dim hover:text-fg">
            <span aria-hidden className={cn('transition-transform', showActions && 'rotate-90')}>▸</span>
            {tr('ui.settings.skills.singleActions', { count: actions.length })}
          </button>
          {showActions && (
            <Group className="mt-2">
              {actions.map((skill) => (
                <SettingRow key={skill.name} label={<span className="font-mono text-[13px]">/{skill.name}</span>} detail={skill.description} />
              ))}
            </Group>
          )}
        </>
      )}
      <p className="mt-2 text-[12px] leading-relaxed text-fg-dim">
        {tr('ui.settings.skills.sharedSkillsAreTheSame')}{canShare ? tr('ui.settings.skills.asAnAdminYouCan') : tr('ui.settings.skills.andAreAddedByAn')}
      </p>
    </>
  );
}
