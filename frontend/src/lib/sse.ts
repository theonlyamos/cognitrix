export interface SSEFrame {
  id?: string;
  event?: string;
  data: string;
}

export function consumeSSE(buffer: string): {
  frames: SSEFrame[];
  rest: string;
} {
  const frames: SSEFrame[] = [];
  let start = 0;
  const boundary = /\r?\n\r?\n/g;
  let match: RegExpExecArray | null;

  while ((match = boundary.exec(buffer)) !== null) {
    const block = buffer.slice(start, match.index);
    start = match.index + match[0].length;
    const data: string[] = [];
    let id: string | undefined;
    let event: string | undefined;

    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      const raw = colon === -1 ? '' : line.slice(colon + 1);
      const value = raw.startsWith(' ') ? raw.slice(1) : raw;

      if (field === 'data') data.push(value);
      else if (field === 'id') id = value;
      else if (field === 'event') event = value;
    }

    if (data.length) frames.push({ id, event, data: data.join('\n') });
  }

  return { frames, rest: buffer.slice(start) };
}
