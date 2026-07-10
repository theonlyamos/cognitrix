import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { CheckList } from '@/components/form';

export interface TaskRecord {
  id: string;
  title: string;
  status?: string;
  assigned_agents?: string[];
  team_id?: string | null;
  [key: string]: unknown;
}

export function TaskAssignmentPanel({
  mode,
  entityId,
  memberIds = [],
  tasks,
  onAssigned,
}: {
  mode: 'agent' | 'team';
  entityId: string;
  memberIds?: string[];
  tasks: TaskRecord[];
  onAssigned: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const panelId = useId();
  const labelId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef(false);

  const eligible = useMemo(
    () => tasks.filter((task) => (
      mode === 'agent'
        ? !(task.assigned_agents || []).includes(entityId)
        : task.team_id !== entityId
    )),
    [entityId, mode, tasks],
  );

  useEffect(() => {
    if (open) {
      panelRef.current?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus();
    } else if (!saving && restoreFocusRef.current) {
      restoreFocusRef.current = false;
      triggerRef.current?.focus();
    }
  }, [open, saving]);

  const toggle = (id: string) => {
    if (saving) return;
    setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
    });
  };

  const close = () => {
    if (saving) return;
    setOpen(false);
    setSelected(new Set());
    setError('');
    restoreFocusRef.current = true;
  };

  const assign = async () => {
    if (selected.size === 0 || saving) return;
    setSaving(true);
    setError('');
    try {
      const chosen = eligible.filter((task) => selected.has(task.id));
      const results = await Promise.allSettled(chosen.map((task) => {
        const assignment = mode === 'agent'
          ? { assigned_agents: [...new Set([...(task.assigned_agents || []), entityId])], team_id: task.team_id || null }
          : { team_id: entityId, assigned_agents: [...memberIds] };
        return api.patch(`/tasks/${task.id}/assignment`, assignment);
      }));
      const failed = new Set(
        chosen.filter((_, index) => results[index].status === 'rejected').map((task) => task.id),
      );
      const succeeded = chosen.length - failed.size;

      if (succeeded > 0) await onAssigned();
      if (failed.size > 0) {
        setSelected(failed);
        if (succeeded === 0) {
          const firstFailure = results.find(
            (result): result is PromiseRejectedResult => result.status === 'rejected',
          );
          setError(errorMessage(firstFailure?.reason, 'Could not assign the selected tasks.'));
        } else {
          setError(`Could not assign ${failed.size} of ${chosen.length} selected tasks.`);
        }
      } else {
        setSelected(new Set());
        setOpen(false);
        restoreFocusRef.current = true;
      }
    } catch (assignError) {
      setError(errorMessage(assignError, 'Could not assign the selected tasks.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Button
        ref={triggerRef}
        variant="outline"
        size="sm"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => (open ? close() : setOpen(true))}
        disabled={saving}
      >
        Assign task
      </Button>
      {open && (
        <div ref={panelRef} id={panelId} className="mt-3 space-y-3 rounded border border-line bg-panel p-3">
          <p id={labelId} className="font-mono text-[11px] tracking-[0.12em] text-fg-dim">Tasks available to assign</p>
          <CheckList
            options={eligible.map((task) => ({ value: task.id, label: task.title, sub: task.status || 'pending' }))}
            selected={selected}
            onToggle={toggle}
            disabled={saving}
            aria-labelledby={labelId}
            empty={`no tasks available for this ${mode}`}
          />
          {error && <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[11px] text-danger-ink">{error}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={close} disabled={saving}>Cancel</Button>
            <Button size="sm" onClick={assign} disabled={saving || selected.size === 0}>
              {saving ? 'Assigning…' : 'Assign selected'}
            </Button>
            {saving && <span role="status" className="sr-only">Assigning selected tasks…</span>}
          </div>
        </div>
      )}
    </>
  );
}
