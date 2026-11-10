// Model providers offered in the agent editor and the first-run assistant
// setup. Keep PROVIDERS in step with SETUP_PROVIDERS in
// cognitrix/api/routes/onboarding.py, and BASE_URLS with the backend's
// _DEFAULT_BASE_URLS in cognitrix/providers/base.py.
export const PROVIDERS = ['openrouter', 'openai', 'chatgpt', 'anthropic', 'google', 'groq', 'cerebras', 'ollama'];

/** Coding agent CLIs an agent can run on, signed in on this computer with the
 *  person's own plan or key (cognitrix/providers/harness.py HARNESSES). */
export const HARNESS_PROVIDERS = ['claude-code', 'codex', 'gemini-cli', 'copilot-cli', 'opencode'];

export function isHarness(provider: string): boolean {
  return HARNESS_PROVIDERS.includes(provider);
}

/** How to install and sign in to each CLI (keep in step with HARNESSES). */
export const HARNESS_INSTALL: Record<string, string> = {
  'claude-code': 'npm install -g @anthropic-ai/claude-code, then run `claude` once and sign in',
  codex: 'npm install -g @openai/codex, then run `codex login` (Sign in with ChatGPT)',
  'gemini-cli': 'npm install -g @google/gemini-cli, then run `gemini` once and sign in',
  'copilot-cli': 'npm install -g @github/copilot, then run `copilot login` (needs a Copilot plan)',
  opencode: 'npm install -g opencode-ai, then run `opencode auth login`',
};

/** What a CLI agent may do to the files in its working folder. */
export const HARNESS_MODES = [
  { value: 'read_only', label: 'Read only', detail: 'Reads files and answers. Changes nothing.' },
  {
    value: 'ask', label: 'Ask first',
    detail: 'Changes files in the working folder, and shows you an Approve/Deny card before it runs a command or touches anything outside it.',
  },
  { value: 'edit', label: 'Edit files', detail: 'Can change files in the working folder.' },
  { value: 'full', label: 'Full access', detail: 'Can also run any command without asking. Use with care.' },
];

/** CLIs that can send their permission prompts to Cognitrix (ASK_HARNESSES in harness.py). */
export const ASK_HARNESSES = ['claude-code'];

/** Providers that run on a signed-in plan instead of an API key
 *  (Settings › Models and keys › Use your ChatGPT plan). */
export const PLAN_PROVIDERS = new Set(['chatgpt']);

export const PROVIDER_LABELS: Record<string, string> = {
  openrouter: 'OpenRouter',
  openai: 'OpenAI',
  chatgpt: 'ChatGPT plan',
  anthropic: 'Anthropic',
  google: 'Google Gemini',
  groq: 'Groq',
  cerebras: 'Cerebras',
  ollama: 'Ollama (local)',
  'claude-code': 'Claude Code',
  codex: 'Codex',
  'gemini-cli': 'Gemini CLI',
  'copilot-cli': 'GitHub Copilot CLI',
  opencode: 'OpenCode',
};

// Recent models per provider, newest and most useful first. The first one is
// the default for a new agent. The model pickers also add whatever the
// provider lists for your key (GET /providers/{id}/models), and "Custom…"
// takes any other name. Keep the first entry in step with
// Settings.get_default_model in cognitrix/config.py.
export const MODELS: Record<string, string[]> = {
  openrouter: [
    'google/gemini-3.6-flash', 'anthropic/claude-sonnet-5.5', 'openai/gpt-6.1-sol',
    'anthropic/claude-opus-5.5', 'anthropic/claude-fable-5.2', 'google/gemini-3.8-flash',
    'google/gemini-4-carbon', 'openai/gpt-6.1-terra', 'openai/gpt-5.5', 'openai/gpt-oss-120b',
  ],
  openai: [
    'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
    'gpt-5.5', 'gpt-5.5-pro', 'gpt-5.4-mini',
  ],
  // The live list (your plan's models) is added by the model picker.
  chatgpt: ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-sol', 'gpt-5.5'],
  anthropic: [
    'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-2', 'claude-haiku-5-5', 'claude-fable-5-1',
    'claude-opus-5', 'claude-sonnet-5', 'claude-opus-4-8', 'claude-sonnet-4-6',
  ],
  google: [
    'gemini-3.6-flash', 'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-4-carbon', 'gemini-4-argon',
    'gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-pro-preview',
  ],
  groq: ['openai/gpt-oss-120b', 'openai/gpt-oss-20b'],
  cerebras: ['gpt-oss-120b', 'zai-glm-4.7'],
  ollama: ['llama3.2', 'gpt-oss:20b', 'qwen3', 'mistral'],
  // Coding agent CLIs: an empty model uses the CLI's own default.
  'claude-code': ['sonnet', 'opus', 'haiku', 'claude-fable-5-2'],
  codex: ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-sol', 'gpt-5.5'],
  'gemini-cli': ['gemini-3.6-flash', 'gemini-3.8-flash', 'gemini-4-carbon', 'gemini-3.5-flash'],
  'copilot-cli': ['auto', 'claude-sonnet-5-5', 'gpt-6.1-sol'],
  opencode: ['openai/gpt-6.1-sol', 'anthropic/claude-sonnet-5-5', 'github-copilot/gpt-5.5'],
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
  chatgpt: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  google: 'https://generativelanguage.googleapis.com/v1beta/openai/v1',
  groq: 'https://api.groq.com/openai/v1',
  cerebras: 'https://api.cerebras.ai/v1',
  ollama: 'http://localhost:11434/v1',
};

// Claude Opus 4.7 and every later Claude model reject `temperature`; the
// backend drops it for them (_anthropic_rejects_temperature in base.py).
export function ignoresTemperature(provider: string, model: string): boolean {
  // The ChatGPT plan route and the coding agent CLIs take no temperature.
  if (provider === 'chatgpt' || HARNESS_PROVIDERS.includes(provider)) return true;
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
