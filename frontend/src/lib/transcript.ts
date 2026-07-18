import type { ChatMessage, ToolArtifact } from '@/context/SessionContext';

/** Raw session.chat entry as the backend persists it. Roles are inconsistent
 *  in case ('User' vs 'user') and tool results carry no `type` — parse, don't
 *  pattern-match, anywhere else. */
export interface BackendChatEntry {
  role?: string;
  name?: string;
  type?: string;
  content?: unknown;
  tool_calls?: { name?: string; arguments?: unknown; tool_call_id?: string | null }[];
  tool_call_id?: string | null;
  outcome?: { status?: unknown; artifacts?: unknown };
  artifact?: unknown;
  duration?: number;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
}

export type TranscriptTool = {
  id?: string;
  name: string;
  args: string;
  result?: string;
  status?: 'running' | 'done' | 'error' | 'stopped';
  artifacts?: ToolArtifact[];
};

export type TranscriptEntry =
  | { kind: 'user'; content: string }
  | { kind: 'assistant'; content: string; name?: string; live?: boolean }
  | { kind: 'tool_calls'; content: string; name?: string; tools: TranscriptTool[] }
  | { kind: 'tool_result'; content: string }
  | { kind: 'image'; artifact: ToolArtifact }
  | { kind: 'timing'; label: string; tokens?: string }
  | { kind: 'summary'; content: string }
  | { kind: 'system'; content: string };

const asText = (v: unknown): string =>
  typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v);

const safeArgs = (v: unknown): string => {
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v ?? {});
  } catch {
    return String(v);
  }
};

const safeArtifacts = (value: unknown): ToolArtifact[] | undefined => {
  if (!Array.isArray(value)) return undefined;
  const artifacts = value.filter((item): item is ToolArtifact => {
    if (!item || typeof item !== 'object') return false;
    const candidate = item as Record<string, unknown>;
    return typeof candidate.id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(candidate.id)
      && typeof candidate.mime_type === 'string' && candidate.mime_type.length > 0
      && (candidate.origin === undefined || candidate.origin === 'uploaded' || candidate.origin === 'generated')
      && (candidate.filename === undefined || typeof candidate.filename === 'string')
      && (candidate.width === undefined || typeof candidate.width === 'number')
      && (candidate.height === undefined || typeof candidate.height === 'number');
  });
  return artifacts.length ? artifacts : undefined;
};

/** Map raw backend chat entries to render-ready transcript entries. Unknown
 *  shapes fall back to `system` so future entry types degrade gracefully. */
