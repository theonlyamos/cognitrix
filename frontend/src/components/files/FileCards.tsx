import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Box, Download, File, FileArchive, FileAudio, FileCode, FileImage, FileSpreadsheet, FileText, FileVideo,
  Loader2, Pause, Play, Presentation, Type,
} from 'lucide-react';
import {
  downloadFile, fileMeta, fileSrc, fmtDuration, loadDetails, requestOpenFile,
  type FileKind, type ProducedFileDetails, type ProducedFileRef,
} from '@/lib/producedFiles';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

function useDetails(id: string, enabled: boolean) {
  const [data, setData] = useState<ProducedFileDetails | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    loadDetails(id).then((d) => { if (alive) setData(d); }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [id, enabled]);
  return { data, failed };
}

function useVisible<T extends Element>() {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || visible) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: '300px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);
  return { ref, visible };
}

export function KindIcon({ kind, size = 18 }: { kind: FileKind; size?: number }) {
  const props = { size, 'aria-hidden': true } as const;
  switch (kind) {
    case 'image': return <FileImage {...props} />;
    case 'video': return <FileVideo {...props} />;
    case 'audio': return <FileAudio {...props} />;
    case 'pdf': case 'doc': case 'text': return <FileText {...props} />;
    case 'slides': return <Presentation {...props} />;
    case 'sheet': return <FileSpreadsheet {...props} />;
    case 'archive': return <FileArchive {...props} />;
    case 'code': case 'html': case 'data': return <FileCode {...props} />;
    case 'model': return <Box {...props} />;
    case 'font': return <Type {...props} />;
    default: return <File {...props} />;
  }
}

const kindTone: Partial<Record<FileKind, string>> = {
  pdf: 'text-danger-ink', sheet: 'text-ok', slides: 'text-warn-ink', doc: 'text-accent-ink',
};

function open(file: ProducedFileRef, url?: string) {
  if (requestOpenFile(file.id)) return;
  if (url) window.open(fileSrc(url), '_blank', 'noopener');
}

function Frame({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('relative grid aspect-video w-full place-items-center overflow-hidden bg-panel-2', className)}>{children}</div>;
}

function Placeholder({ file }: { file: ProducedFileRef }) {
  return (
    <Frame>
      <span className={cn('grid h-12 w-12 place-items-center rounded-xl border border-line bg-panel text-fg-dim', kindTone[file.kind])}>
        <KindIcon kind={file.kind} size={22} />
      </span>
    </Frame>
  );
}

function ImagePreview({ file, d }: { file: ProducedFileRef; d: ProducedFileDetails | null }) {
  const src = d?.thumb_url || (file.mime_type === 'image/svg+xml' ? d?.url : undefined);
  if (!src) return <Placeholder file={file} />;
  return (
    <Frame>
      <img src={fileSrc(src)} alt={file.name} loading="lazy" className="h-full w-full object-contain" />
    </Frame>
  );
}

function VideoPreview({ file, d }: { file: ProducedFileRef; d: ProducedFileDetails | null }) {
  const [playing, setPlaying] = useState(false);
  const duration = fmtDuration(file.info?.duration);
  if (playing && d?.url) {
    return (
      <Frame className="bg-black">
        <video src={fileSrc(d.url)} poster={d.thumb_url ? fileSrc(d.thumb_url) : undefined} controls autoPlay playsInline
          className="h-full w-full" onClick={(e) => e.stopPropagation()} />
      </Frame>
    );
  }
  return (
    <Frame className="bg-black">
      {d?.thumb_url && <img src={fileSrc(d.thumb_url)} alt="" loading="lazy" className="absolute inset-0 h-full w-full object-contain" />}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          if (d?.url) setPlaying(true);
          else open(file);
        }}
        aria-label={tr('ui.producedFiles.playName', { name: file.name })}
        className="relative grid h-12 w-12 place-items-center rounded-full bg-white/20 text-white backdrop-blur transition-colors hover:bg-white/30"
      >
        <Play size={20} aria-hidden className="ml-0.5" fill="currentColor" />
      </button>
      {duration && <span className="absolute bottom-1.5 right-2 rounded bg-black/60 px-1.5 py-px font-mono text-[11px] text-white">{duration}</span>}
    </Frame>
  );
}

