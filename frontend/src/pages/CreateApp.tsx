import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, BarChart3, ClipboardCheck, Copy, FileInput, HeartPulse, ListChecks, Sparkles } from 'lucide-react';
import { api } from '@/lib/api';
import { CHAT_AUTOSEND_KEY, CHAT_DRAFT_KEY } from '@/lib/chatDraft';
import { cn } from '@/lib/utils';

interface Agent { id: string; name: string; automation?: { standing?: { primary?: boolean } } | null }

const TEMPLATES = [
  { icon: BarChart3, title: 'Metrics dashboard', detail: 'Charts of the numbers you watch, refreshed on a schedule.',
    prompt: 'A metrics dashboard with a few key numbers at the top and charts below. Ask me which numbers and where they come from.' },
  { icon: ClipboardCheck, title: 'Review queue', detail: 'A list of items to approve or reject, with notes.',
    prompt: 'A review queue: a list of items people approve or reject with a note, saved so everyone sees the same state.' },
  { icon: ListChecks, title: 'Project status page', detail: 'Milestones, owners and what is blocked.',
    prompt: 'A project status page showing milestones, owners, dates and blockers, that people on the team can update.' },
  { icon: FileInput, title: 'Request form', detail: 'A form people fill in, with a table of what came in.',
    prompt: 'A request form people fill in, with a table of the requests that came in and their status.' },
  { icon: HeartPulse, title: 'Customer health', detail: 'Accounts with a health score and recent activity.',
    prompt: 'A customer health page listing accounts with a health score, recent activity and who owns each one.' },
  { icon: Copy, title: 'Staging copy of a site', detail: 'Run a branch of a web project to try it out.',
    prompt: 'Run a copy of one of my web projects so I can try a branch. Ask me which folder and branch.' },
] as const;

function buildPrompt(idea: string): string {
  return `Build me a web app and publish it with the Publish App tool so it gets a link.\n\nWhat it should do: ${idea.trim()}\n\n`
    + 'Keep its data in $APP_DATA_DIR, listen on $PORT, use relative URLs, and tell me the link when it is live.';
}

/** /apps/new: describe an app or start from a template; an agent builds and publishes it in a chat. */
export default function CreateApp() {
  const navigate = useNavigate();
  const [idea, setIdea] = useState('');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState(() => {
    try {
      return localStorage.getItem('selectedAgentId') || '';
    } catch {
      return '';
    }
  });

  useEffect(() => {
    api.get<Agent[]>('/agents').then((r) => {
      const list = Array.isArray(r.data) ? r.data : [];
      setAgents(list);
      setAgentId((current) => (list.some((a) => a.id === current) ? current
        : (list.find((a) => a.automation?.standing?.primary) ?? list[0])?.id ?? ''));
    }).catch(() => setAgents([]));
  }, []);

  const build = (e?: FormEvent) => {
    e?.preventDefault();
    if (!idea.trim()) return;
    if (!agentId) {
      navigate('/agents/new');
      return;
    }
    try {
      localStorage.setItem('selectedAgentId', agentId);
      localStorage.setItem(`chatSession:${agentId}`, '');
      sessionStorage.setItem(CHAT_DRAFT_KEY, buildPrompt(idea));
      sessionStorage.setItem(CHAT_AUTOSEND_KEY, '1');
    } catch {
      /* storage off: the chat still opens */
    }
    navigate('/home');
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 pt-5 md:px-8 md:pt-8">
        <Link to="/apps" className="inline-flex items-center gap-1.5 text-[13px] text-fg-dim hover:text-fg"><ArrowLeft size={14} aria-hidden />Apps</Link>
        <h1 className="mt-3 text-[22px] font-semibold tracking-tight">Create an app</h1>
        <p className="mt-1 text-[13.5px] text-fg-dim">Describe what you need. An agent builds it, runs it for you and gives you a private link that only people you share it with can open.</p>

        <form onSubmit={build} className="mt-5 rounded-[12px] border border-line bg-panel p-3">
          <label htmlFor="app-idea" className="sr-only">What should the app do?</label>
          <textarea id="app-idea" value={idea} onChange={(e) => setIdea(e.target.value)} rows={4}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) build(); }}
            placeholder="A page where the team logs support calls, with a weekly summary chart…"
            className="w-full resize-y bg-transparent text-[14px] leading-relaxed outline-none placeholder:text-fg-dim" />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="flex min-w-0 items-center gap-2 text-[12.5px] text-fg-dim">
              Agent
              <select value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label="Agent that builds it"
                className="h-9 min-w-0 max-w-[220px] rounded-md border border-line bg-bg px-2 text-[12.5px] text-fg md:h-8">
                {!agents.length && <option value="">No agents yet</option>}
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            <span className="flex-1" />
            <button type="submit" disabled={!idea.trim()}
              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-[13px] font-semibold text-[#14210a] disabled:opacity-50 md:h-8">
              <Sparkles size={14} aria-hidden />Build app
            </button>
          </div>
        </form>

        <h2 className="mt-8 font-mono text-[11px] uppercase tracking-wider text-fg-dim">Start from a template</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {TEMPLATES.map((t) => (
            <button key={t.title} type="button" onClick={() => setIdea(t.prompt)}
              className={cn('flex flex-col items-start gap-2 rounded-[12px] border bg-panel p-3.5 text-left transition-colors hover:border-fg-dim',
                idea === t.prompt ? 'border-accent-ink/60' : 'border-line')}>
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent-ink"><t.icon size={16} aria-hidden /></span>
              <span className="text-[13.5px] font-semibold">{t.title}</span>
              <span className="text-[12.5px] leading-snug text-fg-dim">{t.detail}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
