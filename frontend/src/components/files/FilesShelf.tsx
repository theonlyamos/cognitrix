import { useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { KindIcon } from '@/components/files/FileCards';
import { downloadZip, fmtSize, requestOpenFile, sourceLabel, type ProducedFileRef } from '@/lib/producedFiles';
import { tr, trn } from '@/lib/i18n';

const SHOWN = 8;

/** The other files a turn made, as small chips under the reply. */
export function FilesShelf({ files }: { files: ProducedFileRef[] }) {
  const [all, setAll] = useState(false);
  const [zipping, setZipping] = useState(false);
  const [error, setError] = useState('');
  if (!files.length) return null;
  const shown = all ? files : files.slice(0, SHOWN);
  const zip = async () => {
    setZipping(true);
    setError('');
    try {
      await downloadZip(files.map((f) => f.id), 'files');
    } catch {
      setError(tr('ui.producedFiles.zipFailed'));
    } finally {
      setZipping(false);
    }
  };
  return (
    <div className="w-full max-w-2xl rounded-[10px] border border-dashed border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12.5px] font-semibold">{trn(files.length, 'ui.producedFiles.moreFilesMade_one', 'ui.producedFiles.moreFilesMade_other')}</span>
        <span className="grow" />
        {files.length > 1 && (
          <button
            type="button"
            onClick={() => { void zip(); }}
            disabled={zipping}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-line px-2.5 text-[12px] text-fg-dim transition-colors hover:text-fg disabled:opacity-60 md:min-h-0 md:py-1"
          >
            {zipping ? <Loader2 size={13} aria-hidden className="animate-spin" /> : <Download size={13} aria-hidden />}
            {tr('ui.producedFiles.downloadAll')}
          </button>
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {shown.map((f) => (
          <li key={f.id} className="min-w-0 max-w-full">
            <button
              type="button"
              onClick={() => requestOpenFile(f.id)}
              title={`${f.name} · ${fmtSize(f.size)} · ${sourceLabel(f.source)}`}
              className="inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-md border border-line bg-panel px-2 text-[12px] transition-colors hover:border-fg-dim md:min-h-0 md:py-1"
            >
              <span className="flex-none text-fg-dim"><KindIcon kind={f.kind} size={13} /></span>
              <span className="min-w-0 truncate font-mono text-[11.5px]">{f.name}</span>
              <span className="flex-none text-[10.5px] text-fg-dim">{fmtSize(f.size)}</span>
            </button>
          </li>
        ))}
        {!all && files.length > SHOWN && (
          <li>
            <button type="button" onClick={() => setAll(true)} className="min-h-11 px-1 text-[12px] text-accent-ink underline-offset-2 hover:underline md:min-h-0 md:py-1">
              {tr('ui.producedFiles.showAll', { count: files.length })}
            </button>
          </li>
        )}
      </ul>
      {error && <p role="alert" className="mt-1.5 text-[12px] text-danger-ink">{error}</p>}
    </div>
  );
}
