/** How an agent's thumbnail looks. Empty means a colour made from its name. */
export interface AgentAvatar {
  emoji?: string;
  hue?: number | null;
}

/** Hues offered on the agent page. */
export const AVATAR_HUES = [78, 150, 200, 230, 280, 340, 10, 28, 48];

/** A steady hue for a name, so the same agent has the same colour everywhere. */
export function hueFromName(name: string): number {
  let hash = 0;
  for (const ch of name.trim().toLowerCase()) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0;
  return AVATAR_HUES[hash % AVATAR_HUES.length];
}

export function avatarHue(name: string, avatar?: AgentAvatar | null): number {
  const hue = avatar?.hue;
  return typeof hue === 'number' && Number.isFinite(hue) ? ((Math.round(hue) % 360) + 360) % 360 : hueFromName(name);
}

/** The emoji to show, or '' to show the first letter. */
export function avatarEmoji(avatar?: AgentAvatar | null): string {
  return (avatar?.emoji || '').trim();
}