function AudioPreview({ file, d }: { file: ProducedFileRef; d: ProducedFileDetails | null }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const peaks = file.info?.peaks?.length ? file.info.peaks : Array.from({ length: 32 }, (_, i) => 0.3 + 0.5 * Math.abs(Math.sin(i * 1.7)));
  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    const el = audio.current;
    if (!el) { open(file); return; }
    if (el.paused) void el.play().catch(() => {});
    else el.pause();
  };
  return (
    <div className="flex items-center gap-3 bg-panel-2 px-3 py-3.5">
      <button
        type="button"
        onClick={toggle}
        aria-label={playing ? tr('ui.producedFiles.pauseName', { name: file.name }) : tr('ui.producedFiles.playName', { name: file.name })}
        className="grid h-9 w-9 flex-none place-items-center rounded-full bg-accent text-accent-foreground transition-opacity hover:opacity-90"
      >
        {playing ? <Pause size={15} aria-hidden fill="currentColor" /> : <Play size={15} aria-hidden fill="currentColor" className="ml-0.5" />}
      </button>
      <div className="flex h-8 min-w-0 flex-1 items-center gap-[2px]" aria-hidden>
        {peaks.map((p, i) => (
          <i key={i} className={cn('flex-1 rounded-[1px]', i / peaks.length < progress ? 'bg-accent' : 'bg-accent/35')}
            style={{ height: `${Math.max(12, p * 100)}%` }} />
        ))}
      </div>
      <span className="flex-none font-mono text-[11px] text-fg-dim">{fmtDuration(file.info?.duration)}</span>
      {d?.url && (
        <audio
          ref={audio}
          src={fileSrc(d.url)}
          preload="none"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => { setPlaying(false); setProgress(0); }}
          onTimeUpdate={(e) => {
            const el = e.currentTarget;
            if (el.duration) setProgress(el.currentTime / el.duration);
          }}
        />
      )}
    </div>
  );
}

