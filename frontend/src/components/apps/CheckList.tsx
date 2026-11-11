import type { CheckResult } from '@/lib/apps';
import { cn } from '@/lib/utils';

/** The lines of a "test connection" result: what passed, what failed and why. */
export function CheckList({ result, label }: { result: CheckResult; label: string }) {
  return (
    <ul role="status" aria-label={label} className="mt-2 space-y-1 rounded-[10px] border border-line bg-bg px-3 py-2">
      {result.checks.map((check) => (
        <li key={check.label} className="flex items-start gap-2 text-[12px] leading-snug">
          <span
            aria-hidden="true"
            className={cn('mt-px w-3 flex-none font-mono', check.ok === true ? 'text-ok' : check.ok === false ? 'text-danger-ink' : 'text-fg-dim')}
          >
            {check.ok === true ? '✓' : check.ok === false ? '✕' : '?'}
          </span>
          <span className="min-w-0">
            <span className="sr-only">{check.ok === true ? 'Passed: ' : check.ok === false ? 'Failed: ' : 'Unknown: '}</span>
            {check.label}
            {check.detail && <span className="break-words text-fg-dim"> · {check.detail}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}
