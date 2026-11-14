/**
 * Explanations for the engine's safety refusals, shown where they happen:
 * a shell command that didn't run (outside the working folder, or nobody was
 * there to approve it), and a provider key that wasn't sent to an untrusted
 * base URL.
 */

const REFUSAL_PATTERNS = [
  /Command refused/i,
  /Needs approval - /i,
  /outside the working (folder|directory)/i,
  /resolves outside the permitted root/i,
];

const OUTSIDE_FOLDER = /outside the (working|permitted)|left the working folder|protected location/i;
const NEEDS_APPROVAL = /Needs approval - /i;
const ALLOWLIST_ONLY = /COGNITRIX_SHELL_MODE=allowlist/i;

const BLOCKED_ENDPOINT = /base_url '([^']+)' is not a trusted endpoint/i;

/** True when a tool result is the engine refusing to run a command. */
export function isRefusal(result?: string | null): boolean {
  return !!result && REFUSAL_PATTERNS.some((pattern) => pattern.test(result));
}

/** The blocked base URL if this text is the "untrusted endpoint" error. */
export function blockedEndpoint(text?: string | null): string | null {
  const match = text ? BLOCKED_ENDPOINT.exec(text) : null;
  return match ? match[1] : null;
}

/** Why a refused command didn't run, in words for the person. */
export function refusalKind(result?: string | null): 'folder' | 'approval' | 'allowlist' | 'other' {
  const text = result || '';
  if (OUTSIDE_FOLDER.test(text)) return 'folder';
  if (ALLOWLIST_ONLY.test(text)) return 'allowlist';
  if (NEEDS_APPROVAL.test(text)) return 'approval';
  return 'other';
}

export function RefusedNotice({ result }: { result?: string | null }) {
  const kind = refusalKind(result);
  return (
    <div role="note" className="w-full max-w-2xl rounded border border-warn/40 bg-warn/10 px-3 py-2 text-[12.5px] leading-relaxed">
      <span className="font-mono text-[10.5px] tracking-[0.1em] text-warn-ink">{kind === 'approval' ? 'NOT RUN' : 'REFUSED'}</span>{' '}
      {kind === 'folder' && (
        <>
          This path is outside the agent's working folder, so the command didn't run. The agent was told why and can carry on without it.{' '}
          <a href="/settings/access" className="text-accent-ink underline-offset-2 hover:underline">Change working folder</a>
        </>
      )}
      {kind === 'approval' && (
        <>This command needs your approval to run, and nobody was there to ask, so it didn't run. The agent was told why and can ask you to run it.</>
      )}
      {kind === 'allowlist' && (
        <>This command isn't on the allowlist, and the full shell is turned off on this computer, so it didn't run. The agent was told why.</>
      )}
      {kind === 'other' && (
        <>The command didn't run. The agent was told why and can carry on without it.</>
      )}
    </div>
  );
}

export function BlockedEndpointNotice({ url }: { url: string }) {
  return (
    <div role="alert" className="mb-2 rounded border border-danger/40 bg-danger/5 px-3 py-2.5 text-[13px] leading-relaxed">
      <strong className="font-semibold text-danger-ink">This address is blocked.</strong>{' '}
      <span className="font-mono text-[12px]">{url}</span> isn't the provider's own endpoint, so Cognitrix didn't send your stored key to it.
      Use the provider's address, an endpoint that doesn't need the stored key (like a local Ollama), or ask whoever runs the engine to add
      it to <span className="font-mono text-[12px]">COGNITRIX_TRUSTED_BASE_URLS</span>.
    </div>
  );
}

const SIGN_IN_NEEDED = /App sign-in needed: ([^.\n]+)\./;

/** The app's name when a tool result says the person has to (re)connect it. */
export function signInNeeded(result?: string | null): string | null {
  const match = result ? SIGN_IN_NEEDED.exec(result) : null;
  return match ? match[1].trim() : null;
}

export function SignInNeededNotice({ app }: { app: string }) {
  return (
    <div role="note" className="flex w-full max-w-2xl flex-wrap items-center gap-x-3 gap-y-2 rounded border border-warn/40 bg-warn/10 px-3 py-2.5 text-[12.5px] leading-relaxed">
      <span className="min-w-0 flex-1 basis-[220px]">
        <span className="font-mono text-[10.5px] tracking-[0.1em] text-warn-ink">SIGN-IN NEEDED</span>{' '}
        The agent can't use {app} until you connect it again. Ask it to try again afterwards.
      </span>
      <a href="/settings/apps" className="inline-flex h-8 items-center rounded-lg bg-accent px-3 text-[12.5px] font-medium text-accent-foreground">
        Reconnect {app}
      </a>
    </div>
  );
}
