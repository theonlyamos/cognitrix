// One look for every control in the chat box's bottom row: borderless, the
// same height, icon + short label. Tinted variants mark risky settings.
export const composerBtn =
  'inline-flex h-11 min-w-0 flex-none items-center gap-1.5 whitespace-nowrap rounded-lg px-1.5 text-[13px] font-medium text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg disabled:opacity-60 disabled:hover:bg-transparent md:h-8 md:px-2';
export const composerIconBtn =
  'inline-grid h-11 w-11 flex-none place-items-center rounded-lg text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg disabled:opacity-60 md:h-8 md:w-8';
export const composerDanger = 'bg-danger/10 text-danger-ink hover:bg-danger/20 hover:text-danger-ink';
export const composerWarn = 'bg-warn-ink/10 text-warn-ink hover:bg-warn-ink/20 hover:text-warn-ink';
/** Popover that opens above the row on desktop and as a bottom sheet on phones. */
export const composerPopover =
  'animate-rise fixed inset-x-0 bottom-0 z-50 max-h-[75vh] overflow-y-auto rounded-t-2xl border-t border-line bg-panel px-2.5 pb-6 pt-2 shadow-lg md:absolute md:inset-x-auto md:bottom-full md:left-0 md:mb-2 md:max-h-[min(60vh,440px)] md:rounded-xl md:border md:p-1.5';
