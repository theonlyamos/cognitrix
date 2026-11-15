// Live voice mode: the words that are spoken, and how spoken answers are read.
// Kept free of React and audio so it can be tested on its own.

export interface SpokenApproval {
  request_id: string;
  tool_name: string;
  params?: Record<string, unknown>;
  risk_level?: string;
  details?: string;
}

export interface SpokenQuestionOption {
  id: string;
  label: string;
}

export interface SpokenQuestion {
  request_id: string;
  prompt: string;
  details?: string | null;
  options: SpokenQuestionOption[];
  allow_free_text: boolean;
  /** Sign-in requests are typed, never spoken. */
  secret?: unknown;
}

export interface LiveVoicePrefs {
  /** Talking while a reply is read stops the reading and listens. */
  interrupt: boolean;
  /** How long a pause ends what you are saying, in milliseconds. */
  pauseMs: number;
}

export const LIVE_VOICE_DEFAULTS: LiveVoicePrefs = { interrupt: true, pauseMs: 1100 };
const PREFS_KEY = 'liveVoicePrefs';

export function getLiveVoicePrefs(): LiveVoicePrefs {
  try {
    const data = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    return {
      interrupt: typeof data.interrupt === 'boolean' ? data.interrupt : LIVE_VOICE_DEFAULTS.interrupt,
      pauseMs: typeof data.pauseMs === 'number' && data.pauseMs >= 500 && data.pauseMs <= 4000
        ? data.pauseMs
        : LIVE_VOICE_DEFAULTS.pauseMs,
    };
  } catch {
    return LIVE_VOICE_DEFAULTS;
  }
}

export function setLiveVoicePrefs(patch: Partial<LiveVoicePrefs>): LiveVoicePrefs {
  const next = { ...getLiveVoicePrefs(), ...patch };
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(next));
  } catch {
    /* storage blocked: the change lasts until reload */
  }
  return next;
}

/** Lower case, no punctuation, single spaces. */
export function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[’']/g, '')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const YES = [
  'yes', 'yeah', 'yep', 'yup', 'sure', 'ok', 'okay', 'go ahead', 'do it', 'approve', 'approved',
  'allow', 'allow it', 'run it', 'go for it', 'please do', 'confirm', 'confirmed', 'proceed',
  // French and Spanish
  'oui', 'daccord', 'vas y', 'si', 'sí', 'claro', 'adelante', 'aprobar', 'apruebo', 'permitir',
];
const NO = [
  'no', 'nope', 'nah', 'deny', 'denied', 'dont', 'do not', 'stop', 'cancel', 'reject', 'refuse',
  'block', 'dont do it', 'not now', 'skip', 'never',
  'non', 'refuser', 'annuler', 'negar', 'cancelar', 'rechazar',
];
const UNSURE = [
  'not sure', 'unsure', 'maybe', 'perhaps', 'i dont know', 'dont know', 'wait', 'hold on', 'hang on', 'hmm',
  'let me think', 'what', 'why', 'peut etre', 'quizás', 'quizas', 'tal vez', 'no sé', 'no se',
];
const NEGATORS = new Set(['no', 'not', 'nope', 'nah', 'dont', 'never', 'cant', 'wont', 'shouldnt', 'isnt', 'non', 'pas', 'ne', 'nunca']);
/** Words that count as a yes when the action is high risk: a plain "ok" is not enough. */
const STRONG_YES = ['approve', 'approved', 'allow', 'allow it', 'yes approve', 'yes allow', 'aprobar', 'apruebo', 'permitir'];

function hasPhrase(text: string, phrase: string): boolean {
  return ` ${text} `.includes(` ${phrase} `);
}

/**
 * Read a spoken answer to an approval. Anything mixed ("yes no") or unclear
 * comes back null so the person is asked again; a no always wins over a yes
 * in the same breath. High-risk actions need "approve" or "allow" said out loud.
 */
export function parseApprovalAnswer(spoken: string, risk = ''): 'yes' | 'no' | null {
  const text = normalize(spoken);
  if (!text) return null;
  const words = text.split(' ');
  // Long sentences are probably not an answer ("yes but first check the folder").
  if (words.length > 6) return null;
  // Doubt is never a yes: ask again.
  if (UNSURE.some((p) => hasPhrase(text, normalize(p)))) return null;
  // Any negation turns the answer into a no ("not approved", "I'm not saying yes").
  if (words.some((w) => NEGATORS.has(w)) || NO.some((p) => hasPhrase(text, normalize(p)))) return 'no';
  const yesWords = risk === 'high' ? STRONG_YES : YES;
  return yesWords.some((p) => hasPhrase(text, normalize(p))) ? 'yes' : null;
}

