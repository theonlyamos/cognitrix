/**
 * Explanations for the engine's safety refusals, shown where they happen:
 * a shell command that reached outside the working folder, and a provider key
 * that wasn't sent to an untrusted base URL.
 */

const REFUSAL_PATTERNS = [
  /Command refused/i,
  /outside the working (folder|directory)/i,
  /resolves outside the permitted root/i,
];

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

export function RefusedNotice() {
  return (
    <div role="note" className="w-full max-w-2xl rounded border border-[#ffb86b]/40 bg-[#ffb86b]/10 px-3 py-2 text-[12.5px] leading-relaxed">
      <span className="font-mono text-[10.5px] tracking-[0.1em] text-[#b4691a] dark:text-[#ffb86b]">REFUSED</span>{' '}
      This path is outside the agents' working folder, so the command didn't run. The agent was told why and can carry on without it.{' '}
      <a href="/settings/access" className="text-accent-ink underline-offset-2 hover:underline">Change working folder</a>
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
