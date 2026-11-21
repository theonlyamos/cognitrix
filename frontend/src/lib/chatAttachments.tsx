import { tr } from '@/lib/i18n';

// What a message box accepts: pictures and common document types. Keep DOC_EXT
// and ACCEPT_ATTR in sync: `accept` filters the file picker, and the check
// below runs again so a drop or a paste can't slip other types in.
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 25 * 1024 * 1024;
export const MAX_FILES = 20;
export const DOC_EXT = /\.(pdf|txt|md|markdown|csv|tsv|docx?|xlsx?|pptx?|rtf|odt|ods|odp|json|log)$/i;
export const ACCEPT_ATTR = 'image/*,.pdf,.txt,.md,.markdown,.csv,.tsv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.rtf,.odt,.ods,.odp,.json,.log';

export interface Attachment {
  id: string;
  kind: 'image' | 'file';
  name: string;
  mime: string;
  file: File;
  previewUrl?: string;
}

/** Which of the picked files can be attached, given what is attached already,
 *  and what to tell the person about the ones that can't. */
export function checkFiles(incoming: File[], current: Attachment[]): { added: Attachment[]; error: string | null } {
  let error: string | null = null;
  let total = current.reduce((sum, a) => sum + a.file.size, 0);
  const added: Attachment[] = [];
  for (const file of incoming) {
    if (current.length + added.length >= MAX_FILES) {
      error = tr('ui.home.youCanAttachUpTo');
      break;
    }
    // Pasted and dropped files often arrive with no type: read it from the name.
    const ext = /\.(png|jpe?g|gif|webp|bmp|avif)$/i.exec(file.name)?.[1]?.toLowerCase();
    const imgMime = file.type.startsWith('image/') ? file.type : ext ? `image/${ext === 'jpg' ? 'jpeg' : ext}` : '';
    const isImage = !!imgMime;
    const isDoc = DOC_EXT.test(file.name) || /(pdf|word|excel|spreadsheet|presentation|officedocument|^text\/|rtf|csv|json)/i.test(file.type);
    if (!isImage && !isDoc) {
      error = tr('ui.home.onlyImagesAndDocumentsCan');
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      error = tr('ui.home.nameIsLargerThan10', { name: file.name });
      continue;
    }
    if (total + file.size > MAX_TOTAL_BYTES) {
      error = tr('ui.home.attachmentsExceed25MbTotal');
      break;
    }
    total += file.size;
    added.push({
      id: crypto.randomUUID(),
      kind: isImage ? 'image' : 'file',
      name: file.name || 'file',
      mime: file.type || imgMime || 'application/octet-stream',
      file,
      previewUrl: isImage && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : undefined,
    });
  }
  return { added, error };
}
