import { publishedFrom } from '@/lib/artifactPages';
import type { ProducedFileRef } from '@/lib/producedFiles';

interface TurnMessage {
  tools?: { name: string; status?: string; result?: string | null; files?: ProducedFileRef[] }[];
  filesMade?: ProducedFileRef[];
}

/** The pages and files one turn put in front of the person, each once. */
export function madeInTurn(messages: TurnMessage[]) {
  const pages = new Map<string, { id: string; title: string; toolName: string; result: string }>();
  const files = new Map<string, ProducedFileRef>();
  for (const message of messages) {
    for (const tool of message.tools ?? []) {
      if (tool.status !== 'done') continue;
      const page = publishedFrom(tool.name, tool.result ?? undefined);
      // A later version of the same page replaces the earlier card.
      if (page) pages.set(page.id, { id: page.id, title: page.title, toolName: tool.name, result: tool.result as string });
      for (const file of tool.files ?? []) files.set(file.id, file);
    }
    for (const file of message.filesMade ?? []) {
      if (file.shown && !files.has(file.id)) files.set(file.id, file);
    }
  }
  return { pages: [...pages.values()], files: [...files.values()] };
}