export function parseChatEntries(chat: BackendChatEntry[] | null | undefined): TranscriptEntry[] {
  const out: TranscriptEntry[] = [];
  // Tool results are separate `role: 'tool'` messages; index them by
  // tool_call_id so each tool call can carry its own result for display.
  const resultsById: Record<string, { content: string; status: 'done' | 'error' | 'stopped'; artifacts?: ToolArtifact[] }> = {};
  for (const m of chat || []) {
    if (String(m.role || '').toLowerCase() === 'tool' && m.tool_call_id) {
      const outcomeStatus = m.outcome?.status;
      resultsById[m.tool_call_id] = {
        content: asText(m.content),
        status: outcomeStatus === 'stopped'
          ? 'stopped'
          : outcomeStatus === 'success' || !m.outcome
            ? 'done'
            : 'error',
        artifacts: safeArtifacts(m.outcome?.artifacts),
      };
    }
  }
  const pairedResultIds = new Set<string>();
  for (const m of chat || []) {
    if (
      String(m.role || '').toLowerCase() !== 'assistant'
      || m.type !== 'tool_calls'
    ) continue;
    for (const tool of m.tool_calls || []) {
      if (
        tool.tool_call_id
        && Object.prototype.hasOwnProperty.call(resultsById, tool.tool_call_id)
      ) {
        pairedResultIds.add(tool.tool_call_id);
      }
    }
  }
  for (const m of chat || []) {
    const role = String(m.role || '').toLowerCase();
    const type = m.type || '';
    const content = asText(m.content);

    if (role === 'user' && type === 'summary') {
      out.push({ kind: 'summary', content });
    } else if (role === 'user' && type === 'image') {
      const artifact = safeArtifacts([m.artifact])?.[0];
      if (artifact?.mime_type.startsWith('image/')) {
        out.push({ kind: 'image', artifact: { ...artifact, origin: artifact.origin || 'uploaded' } });
      }
    } else if (role === 'user' && type === 'text') {
      if (content.trim()) out.push({ kind: 'user', content });
    } else if (role === 'assistant' && type === 'tool_calls') {
      out.push({
        kind: 'tool_calls',
        content,
        name: m.name || undefined,
        tools: (m.tool_calls || []).map((tool) => {
          const result = tool.tool_call_id
            ? resultsById[tool.tool_call_id]
            : undefined;
          return {
            id: tool.tool_call_id || undefined,
            name: tool.name || 'tool',
            args: safeArgs(tool.arguments),
            result: result?.content,
            status: result?.status,
            artifacts: result?.artifacts,
          };
        }),
      });
    } else if (role === 'assistant') {
      if (content.trim()) out.push({ kind: 'assistant', content, name: m.name || undefined });
    } else if (role === 'tool') {
      if (!m.tool_call_id || !pairedResultIds.has(m.tool_call_id)) {
        out.push({ kind: 'tool_result', content });
      }
    } else if (role === 'system' && type === 'turn_timing') {
      const tokens =
        m.prompt_tokens || m.completion_tokens
          ? `${(m.prompt_tokens ?? 0).toLocaleString()} → ${(m.completion_tokens ?? 0).toLocaleString()} tok`
          : undefined;
      out.push({ kind: 'timing', label: content || 'turn', tokens });
    } else if (content.trim()) {
      out.push({ kind: 'system', content });
    }
  }
  return out;
}

/** Reduce a transcript to the plain chat bubbles Home renders (drops tool
 *  chatter and timing rows so a restored thread looks like the live one). */
export function toChatMessages(entries: TranscriptEntry[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const e of entries) {
    if (e.kind === 'user') out.push({ id: `hist-${out.length}`, role: 'user', content: e.content });
    else if (e.kind === 'image') {
      const last = out[out.length - 1];
      if (last?.role === 'user') {
        last.artifacts = [...(last.artifacts || []), e.artifact];
      } else {
        out.push({ id: `hist-${out.length}`, role: 'user', content: '', artifacts: [e.artifact] });
      }
    }
    else if (e.kind === 'assistant') out.push({ id: `hist-${out.length}`, role: 'assistant', content: e.content });
    else if (e.kind === 'tool_calls') {
      // The assistant's preamble (if any) first, then the tools it invoked as a
      // chip row — matches how the live stream renders a tool round.
      if (e.content.trim()) out.push({ id: `hist-${out.length}`, role: 'assistant', content: e.content });
      if (e.tools.length)
        out.push({
          id: `hist-${out.length}`,
          role: 'tool',
          content: '',
          tools: e.tools.map((t) => ({
            name: t.name,
            status: t.status ?? 'error',
            params: t.args,
            result: t.result,
            artifacts: t.artifacts,
          })),
        });
    }
  }
  return out;
}

/** Session timestamps come in two shapes: "Tue Jul 03 2026 14:30:45" (the
 *  model's strftime default) and ISO ("2026-07-03T20:08:54"). */
export function parseSessionDate(s: string | null | undefined): Date | null {
  if (!s) return null;
  let d = new Date(s);
  if (isNaN(d.getTime())) d = new Date(s.replace(' ', 'T'));
  return isNaN(d.getTime()) ? null : d;
}

export function fmtDuration(startedAt?: string | null, completedAt?: string | null): string {
  const a = parseSessionDate(startedAt);
  const b = parseSessionDate(completedAt);
  if (!a || !b) return '';
  const secs = Math.max(0, Math.round((b.getTime() - a.getTime()) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`;
}

export function fmtRelative(s: string | null | undefined): string {
  const d = parseSessionDate(s);
  if (!d) return '';
  const secs = Math.round((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d ago`;
  return d.toLocaleDateString();
}
