import { useState, type FormEvent } from 'react';
import { Trash2 } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Select } from '@/lib/components/ui/select';
import type { BoardCommand, BoardFollow, BoardMirror, BoardSchedule, ChannelView } from '@/lib/board';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

const field = 'h-9 min-w-0 rounded-lg border border-line bg-panel px-2.5 text-[12.5px] outline-none placeholder:text-fg-dim focus:border-fg-dim';
const heading = 'text-[13px] font-semibold';
const note = 'text-[12px] leading-snug text-fg-dim';
const iconButton = 'inline-flex h-7 w-7 flex-none items-center justify-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-danger-ink';

function useAct() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      return true;
    } catch (e) {
      setError(errorMessage(e, tr('ui.board.thatDidnTWork')));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, act };
}

/** Who is in a project channel: people, and the agents allowed to post. */
export function ChannelPanel({ board, onChanged }: { board: string; onChanged?: () => void }) {
  const { data, setData } = useResource<ChannelView>(`/board/channels/${encodeURIComponent(board)}`);
  const follows = useResource<BoardFollow[]>('/board/follows');
  const [email, setEmail] = useState('');
  const { busy, error, act } = useAct();
  if (!data || !Array.isArray(data.members)) return null;
  const save = (changes: { members?: string[]; agents?: string[]; externals?: string[] }) => act(async () => {
    const res = await api.put<ChannelView>(`/board/channels/${encodeURIComponent(board)}`, changes);
    setData(res.data);
    onChanged?.();
  });
  const projectIds = new Set(data.project_agents.map((a) => a.id));
  const granted = new Set(data.agents.map((a) => a.id));
  const others = (Array.isArray(follows.data) ? follows.data : []).filter((f) => !projectIds.has(f.agent_id));
  const outside = Array.from(new Set([...data.known_externals, ...data.externals]));
  const addMember = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return;
    if (await save({ members: [...data.members.map((m) => m.id), email.trim()] })) setEmail('');
  };
  return (
    <section aria-label={tr('ui.board.whoIsInThisChannel')} className="flex flex-col gap-2 text-[12.5px]">
      <h2 className={heading}>{tr('ui.board.whoIsInThisChannel')}</h2>
      <p className={note}>{tr('ui.board.peopleYouAddReadAndPost')}</p>
      <ul className="flex flex-col gap-1">
        {data.members.map((m) => (
          <li key={m.id} className="flex items-center justify-between gap-2">
            <span className="truncate">{m.name} <span className="text-fg-dim">{m.email}</span></span>
            {data.owner && (
              <button type="button" className={iconButton} aria-label={tr('ui.board.removeName', { name: m.name })} disabled={busy}
                onClick={() => void save({ members: data.members.filter((x) => x.id !== m.id).map((x) => x.id) })}>
                <Trash2 size={13} aria-hidden />
              </button>
            )}
          </li>
        ))}
      </ul>
      {data.owner && (
        <form onSubmit={(e) => void addMember(e)} className="flex gap-1.5">
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" aria-label={tr('ui.board.addSomeoneByEmail')}
            placeholder={tr('ui.board.addSomeoneByEmail')} className={cn(field, 'flex-1')} />
          <Button type="submit" size="sm" variant="outline" disabled={busy || !email.trim()}>{tr('ui.board.add')}</Button>
        </form>
      )}
      <h3 className="pt-1 text-[12.5px] font-medium">{tr('ui.board.agentsThatMayPost')}</h3>
      <p className={note}>{tr('ui.board.theProjectSAgentsAlwaysMay')}</p>
      <ul className="flex flex-col gap-1">
        {data.project_agents.map((a) => (
          <li key={a.id} className="flex items-center gap-1.5"><input type="checkbox" checked disabled aria-label={a.name} />{a.name}</li>
        ))}
        {others.map((f) => (
          <li key={f.agent_id}>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={granted.has(f.agent_id)} disabled={!data.owner || busy}
                onChange={(e) => void save({ agents: e.target.checked ? [...granted, f.agent_id] : [...granted].filter((x) => x !== f.agent_id) })} />
              {f.agent_name}
            </label>
          </li>
        ))}
      </ul>
      {outside.length > 0 && (
        <>
          <h3 className="pt-1 text-[12.5px] font-medium">{tr('ui.board.outsideAgents')}</h3>
          <ul className="flex flex-col gap-1">
            {outside.map((name) => (
              <li key={name}>
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={data.externals.includes(name)} disabled={!data.owner || busy}
                    onChange={(e) => void save({ externals: e.target.checked ? [...data.externals, name] : data.externals.filter((x) => x !== name) })} />
                  {name}
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className={note}>{tr('ui.board.outsideAgentsJoinWithTheBoardCli')}</p>
      {error && <p role="alert" className="text-danger-ink">{error}</p>}
    </section>
  );
}

/** Repeating posts on this board ("every weekday at 9:00, ask Reviewer to …"). */
export function SchedulesPanel({ board, targets }: { board: string; targets: string[] }) {
  const { data, setData } = useResource<BoardSchedule[]>(`/board/schedules?board=${encodeURIComponent(board)}`);
  const [text, setText] = useState('');
  const [to, setTo] = useState('');
  const [when, setWhen] = useState('weekdays 09:00');
  const { busy, error, act } = useAct();
  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    await act(async () => {
      const res = await api.post<BoardSchedule>('/board/schedules', { board, text, to, when, kind: 'request' });
      setData((rows) => [...(Array.isArray(rows) ? rows : []), res.data]);
      setText('');
    });
  };
  const remove = (row: BoardSchedule) => act(async () => {
    await api.delete(`/board/schedules/${row.id}`);
    setData((rows) => (rows || []).filter((r) => r.id !== row.id));
  });
  const toggle = (row: BoardSchedule) => act(async () => {
    const res = await api.put<BoardSchedule>(`/board/schedules/${row.id}`, {
      board: row.board, text: row.text, to: row.to, kind: row.kind, days: row.days, time: row.time,
      thread: row.thread, enabled: !row.enabled,
    });
    setData((rows) => (rows || []).map((r) => (r.id === row.id ? res.data : r)));
  });
  return (
    <section aria-label={tr('ui.board.repeatingPosts')} className="flex flex-col gap-2 text-[12.5px]">
      <h2 className={heading}>{tr('ui.board.repeatingPosts')}</h2>
      <p className={note}>{tr('ui.board.aRepeatingPostIsARequest')}</p>
      <ul className="flex flex-col gap-1.5">
        {(Array.isArray(data) ? data : []).map((row) => (
          <li key={row.id} className={cn('flex items-start gap-1.5', !row.enabled && 'opacity-60')}>
            <input type="checkbox" checked={row.enabled} disabled={busy} aria-label={tr('ui.board.on')} onChange={() => void toggle(row)} className="mt-0.5" />
            <span className="min-w-0 flex-1">
              <span className="font-medium">{row.when}</span>{row.to ? ` · ${row.to}` : ''}
              <span className="block truncate text-fg-dim">{row.text}</span>
            </span>
            <button type="button" className={iconButton} aria-label={tr('ui.board.removeName', { name: row.when })} disabled={busy}
              onClick={() => void remove(row)}><Trash2 size={13} aria-hidden /></button>
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => void add(e)} className="flex flex-col gap-1.5">
        <input value={text} onChange={(e) => setText(e.target.value)} aria-label={tr('ui.board.whatItAsks')}
          placeholder={tr('ui.board.checkOpenPullRequests')} className={field} />
        <div className="flex gap-1.5">
          <Select value={to} onChange={(e) => setTo(e.target.value)} aria-label={tr('ui.board.whoItIsFor')} className="h-9 w-auto min-w-0 flex-1 text-[12.5px]">
            <option value="">{tr('ui.board.forAnyone')}</option>
            {targets.map((name) => <option key={name} value={name}>{name}</option>)}
          </Select>
          <input value={when} onChange={(e) => setWhen(e.target.value)} aria-label={tr('ui.board.when')}
            placeholder="weekdays 09:00" className={cn(field, 'w-32')} />
        </div>
        <Button type="submit" size="sm" variant="outline" className="self-start" disabled={busy || !text.trim() || !when.trim()}>
          {tr('ui.board.addRepeatingPost')}
        </Button>
      </form>
      {error && <p role="alert" className="text-danger-ink">{error}</p>}
    </section>
  );
}

/** /commands: a post starting with /name runs that workflow; the result is posted back. */
export function CommandsPanel() {
  const { data, setData } = useResource<{ commands: BoardCommand[]; suggested: BoardCommand[] }>('/board/commands');
  const follows = useResource<BoardFollow[]>('/board/follows');
  const agents = Array.isArray(follows.data) ? follows.data : [];
  const [name, setName] = useState('');
  const [agent, setAgent] = useState('');
  const [prompt, setPrompt] = useState('');
  const [open, setOpen] = useState(false);
  const { busy, error, act } = useAct();
  if (!data || !Array.isArray(data.commands)) return null;
  const save = (row: { name: string; prompt: string; description?: string }, agentId: string) => act(async () => {
    const res = await api.put<BoardCommand>(`/board/commands/${encodeURIComponent(row.name)}`, {
      agent_id: agentId, prompt: row.prompt, description: row.description || '',
    });
    setData((d) => d && {
      commands: [...d.commands.filter((c) => c.name !== res.data.name), res.data].sort((a, b) => a.name.localeCompare(b.name)),
      suggested: d.suggested.filter((s) => s.name !== res.data.name),
    });
  });
  const remove = (row: BoardCommand) => act(async () => {
    await api.delete(`/board/commands/${encodeURIComponent(row.name)}`);
    setData((d) => d && { ...d, commands: d.commands.filter((c) => c.name !== row.name) });
  });
  const firstAgent = agent || agents[0]?.agent_id || '';
  return (
    <section aria-label={tr('ui.board.commands')} className="flex flex-col gap-2 text-[12.5px]">
      <h2 className={heading}>{tr('ui.board.commands')}</h2>
      <p className={note}>{tr('ui.board.postSlashNameToRunIt')}</p>
      <ul className="flex flex-col gap-1">
        {data.commands.map((c) => (
          <li key={c.name} className="flex items-start gap-1.5">
            <span className="min-w-0 flex-1">
              <span className="font-mono font-medium">/{c.name}</span> <span className="text-fg-dim">{c.agent_name}</span>
              {c.description && <span className="block truncate text-fg-dim">{c.description}</span>}
            </span>
            <button type="button" className={iconButton} aria-label={tr('ui.board.removeName', { name: `/${c.name}` })} disabled={busy}
              onClick={() => void remove(c)}><Trash2 size={13} aria-hidden /></button>
          </li>
        ))}
      </ul>
      {data.suggested.length > 0 && agents.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {data.suggested.map((s) => (
            <button key={s.name} type="button" disabled={busy} title={s.description}
              className="inline-flex min-h-7 items-center rounded-md border border-dashed border-line px-2 font-mono text-[12px] hover:bg-panel-2"
              onClick={() => void save(s, firstAgent)}>
              + /{s.name}
            </button>
          ))}
        </div>
      )}
      {agents.length > 0 && (
        <Select value={firstAgent} onChange={(e) => setAgent(e.target.value)} aria-label={tr('ui.board.agentThatRunsIt')} className="h-9 w-full text-[12.5px]">
          {agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.agent_name}</option>)}
        </Select>
      )}
      {open ? (
        <form className="flex flex-col gap-1.5" onSubmit={(e) => {
          e.preventDefault();
          void save({ name, prompt }, firstAgent).then((ok) => { if (ok) { setName(''); setPrompt(''); setOpen(false); } });
        }}>
          <input value={name} onChange={(e) => setName(e.target.value)} aria-label={tr('ui.board.commandName')}
            placeholder="deploy-check" className={field} />
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} aria-label={tr('ui.board.whatTheAgentDoes')}
            placeholder={tr('ui.board.whatTheAgentDoes')} className="rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12.5px] outline-none focus:border-fg-dim" />
          <div className="flex gap-1.5">
            <Button type="submit" size="sm" disabled={busy || !name.trim() || !prompt.trim() || !firstAgent}>{tr('ui.board.save')}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>{tr('ui.board.cancel')}</Button>
          </div>
        </form>
      ) : (
        agents.length > 0 && <Button type="button" size="sm" variant="outline" className="self-start" onClick={() => setOpen(true)}>{tr('ui.board.newCommand')}</Button>
      )}
      {error && <p role="alert" className="text-danger-ink">{error}</p>}
    </section>
  );
}

/** Copy this board both ways to a Slack channel. */
export function SlackMirrorPanel({ board }: { board: string }) {
  const { data, setData } = useResource<{ slack_connected: boolean; mirrors: BoardMirror[] }>(
    `/board/mirrors?board=${encodeURIComponent(board)}`);
  const [channel, setChannel] = useState('');
  const { busy, error, act } = useAct();
  if (!data || !Array.isArray(data.mirrors)) return null;
  const add = async (event: FormEvent) => {
    event.preventDefault();
    await act(async () => {
      const res = await api.post<BoardMirror>('/board/mirrors', { board, slack_channel: channel });
      setData((d) => d && { ...d, mirrors: [...d.mirrors, res.data] });
      setChannel('');
    });
  };
  const remove = (row: BoardMirror) => act(async () => {
    await api.delete(`/board/mirrors/${row.id}`);
    setData((d) => d && { ...d, mirrors: d.mirrors.filter((m) => m.id !== row.id) });
  });
  return (
    <section aria-label={tr('ui.board.slackMirror')} className="flex flex-col gap-2 text-[12.5px]">
      <h2 className={heading}>{tr('ui.board.slackMirror')}</h2>
      {!data.slack_connected ? (
        <p className={note}>{tr('ui.board.connectSlackInSettingsChatApps')}</p>
      ) : (
        <>
          <p className={note}>{tr('ui.board.postsAndAnswersAreCopiedBothWays')}</p>
          <ul className="flex flex-col gap-1">
            {data.mirrors.map((m) => (
              <li key={m.id} className="flex items-center justify-between gap-2">
                <span className="font-mono">#{m.slack_channel}</span>
                <button type="button" className={iconButton} aria-label={tr('ui.board.removeName', { name: m.slack_channel })} disabled={busy}
                  onClick={() => void remove(m)}><Trash2 size={13} aria-hidden /></button>
              </li>
            ))}
          </ul>
          <form onSubmit={(e) => void add(e)} className="flex gap-1.5">
            <input value={channel} onChange={(e) => setChannel(e.target.value)} aria-label={tr('ui.board.slackChannelId')}
              placeholder={tr('ui.board.boardPanels.c0123abcd')} className={cn(field, 'flex-1 font-mono')} />
            <Button type="submit" size="sm" variant="outline" disabled={busy || !channel.trim()}>{tr('ui.board.add')}</Button>
          </form>
        </>
      )}
      {error && <p role="alert" className="text-danger-ink">{error}</p>}
    </section>
  );
}
