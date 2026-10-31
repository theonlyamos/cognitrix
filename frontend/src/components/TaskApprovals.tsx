import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';

export interface TaskApproval {
  id: string;
  run_id: string;
  step_index: number | null;
  tool_name: string;
  params: Record<string, unknown>;
  risk_level: string;
  details: string;
  status: string;
  can_decide: boolean;
}

interface TaskApprovalsProps {
  taskId: string;
  runId: string;
  /** Poll only while the run can still be waiting on an answer. */
  live: boolean;
}

const POLL_MS = 3000;

function preview(params: Record<string, unknown>): string {
  const text = JSON.stringify(params, null, 2);
  return text.length > 1200 ? `${text.slice(0, 1200)}\n…` : text;
}

/** Risky tool calls a running task has paused on, with Approve / Deny. */
export function TaskApprovals({ taskId, runId, live }: TaskApprovalsProps) {
  const [pending, setPending] = useState<TaskApproval[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const base = `/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(runId)}/approvals`;

  const load = useCallback(async () => {
    try {
      const res = await api.get<TaskApproval[]>(base, { params: { status: 'pending' } });
      setPending(Array.isArray(res.data) ? res.data : []);
    } catch {
      // Transient; the next poll retries.
    }
  }, [base]);

  useEffect(() => {
    setPending([]);
    setError(null);
    void load();
  }, [load]);

  // Own timer (not usePolling) so the page's single polling hook stays the
  // page's: this card polls only while its run can still be waiting.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [live, load]);

  const decide = async (approval: TaskApproval, approved: boolean) => {
    setBusy(approval.id);
    setError(null);
    try {
      await api.post(`${base}/${encodeURIComponent(approval.id)}`, { approved });
      setPending((rows) => rows.filter((row) => row.id !== approval.id));
    } catch (err) {
      setError(errorMessage(err, 'Could not send your answer. Try again.'));
      void load();
    } finally {
      setBusy(null);
    }
  };

  if (pending.length === 0) return null;

  return (
    <div className="mx-6 mt-3 space-y-2" aria-live="polite">
      {pending.map((approval) => (
        <section
          key={approval.id}
          role="group"
          aria-label={`Approval needed: ${approval.tool_name}`}
          className="rounded border border-accent/60 bg-accent/5 px-3 py-2.5"
        >
          <p className="font-mono text-[12px] font-semibold text-fg">
            Waiting for approval: {approval.tool_name}
            {approval.step_index != null && (
              <span className="font-normal text-fg-dim"> · step {approval.step_index + 1}</span>
            )}
            {approval.risk_level && (
              <span className="font-normal text-fg-dim"> · {approval.risk_level} risk</span>
            )}
          </p>
          {approval.details && (
            <p className="mt-1 text-[12px] text-fg-dim">{approval.details}</p>
          )}
          <pre className="mt-2 max-h-48 overflow-auto rounded bg-panel-2 p-2 font-mono text-[11px] text-fg">
            {preview(approval.params)}
          </pre>
          {approval.can_decide ? (
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                disabled={busy === approval.id}
                onClick={() => void decide(approval, true)}
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy === approval.id}
                onClick={() => void decide(approval, false)}
              >
                Deny
              </Button>
            </div>
          ) : (
            <p className="mt-2 text-[12px] text-fg-dim">Only the person who started this run can answer.</p>
          )}
        </section>
      ))}
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
    </div>
  );
}