const STOP = ['stop', 'stop it', 'stop working', 'cancel', 'cancel that', 'never mind', 'nevermind', 'abort', 'arrête', 'para', 'detente'];
const EXIT = [
  'end voice mode', 'exit voice mode', 'stop voice mode', 'close voice mode', 'leave voice mode',
  'turn off voice mode', 'end live voice', 'goodbye', 'bye', 'stop listening',
];

/** A short "stop" while the agent works stops the run; anything longer is a message. */
export function isStopCommand(spoken: string): boolean {
  const text = normalize(spoken);
  return STOP.some((p) => text === normalize(p) || text === `${normalize(p)} please` || text === `please ${normalize(p)}`);
}

export function isExitCommand(spoken: string): boolean {
  const text = normalize(spoken);
  return EXIT.some((p) => text === p || text === `${p} please` || text === `ok ${p}` || text === `okay ${p}`);
}

const ORDINALS: Record<string, number> = {
  one: 1, first: 1, '1': 1, two: 2, second: 2, '2': 2, three: 3, third: 3, '3': 3,
  four: 4, fourth: 4, '4': 4, five: 5, fifth: 5, '5': 5, six: 6, sixth: 6, '6': 6,
};

/**
 * Match a spoken answer to one of the options: by its words ("the blue one"
 * for "Blue"), or by number ("option two", "the second one"). Null when no
 * single option fits.
 */
export function matchOption(spoken: string, options: SpokenQuestionOption[]): string | null {
  const text = normalize(spoken);
  if (!text || options.length === 0) return null;
  const exact = options.filter((o) => normalize(o.label) === text);
  if (exact.length === 1) return exact[0].id;
  const contained = options.filter((o) => {
    const label = normalize(o.label);
    return label.length > 1 && hasPhrase(text, label);
  });
  if (contained.length === 1) return contained[0].id;
  if (contained.length > 1) {
    // "Blue green" contains "blue" and "blue green": take the longest when it holds the others.
    const longest = contained.reduce((a, b) => (normalize(b.label).length > normalize(a.label).length ? b : a));
    const longLabel = normalize(longest.label);
    if (contained.every((o) => longLabel.includes(normalize(o.label)))) return longest.id;
    return null;
  }
  const words = text.split(' ');
  if (words.length <= 4) {
    const numbers = words.map((w) => ORDINALS[w]).filter((n): n is number => typeof n === 'number');
    const unique = Array.from(new Set(numbers));
    if (unique.length === 1 && unique[0] <= options.length) return options[unique[0] - 1].id;
  }
  return null;
}

function humanTool(name: string): string {
  return name.replace(/^mcp__/, '').replace(/__/g, ' ').replace(/[_-]+/g, ' ').trim();
}

function shorten(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  if (flat.length <= limit) return flat;
  const cut = flat.slice(0, limit);
  const space = cut.lastIndexOf(' ');
  return `${cut.slice(0, space > limit * 0.6 ? space : limit)}, and more`;
}

/** What is said when the agent asks to run something. */
export function approvalSpeech(req: SpokenApproval, agentName = ''): string {
  const who = agentName.trim() || 'The agent';
  const parts = [`${who} wants to run ${humanTool(req.tool_name)}.`];
  const command = typeof req.params?.command === 'string' ? req.params.command : '';
  if (command) parts.push(`The command is: ${shorten(command, 140)}.`);
  else if (req.details) parts.push(`${shorten(req.details, 160)}.`);
  parts.push(req.risk_level === 'high'
    ? 'This one is high risk. Say approve to allow it, or no to deny it.'
    : 'Say yes to allow it, or no to deny it.');
  return parts.join(' ').replace(/\.\./g, '.');
}

export function approvalRetrySpeech(req: SpokenApproval): string {
  return req.risk_level === 'high'
    ? 'Sorry, I need a clear answer. Say approve, or say no.'
    : 'Sorry, I didn’t catch that. Say yes or no.';
}

/** What is said when the agent asks a question. */
export function questionSpeech(q: SpokenQuestion): string {
  if (q.secret) return `${q.prompt} This needs typing, so I’ve left it on screen.`;
  const parts = [q.prompt.trim().replace(/[.?!]?$/, (m) => m || '.')];
  if (q.options.length > 0) {
    const labels = q.options.map((o, i) => `${i + 1}, ${o.label}`);
    parts.push(`The options are: ${labels.join('; ')}.`);
    parts.push(q.allow_free_text ? 'Say one of them, or answer in your own words.' : 'Say the one you want.');
  }
  return parts.join(' ');
}

/**
 * The answer to send for a spoken reply to a question, or null to ask again.
 * Free-text questions take whatever was said when no option fits.
 */
export function questionAnswer(spoken: string, q: SpokenQuestion): { option_id: string } | { text: string } | null {
  if (q.secret) return null;
  const option = matchOption(spoken, q.options);
  if (option) return { option_id: option };
  if (q.allow_free_text || q.options.length === 0) {
    const text = spoken.trim();
    return text ? { text } : null;
  }
  return null;
}
