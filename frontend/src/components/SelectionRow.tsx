import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function SelectionRow({
  selected,
  onSelect,
  disabled,
  trailingAction,
  className,
  buttonClassName,
  children,
}: {
  selected: boolean;
  onSelect: () => void;
  disabled?: boolean;
  trailingAction?: ReactNode;
  className?: string;
  buttonClassName?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('flex items-center', className)}>
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        disabled={disabled}
        className={cn('min-h-11 min-w-0 flex-1 cursor-pointer text-left disabled:cursor-not-allowed', buttonClassName)}
      >
        {children}
      </button>
      {trailingAction}
    </div>
  );
}
