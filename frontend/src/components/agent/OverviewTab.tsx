import { useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { PROVIDER_LABELS } from '@/lib/providers';
import { folderName } from '@/lib/folders';
import { timeAgo, timeUntil, type TabId, type TabProps } from '@/lib/agentDraft';
import { Card, Eyebrow, buttonClass, linkButtonClass, primaryButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

function paramsPreview(params: Record<string, unknown>): string {
  const command = params.command ?? params.path ?? params.url ?? params.task;
  if (typeof command === 'string') return command;
  try {
    return JSON.stringify(params);
  } catch {
    return '';
  }
}

export default function OverviewTab({ agent, draft, overview, overviewError, refreshOverview, goTo, openChat, options }: TabProps) {
  const [busy, setBusy] = useState('');
  const [approvalError, setApprovalError] = useState('');

  const answer = async (approval: { id: string; run_id: string; task_id: string }, approved: boolean) => {
    setBusy(approval.id);
    setApprovalError('');
    try {
      await api.post(`/tasks/${approval.task_id}/runs/${approval.run_id}/approvals/${approval.id}`, { approved });
      refreshOverview();
    } catch (e) {
      setApprovalError(errorMessage(e, 'Could not answer that approval.'));
    } finally {
      setBusy('');
    }
  };

  const stats = overview?.stats;
  const askFirst = draft.tools.filter((t) => draft.toolPermissions[t] === 'ask').length;
  const tiles = [
    { label: 'CHATS · 7 DAYS', value: stats?.chats_week, note: stats ? `${stats.running_chats} running now` : '', tone: stats?.running_chats ? 'text-ok' : 'text-fg-dim' },
    { label: 'TASK RUNS · 7 DAYS', value: stats?.task_runs_week, note: stats ? (stats.failed_runs_week ? `${stats.failed_runs_week} failed` : 'none failed') : '', tone: stats?.failed_runs_week ? 'text-danger-ink' : 'text-fg-dim' },
    { label: 'WAITING ON YOU', value: stats?.pending_approvals, note: stats?.pending_approvals ? 'see below' : 'nothing to approve', tone: stats?.pending_approvals ? 'text-[#a15c00] dark:text-[#e5b567]' : 'text-fg-dim' },
    { label: 'TOOLS', value: draft.tools.length, note: askFirst ? `${askFirst} ask first` : 'default approvals', tone: 'text-fg-dim' },
  ];

  const upcoming = (overview?.tasks || [])
    .flatMap((t) => [
      ...(t.next_run_at ? [{ key: `${t.id}-s`, title: t.title, how: 'on its schedule', at: t.next_run_at }] : []),
      ...(t.wake_at ? [{ key: `${t.id}-w`, title: t.title, how: t.wake_reason ? `it asked to check: ${t.wake_reason}` : 'it scheduled this', at: t.wake_at }] : []),
    ])
    .sort((a, b) => a.at.localeCompare(b.at))
    .slice(0, 5);

  const folders = draft.folders;
  const reviewerMode = draft.reviewerMode || options?.reviewer.mode || 'off';
  const reviewerText = { off: 'second model off', unattended: 'second model checks task runs', always: 'second model checks every risky action' }[reviewerMode] || '';
  const levelText = { auto: 'Only risky actions ask', ask: 'Every action asks', allow: 'Nothing asks' }[draft.permissionLevel];
  const glance: { label: string; value: string; link: string; tab: TabId }[] = [
    { label: 'MODEL', value: `${PROVIDER_LABELS[draft.provider] || draft.provider} · ${draft.model || '—'}${draft.reasoning ? ` · ${draft.reasoning} reasoning` : ''}`, link: 'General', tab: 'general' },
    { label: 'APPROVALS', value: `${levelText} · ${reviewerText}`, link: 'Tools', tab: 'tools' },
    {
      label: 'FOLDERS',
      value: folders === null ? "The app's working folder"
        : folders.length === 0 ? 'A private scratch folder per chat'
          : folders.map((f) => `${folderName(f)}${f.primary ? ' (primary)' : ''}`).join(', '),
      link: 'Working folders', tab: 'folders',
    },
    {
      label: 'CONNECTIONS',
      value: `${draft.mcpServers.length} MCP ${draft.mcpServers.length === 1 ? 'server' : 'servers'} · ${draft.credentials === null ? 'all saved sign-ins' : `${draft.credentials.length} saved sign-in${draft.credentials.length === 1 ? '' : 's'}`}`,
      link: 'Connections', tab: 'connections',
    },
    {
      label: 'TEAMS',
      value: overview
        ? (overview.teams.length
          ? overview.teams.map((t) => `${t.leader_id === agent.id ? 'leads' : 'in'} ${t.name}`).join(' · ')
          : 'Not in a team')
        : '…',
      link: 'Teams', tab: 'teams',
    },
  ];

  return (
    <div className="flex flex-col gap-[18px]">
      {overviewError && (
        <p role="alert" className="rounded-md border border-danger/40 px-3 py-2 text-[13px] text-danger-ink">
          Couldn't load this agent's activity: {overviewError}
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-[10px] border border-line bg-panel px-4 py-3.5">
            <Eyebrow className="text-[10.5px]">{t.label}</Eyebrow>
            <div className="mt-1.5 text-[26px] font-bold leading-none">{t.value ?? '—'}</div>
            <div className={cn('mt-1.5 text-[12.5px]', t.tone)}>{t.note}</div>
          </div>
        ))}
      </div>

      {overview && overview.approvals.length > 0 && (
        <Card tone="warn">
          <Eyebrow className="text-[#a15c00] dark:text-[#e5b567]">WAITING ON YOU</Eyebrow>
          <ul className="mt-2 divide-y divide-line">
            {overview.approvals.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-1 last:pb-0">
                <div className="min-w-[240px] flex-1">
                  <div className="text-[15px] font-medium">{a.tool_name} <span className="font-normal text-fg-dim">· {a.risk_level || 'needs approval'}</span></div>
                  <div className="mt-1 truncate font-mono text-[12px] text-fg-dim" title={paramsPreview(a.params)}>
                    {paramsPreview(a.params)} · from task “{a.task_title}”
                  </div>
                </div>
                <button type="button" disabled={busy === a.id} onClick={() => void answer(a, false)} className={buttonClass}>Deny</button>
                <button type="button" disabled={busy === a.id} onClick={() => void answer(a, true)} className={primaryButtonClass}>Approve</button>
              </li>
            ))}
          </ul>
          {approvalError && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{approvalError}</p>}
        </Card>
      )}

      <div className="grid grid-cols-1 gap-[18px] md:grid-cols-2">
        <Card title="Recent chats" aside={<button type="button" onClick={() => openChat()} className={linkButtonClass}>New chat</button>}>
          {!overview ? (
            <p className="text-[13px] text-fg-dim">Loading…</p>
          ) : overview.recent_chats.length === 0 ? (
            <p className="text-[13px] text-fg-dim">No chats yet.</p>
          ) : (
            <ul className="-mt-1">
              {overview.recent_chats.map((c) => (
                <li key={c.id}>
                  <button type="button" onClick={() => openChat(c.id)}
                    className="flex min-h-11 w-full items-center gap-2.5 border-t border-line py-2 text-left text-[14px] first:border-t-0 hover:text-accent-ink">
                    <span aria-hidden className={cn('h-[7px] w-[7px] flex-none rounded-full', c.running ? 'bg-ok' : 'bg-line')} />
                    <span className="min-w-0 flex-1 truncate">{c.title || 'Untitled chat'}</span>
                    <span className="flex-none text-[12px] text-fg-dim">{c.running ? 'running' : timeAgo(c.last_active)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Coming up" aside={<button type="button" onClick={() => goTo('tasks')} className={linkButtonClass}>All tasks</button>}>
          {!overview ? (
            <p className="text-[13px] text-fg-dim">Loading…</p>
          ) : upcoming.length === 0 ? (
            <p className="text-[13px] text-fg-dim">Nothing scheduled.</p>
          ) : (
            <ul className="-mt-1">
              {upcoming.map((u) => (
                <li key={u.key} className="flex items-center gap-2.5 border-t border-line py-2.5 text-[14px] first:border-t-0">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{u.title}</span>
                    <span className="block truncate text-[12px] text-fg-dim">{u.how}</span>
                  </span>
                  <span className="flex-none font-mono text-[11.5px] text-fg-dim">{timeUntil(u.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Setup at a glance">
        <ul className="-mt-1">
          {glance.map((g) => (
            <li key={g.label}>
              <button type="button" onClick={() => goTo(g.tab)}
                className="flex min-h-11 w-full flex-wrap items-center gap-x-3.5 gap-y-0.5 border-t border-line py-2.5 text-left first:border-t-0 hover:text-accent-ink">
                <Eyebrow className="w-[120px] flex-none text-[10.5px]">{g.label}</Eyebrow>
                <span className="min-w-[200px] flex-1 text-[14px]">{g.value}</span>
                <span className="text-[12px] text-accent-ink">{g.link} ›</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
