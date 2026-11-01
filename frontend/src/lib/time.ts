/** Server times are naive UTC ('YYYY-MM-DD HH:MM:SS'); parse with an explicit Z. */
export const utcToLocal = (s: string) => new Date(s.replace(' ', 'T') + 'Z').toLocaleString();
