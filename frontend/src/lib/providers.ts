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

// Recent models per provider, newest and most useful first. The first one is
// the default for a new agent. The model pickers also add whatever the
// provider lists for your key (GET /providers/{id}/models), and "Custom…"
// takes any other name. Keep the first entry in step with
// Settings.get_default_model in cognitrix/config.py.
export const MODELS: Record<string, string[]> = {
  openrouter: [
    'google/gemini-3.6-flash', 'anthropic/claude-sonnet-5.5', 'openai/gpt-5.5',
    'anthropic/claude-opus-5.5', 'openai/gpt-oss-120b',
  ],
  openai: ['gpt-5.5', 'gpt-5.5-pro', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano'],
  anthropic: [
    'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5', 'claude-fable-5-1',
    'claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5',
  ],
  google: ['gemini-3.6-flash', 'gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-pro'],
  groq: ['openai/gpt-oss-120b', 'openai/gpt-oss-20b'],
  cerebras: ['gpt-oss-120b', 'zai-glm-4.7'],
  ollama: ['llama3.2', 'gpt-oss:20b', 'qwen3', 'mistral'],
};

// Where to create an API key for each provider.
export const KEY_URLS: Record<string, string> = {
  openrouter: 'https://openrouter.ai/keys',
  openai: 'https://platform.openai.com/api-keys',
  anthropic: 'https://console.anthropic.com/settings/keys',
  google: 'https://aistudio.google.com/apikey',
  groq: 'https://console.groq.com/keys',
  cerebras: 'https://cloud.cerebras.ai/',
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
