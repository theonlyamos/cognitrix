import { lazy, Suspense, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, Loader2, Maximize2, Minimize2, X } from 'lucide-react';
import { KindIcon } from '@/components/files/FileCards';
import {
  downloadFile, fileMeta, fileSrc, fmtSize, forgetDetails, getFile, getPreview, loadDetails, onOpenFileElsewhere, sourceLabel,
  type FilePreview, type ProducedFileDetails,
} from '@/lib/producedFiles';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));

const iconButton = 'grid h-9 w-9 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8';

const errorText = (err: unknown, fallback: string) =>
  (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback;

/** Sheets: a grid per tab. */
function SheetView({ preview }: { preview: FilePreview }) {
  const sheets = preview.sheets || [];
  const [tab, setTab] = useState(0);
  const sheet = sheets[Math.min(tab, sheets.length - 1)];
  if (!sheet) return <p className="p-4 text-[13px] text-fg-dim">{tr('ui.producedFiles.emptySheet')}</p>;
  const width = Math.max(0, ...sheet.rows.map((r) => r.length));
  return (
    <div className="flex h-full min-h-0 flex-col">
      {sheets.length > 1 && (
        <div role="tablist" className="flex flex-none gap-1 overflow-x-auto border-b border-line px-2 py-1.5">
          {sheets.map((s, i) => (
            <button key={s.name + i} role="tab" aria-selected={i === tab} type="button" onClick={() => setTab(i)}
              className={cn('min-h-9 flex-none rounded-md px-2.5 text-[12.5px]', i === tab ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:text-fg')}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="border-collapse font-mono text-[12px]">
          <thead className="sticky top-0 z-10 bg-panel-2">
            <tr>
              <th className="border border-line px-2 py-1 text-fg-dim" />
              {Array.from({ length: width }, (_, j) => (
                <th key={j} className="border border-line px-2 py-1 font-normal text-fg-dim">{String.fromCharCode(65 + (j % 26))}{j >= 26 ? Math.floor(j / 26) : ''}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.rows.map((row, i) => (
              <tr key={i}>
                <td className="border border-line bg-panel-2 px-2 py-1 text-right text-fg-dim">{i + 1}</td>
                {Array.from({ length: width }, (_, j) => (
                  <td key={j} className="max-w-[260px] truncate border border-line px-2 py-1" title={row[j] || ''}>{row[j] || ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sheet.total_rows > sheet.rows.length && (
        <p className="flex-none border-t border-line px-3 py-2 text-[12px] text-fg-dim">
          {tr('ui.producedFiles.firstRowsOf', { shown: sheet.rows.length, total: sheet.total_rows })}
        </p>
      )}
    </div>
  );
}

function ArchiveView({ preview }: { preview: FilePreview }) {
  const entries = preview.entries || [];
  return (
    <div className="h-full overflow-auto">
      <p className="px-4 pb-2 pt-3 text-[12.5px] text-fg-dim">
        {tr('ui.producedFiles.archiveSummary', { count: preview.count || entries.length, size: fmtSize(preview.unpacked_size || 0) || '0 KB' })}
      </p>
      <ul className="divide-y divide-line border-t border-line">
        {entries.map((e) => (
          <li key={e.name} className="flex items-center gap-3 px-4 py-1.5 font-mono text-[12px]">
            <span className={cn('min-w-0 flex-1 truncate', e.dir && 'text-fg-dim')}>{e.name}</span>
            {!e.dir && <span className="flex-none text-[11px] text-fg-dim">{fmtSize(e.size)}</span>}
          </li>
        ))}
      </ul>
      {(preview.count || 0) > entries.length && (
        <p className="px-4 py-2 text-[12px] text-fg-dim">{tr('ui.producedFiles.andMore', { count: (preview.count || 0) - entries.length })}</p>
      )}
    </div>
  );
}

function TextView({ file, preview }: { file: ProducedFileDetails; preview: FilePreview }) {
  const text = preview.text || '';
  if (file.mime_type === 'text/markdown') {
    return (
      <div className="md h-full overflow-auto px-5 py-4 text-[14px]">
        <Suspense fallback={<pre className="whitespace-pre-wrap">{text}</pre>}><MarkdownMessage content={text} /></Suspense>
      </div>
    );
  }
  return <pre className="h-full overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-[12px] leading-relaxed">{text}</pre>;
}

function DocView({ file, preview }: { file: ProducedFileDetails; preview: FilePreview | null }) {
  // Word and PowerPoint can't be drawn in the browser: a first look, then Download.
  const titles = preview?.titles || file.info?.titles || [];
  const excerpt = preview?.excerpt || file.info?.excerpt || '';
  return (
    <div className="h-full overflow-auto px-5 py-4">
      {file.thumb_url && <img src={fileSrc(file.thumb_url)} alt="" className="mb-4 max-w-full rounded border border-line bg-white" />}
      {titles.length > 0 && (
        <ol className="list-decimal space-y-1 pl-5 text-[13.5px]">
          {titles.map((t, i) => <li key={i}>{t}</li>)}
        </ol>
      )}
      {excerpt && <p className="whitespace-pre-line text-[13.5px] leading-relaxed text-fg-dim">{excerpt}</p>}
      <p className="mt-4 text-[12.5px] text-fg-dim">{tr('ui.producedFiles.downloadToOpen')}</p>
    </div>
  );
}

function Body({ file }: { file: ProducedFileDetails }) {
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [error, setError] = useState('');
  const wantsPreview = ['sheet', 'archive', 'text', 'data', 'html', 'code', 'doc', 'slides'].includes(file.kind);
  useEffect(() => {
    if (!wantsPreview) return;
    let alive = true;
    setPreview(null);
    setError('');
    getPreview(file.id).then((p) => { if (alive) setPreview(p); })
      .catch((err) => { if (alive) setError(errorText(err, tr('ui.producedFiles.previewFailed'))); });
    return () => { alive = false; };
  }, [file.id, wantsPreview]);
  const url = file.url ? fileSrc(file.url) : '';
  if (file.kind === 'image' && url) {
    return <div className="grid h-full place-items-center overflow-auto bg-panel-2 p-3"><img src={url} alt={file.name} className="max-h-full max-w-full object-contain" /></div>;
  }
  if (file.kind === 'video' && url) {
    return <div className="grid h-full place-items-center bg-black"><video src={url} poster={file.thumb_url ? fileSrc(file.thumb_url) : undefined} controls playsInline className="max-h-full w-full" /></div>;
  }
  if (file.kind === 'audio' && url) {
    return (
      <div className="grid h-full place-items-center p-6">
        <div className="w-full max-w-md space-y-3 text-center">
          <span className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-accent/15 text-accent-ink"><KindIcon kind="audio" size={28} /></span>
          <audio src={url} controls className="w-full" />
        </div>
      </div>
    );
  }
  if (file.kind === 'pdf' && url) {
    return <iframe title={file.name} src={url} className="h-full w-full border-0 bg-panel-2" />;
  }
  if (error) return <p role="alert" className="p-4 text-[13px] text-danger-ink">{error}</p>;
  if (wantsPreview && !preview) return <p className="flex items-center gap-2 p-4 text-[13px] text-fg-dim"><Loader2 size={14} className="animate-spin" aria-hidden />{tr('ui.producedFiles.loading')}</p>;
  if (preview?.kind === 'sheet') return <SheetView preview={preview} />;
  if (preview?.kind === 'archive') return <ArchiveView preview={preview} />;
  if (preview?.kind === 'text') return <TextView file={file} preview={preview} />;
  if (file.kind === 'doc' || file.kind === 'slides') return <DocView file={file} preview={preview} />;
  return (
    <div className="grid h-full place-items-center p-6 text-center">
      <div className="space-y-2">
        <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl border border-line text-fg-dim"><KindIcon kind={file.kind} size={24} /></span>
        <p className="text-[13px] text-fg-dim">{tr('ui.producedFiles.noPreview')}</p>
      </div>
    </div>
  );
}

/** One file beside the chat: a player, the pages, a sheet grid or what's in a zip. */
export function FileViewer({ fileId, onClose, onBack, expanded, onToggleExpand }: {
  fileId: string;
  onClose: () => void;
  onBack?: () => void;
  expanded?: boolean;
  onToggleExpand?: () => void;
}) {
  const [file, setFile] = useState<ProducedFileDetails | null>(null);
  const [error, setError] = useState('');
  const [copying, setCopying] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let alive = true;
    setFile(null);
    setError('');
    setCopying(false);
    loadDetails(fileId).then(async (d) => {
      if (!alive) return;
      if (d.available) { setFile(d); return; }
      // Still in the sandbox or on the PC: copy it in first.
      setFile(d);
      setCopying(true);
      try {
        const fetched = await getFile(fileId, true);
        forgetDetails(fileId);
        if (alive) setFile(fetched);
      } catch (err) {
        if (alive) setError(errorText(err, tr('ui.producedFiles.notAvailable')));
      } finally {
        if (alive) setCopying(false);
      }
    }).catch((err) => { if (alive) setError(errorText(err, tr('ui.producedFiles.notFound'))); });
    return () => { alive = false; };
  }, [fileId]);

  const download = async () => {
    setDownloading(true);
    try {
      await downloadFile(fileId);
    } catch (err) {
      setError(errorText(err, tr('ui.producedFiles.downloadFailed')));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section aria-label={file?.name || tr('ui.producedFiles.file')} className="flex h-full min-h-0 flex-col bg-panel">
      <header className="flex min-h-12 flex-none items-center gap-1.5 border-b border-line py-1.5 pl-2 pr-2 md:px-3">
        {onBack && !expanded && (
          <button type="button" onClick={onBack} className="inline-flex h-9 flex-none items-center gap-1 rounded-lg px-2 font-mono text-[11.5px] text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8">
            <span aria-hidden>‹</span>{' '}{tr('ui.producedFiles.thisChat')}
          </button>
        )}
        {file && <span className="ml-1 flex-none text-fg-dim"><KindIcon kind={file.kind} size={16} /></span>}
        <div className="ml-1 min-w-0 flex-1">
          <h2 className="truncate text-[14px] font-semibold" title={file?.name}>{file?.name || tr('ui.producedFiles.file')}</h2>
          {file && (
            <p className="truncate font-mono text-[10.5px] text-fg-dim">
              {[fileMeta(file), sourceLabel(file.source), file.agent?.name].filter(Boolean).join(' · ')}
            </p>
          )}
        </div>
        <button type="button" onClick={() => { void download(); }} disabled={!file || downloading}
          className="inline-flex h-9 flex-none items-center gap-1.5 rounded-md border border-line px-2.5 text-[12.5px] hover:bg-panel-2 disabled:opacity-60 md:h-8">
          {downloading ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Download size={14} aria-hidden />}
          <span className="hidden sm:inline">{tr('ui.producedFiles.download')}</span>
        </button>
        {onToggleExpand && (
          <button type="button" onClick={onToggleExpand} className={cn(iconButton, 'hidden md:grid')}
            aria-label={expanded ? tr('ui.producedFiles.shrink') : tr('ui.producedFiles.widen')} title={expanded ? tr('ui.producedFiles.shrink') : tr('ui.producedFiles.widen')}>
            {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
        )}
        <button type="button" onClick={onClose} aria-label={tr('ui.producedFiles.close')} className={iconButton}><X size={16} /></button>
      </header>
      <div className="min-h-0 flex-1">
        {error ? (
          <p role="alert" className="p-4 text-[13px] text-danger-ink">{error}</p>
        ) : copying ? (
          <p className="flex items-center gap-2 p-4 text-[13px] text-fg-dim">
            <Loader2 size={14} className="animate-spin" aria-hidden />
            {file?.source === 'pc' ? tr('ui.producedFiles.copyingFromPc') : tr('ui.producedFiles.copyingFromSandbox')}
          </p>
        ) : file ? (
          <Body file={file} />
        ) : (
          <p className="flex items-center gap-2 p-4 text-[13px] text-fg-dim"><Loader2 size={14} className="animate-spin" aria-hidden />{tr('ui.producedFiles.loading')}</p>
        )}
      </div>
      {file?.caption && <p className="flex-none border-t border-line px-4 py-2 text-[12.5px] text-fg-dim">{file.caption}</p>}
    </section>
  );
}

/** Opens files on pages without a side panel (tasks, Library): full screen on
 *  a phone, a large dialog elsewhere. Mounted once in the app layout. */
export function FileViewerHost() {
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => onOpenFileElsewhere(setOpen), []);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/50 md:items-center md:p-6" onClick={() => setOpen(null)}>
      <div role="dialog" aria-modal="true" className="flex h-full w-full flex-col overflow-hidden border-line bg-panel md:h-[min(86vh,900px)] md:max-w-4xl md:rounded-xl md:border"
        onClick={(e) => e.stopPropagation()}>
        <FileViewer fileId={open} onClose={() => setOpen(null)} />
      </div>
    </div>,
    document.body,
  );
}
