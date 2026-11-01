// Model providers offered in the agent editor and the first-run assistant
// setup. Keep PROVIDERS in step with SETUP_PROVIDERS in
// cognitrix/api/routes/onboarding.py, and BASE_URLS with the backend's
// _DEFAULT_BASE_URLS in cognitrix/providers/base.py.
export const PROVIDERS = ['openrouter', 'openai', 'anthropic', 'google', 'groq', 'cerebras', 'ollama'];

export const PROVIDER_LABELS: Record<string, string> = {
  openrouter: 'OpenRouter',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google Gemini',
  groq: 'Groq',
  cerebras: 'Cerebras',
  ollama: 'Ollama (local)',
};

export const MODELS: Record<string, string[]> = {
  openrouter: ['google/gemini-3.5-flash', 'openai/gpt-5.5', 'anthropic/claude-sonnet-5.5'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'o1-mini'],
  anthropic: [
    'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5', 'claude-fable-5-1',
    'claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5',
  ],
  google: ['gemini-3.5-flash', 'gemini-3.1-pro', 'gemini-3.1-flash'],
  groq: ['openai/gpt-oss-120b', 'openai/gpt-oss-20b', 'qwen/qwen3.8-27b'],
  cerebras: ['llama-3.3-70b', 'llama3.1-8b'],
  ollama: ['llama3.2', 'qwen2.5', 'mistral'],
};

// Default OpenAI-compatible endpoint per provider.
export const BASE_URLS: Record<string, string> = {
  openrouter: 'https://openrouter.ai/api/v1',
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  google: 'https://generativelanguage.googleapis.com/v1beta/openai/v1',
  groq: 'https://api.groq.com/openai/v1',
  cerebras: 'https://api.cerebras.ai/v1',
  ollama: 'http://localhost:11434/v1',
};

// Claude Opus 4.7 and every later Claude model reject `temperature`; the
// backend drops it for them (_anthropic_rejects_temperature in base.py).
export function ignoresTemperature(provider: string, model: string): boolean {
  if (provider !== 'anthropic') return false;
  const m = /^claude-(?:opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?!\d)/.exec(
    model.trim().toLowerCase().split('/').pop() || '',
  );
  if (!m) return false;
  const major = Number(m[1]);
  const minor = Number(m[2] || 0);
  return major > 4 || (major === 4 && minor >= 7);
}

/** Fired on window after an agent is created outside the agent pages (the
 *  first-run setup), so open lists refresh. `detail` is the new agent's id. */
export const AGENTS_CHANGED_EVENT = 'cognitrix:agents-changed';
