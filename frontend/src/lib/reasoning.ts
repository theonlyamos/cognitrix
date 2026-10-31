/** Reasoning levels sent as the OpenAI-compatible `reasoning_effort` param.
 *  '' means "don't send it": the agent's setting, or the provider's default. */
export const REASONING_LEVELS: { value: string; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'minimal', label: 'Minimal' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra high' },
];

export function reasoningLabel(value: string | undefined | null): string {
  return REASONING_LEVELS.find((l) => l.value === value)?.label ?? 'Default';
}