function SheetPreview({ file }: { file: ProducedFileRef }) {
  const rows = file.info?.preview;
  if (!rows?.length) return <Placeholder file={file} />;
  return (
    <Frame className="place-items-stretch bg-panel p-2">
      <div className="overflow-hidden">
        <table className="w-full table-fixed border-collapse font-mono text-[10.5px]">
          <tbody>
            {rows.slice(0, 6).map((row, i) => (
              <tr key={i} className={i === 0 ? 'bg-ok/10 font-semibold' : undefined}>
                {row.slice(0, 5).map((cell, j) => (
                  <td key={j} className="truncate border border-line px-1.5 py-[3px] text-fg">{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Frame>
  );
}

function PagePreview({ file, d }: { file: ProducedFileRef; d: ProducedFileDetails | null }) {
  if (d?.thumb_url) {
    return (
      <Frame className="bg-panel-2 pt-3">
        <img src={fileSrc(d.thumb_url)} alt="" loading="lazy"
          className="h-full max-w-[70%] self-start rounded-t-sm border border-b-0 border-line bg-white object-cover object-top shadow-sm" />
      </Frame>
    );
  }
  const lines = file.info?.excerpt || (file.info?.titles || []).join('\n');
  if (!lines) return <Placeholder file={file} />;
  return (
    <Frame className="place-items-stretch bg-panel-2 px-4 pt-3">
      <div className="overflow-hidden rounded-t-sm border border-b-0 border-line bg-panel px-3 py-2 text-[11px] leading-snug text-fg-dim">
        <p className="line-clamp-6 whitespace-pre-line">{lines}</p>
      </div>
    </Frame>
  );
}

function TextPreview({ file }: { file: ProducedFileRef }) {
  if (!file.info?.excerpt) return <Placeholder file={file} />;
  return (
    <Frame className="place-items-stretch bg-panel-2 p-3">
      <pre className="overflow-hidden whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug text-fg-dim">{file.info.excerpt.slice(0, 500)}</pre>
    </Frame>
  );
}

function ArchivePreview({ file }: { file: ProducedFileRef }) {
  const first = file.info?.first || [];
  return (
    <Frame className="place-items-stretch bg-panel-2 p-3">
      <div className="flex min-w-0 items-start gap-3">
        <span className="grid h-10 w-10 flex-none place-items-center rounded-lg border border-line bg-panel text-fg-dim"><FileArchive size={18} aria-hidden /></span>
        <ul className="min-w-0 flex-1 space-y-0.5 font-mono text-[11px] text-fg-dim">
          {first.map((name) => <li key={name} className="truncate">{name}</li>)}
          {(file.info?.count || 0) > first.length && <li>{tr('ui.producedFiles.andMore', { count: (file.info?.count || 0) - first.length })}</li>}
        </ul>
      </div>
    </Frame>
  );
}

function Preview({ file, d }: { file: ProducedFileRef; d: ProducedFileDetails | null }) {
  switch (file.kind) {
    case 'image': return <ImagePreview file={file} d={d} />;
    case 'video': return <VideoPreview file={file} d={d} />;
    case 'audio': return <AudioPreview file={file} d={d} />;
    case 'sheet': return <SheetPreview file={file} />;
    case 'pdf': case 'doc': case 'slides': return <PagePreview file={file} d={d} />;
    case 'text': case 'html': case 'data': return <TextPreview file={file} />;
    case 'archive': return <ArchivePreview file={file} />;
    default: return <Placeholder file={file} />;
  }
}

export function FileCard({ file }: { file: ProducedFileRef }) {
  const { ref, visible } = useVisible<HTMLDivElement>();
  const { data } = useDetails(file.id, visible);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const download = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setDownloading(true);
    setError('');
    try {
      await downloadFile(file.id);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || tr('ui.producedFiles.downloadFailed'));
    } finally {
      setDownloading(false);
    }
  };
  return (
    <div
      ref={ref}
      role="button"
      tabIndex={0}
      onClick={() => open(file, data?.url)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(file, data?.url); } }}
      aria-label={tr('ui.producedFiles.openName', { name: file.name })}
      className="group flex min-w-0 cursor-pointer flex-col overflow-hidden rounded-[10px] border border-line bg-panel text-left transition-colors hover:border-fg-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
      <Preview file={file} d={data} />
      <div className="flex min-w-0 items-center gap-2 border-t border-line px-2.5 py-2">
        <span className={cn('flex-none text-fg-dim', kindTone[file.kind])}><KindIcon kind={file.kind} size={15} /></span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium" title={file.name}>{file.name}</span>
          <span className="block truncate font-mono text-[10.5px] text-fg-dim">{error || fileMeta(file)}</span>
        </span>
        <button
          type="button"
          onClick={(e) => { void download(e); }}
          aria-label={tr('ui.producedFiles.downloadName', { name: file.name })}
          title={tr('ui.producedFiles.download')}
          className="grid h-11 w-11 flex-none place-items-center rounded-md text-fg-dim transition-colors hover:bg-panel-2 hover:text-fg md:h-8 md:w-8"
        >
          {downloading ? <Loader2 size={15} aria-hidden className="animate-spin" /> : <Download size={15} aria-hidden />}
        </button>
      </div>
    </div>
  );
}

/** Files as preview cards: one wide card for a single file, a grid for more. */
export function FileCards({ files, caption }: { files: ProducedFileRef[]; caption?: string }) {
  if (!files.length) return null;
  return (
    <div className="w-full max-w-2xl space-y-1.5">
      {caption && <p className="text-[12.5px] text-fg-dim">{caption}</p>}
      <div className={cn('grid gap-2.5', files.length === 1 ? 'max-w-md grid-cols-1' : 'grid-cols-1 sm:grid-cols-2')}>
        {files.map((f) => <FileCard key={f.id} file={f} />)}
      </div>
    </div>
  );
}
