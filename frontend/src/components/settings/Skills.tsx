import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Group, SettingRow, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';

interface MySkill { id: string; name: string; description: string; body: string; author: string; created_at: string | null }
interface ListedSkill { name: string; description: string; scope?: 'mine' | 'everyone' }

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
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} required
          placeholder="weekly-client-report" className={cn(field, 'h-9 font-mono')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[12.5px] font-medium">
        When to use it
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1024} required
          placeholder="One sentence your agents read to decide whether this skill fits." className={cn(field, 'h-9')} />
      </label>
      <label className="flex flex-col gap-1.5 text-[12.5px] font-medium">
        Instructions
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12} required
          placeholder="The steps, the decision rules, and what the result should look like."
          className={cn(field, 'resize-y py-2 font-mono text-[12.5px] leading-relaxed')} />
      </label>
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={primaryButtonClass}>{busy ? 'Saving…' : 'Save skill'}</button>
        <button type="button" disabled={busy} onClick={onCancel} className={buttonClass}>Cancel</button>
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
      setLoadError(errorMessage(e, 'Could not load skills.'));
    }
  };
  useEffect(() => { void load(); }, []);

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
  if (!mine) return <p className="text-[13px] text-fg-dim">Loading…</p>;

  return (
    <>
      <div className="mb-2 flex items-center gap-2">
        <SubHeading className="mb-0 flex-1">MY SKILLS</SubHeading>
        {editing !== 'new' && (
          <button type="button" className={buttonClass} onClick={() => { setError(null); setEditing('new'); }}>+ New skill</button>
        )}
      </div>
      {error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{error}</p>}
      <Group>
        {editing === 'new' && (
          <SkillForm skill={null} busy={busy} onCancel={() => setEditing(null)}
            onSave={(v) => void run(() => api.post('/skills/mine', v), 'Could not save the skill.')} />
        )}
        {mine.length === 0 && editing !== 'new' && (
          <p className="px-3.5 py-4 text-[13px] leading-relaxed text-fg-dim">
            You have no skills of your own yet. Use “Save as skill” in a chat's menu after a job that went well, or write one here.
          </p>
        )}
        {mine.map((skill) => (editing === skill.id ? (
          <SkillForm key={skill.id} skill={skill} busy={busy} onCancel={() => setEditing(null)}
            onSave={(v) => void run(() => api.patch(`/skills/mine/${skill.id}`, v), 'Could not save the skill.')} />
        ) : (
          <SettingRow
            key={skill.id}
            label={<span className="font-mono text-[13px]">/{skill.name}</span>}
            detail={<>{skill.description}{skill.author ? <span className="text-fg-dim"> · learned by {skill.author}</span> : null}</>}
          >
            {confirm?.id === skill.id ? (
              <>
                <span className="text-[12.5px] text-fg-dim">
                  {confirm.action === 'delete' ? 'Delete this skill?' : 'Every agent on this server will be able to load it.'}
                </span>
                <button type="button" disabled={busy} className={confirm.action === 'delete' ? cn(buttonClass, 'border-danger/40 text-danger-ink') : primaryButtonClass}
                  onClick={() => void run(
                    () => (confirm.action === 'delete' ? api.delete(`/skills/mine/${skill.id}`) : api.post(`/skills/mine/${skill.id}/share`)),
                    confirm.action === 'delete' ? 'Could not delete the skill.' : 'Could not share the skill.',
                  )}>
                  {confirm.action === 'delete' ? 'Delete' : 'Share'}
                </button>
                <button type="button" disabled={busy} className={quietButtonClass} onClick={() => setConfirm(null)}>Cancel</button>
              </>
            ) : (
              <>
                <button type="button" className={buttonClass} onClick={() => { setError(null); setEditing(skill.id); }}>Edit</button>
                {canShare && (
                  <button type="button" className={buttonClass} onClick={() => setConfirm({ id: skill.id, action: 'share' })}>Share with everyone</button>
                )}
                <button type="button" className={quietButtonClass} onClick={() => setConfirm({ id: skill.id, action: 'delete' })}>Delete</button>
              </>
            )}
          </SettingRow>
        )))}
      </Group>

      <SubHeading className="mt-7">SHARED WITH EVERYONE</SubHeading>
      <Group>
        {shared.length === 0 && <p className="px-3.5 py-4 text-[13px] text-fg-dim">No shared skills on this server.</p>}
        {shared.map((skill) => (
          <SettingRow key={skill.name} label={<span className="font-mono text-[13px]">/{skill.name}</span>} detail={skill.description} />
        ))}
      </Group>
      <p className="mt-2 text-[12px] leading-relaxed text-fg-dim">
        Shared skills are the same for everyone on this server{canShare ? '. As an admin you can share one of yours.' : ' and are added by an admin.'}
      </p>
    </>
  );
}
