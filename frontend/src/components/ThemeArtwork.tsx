import { useMemo } from 'react';
import { useOptionalTheme } from '@/context/ThemeContext';
import { artworkColors, artworkImage } from '@/lib/theme/artwork';
import { cn } from '@/lib/utils';

/** The theme's drawing as an image, painted in the theme's colours for the surface it sits on. */
function useArtworkSrc(surface: 'bg' | 'panel') {
  const artwork = useOptionalTheme()?.artwork ?? null;
  return useMemo(() => {
    if (!artwork) return null;
    return { src: artworkImage(artwork.svg, artworkColors(artwork.tokens, artwork.tokens[surface])), strength: artwork.strength };
  }, [artwork, surface]);
}

/** A band at the top of the sidebar that fades out under the first items. */
export function SidebarArtwork({ className }: { className?: string }) {
  const art = useArtworkSrc('panel');
  if (!art) return null;
  // The band gets its own room (the spacer) and only fades out under the first label.
  return (
    <>
      <div
        aria-hidden="true"
        data-testid="sidebar-artwork"
        className={cn('pointer-events-none absolute inset-x-0 top-0 z-0 h-[96px] overflow-hidden [mask-image:linear-gradient(#000_55%,transparent)]', className)}
        style={{ opacity: art.strength }}
      >
        <img src={art.src} alt="" draggable={false} className="h-full w-full select-none object-cover object-bottom" />
      </div>
      <div aria-hidden="true" className={cn('h-[76px] flex-none md:h-[54px]', className)} />
    </>
  );
}

/** A larger scene above a new chat's greeting and on the Dashboard. */
export function ArtworkScene({ className, fade = 'round' }: { className?: string; fade?: 'round' | 'left' }) {
  const art = useArtworkSrc('bg');
  if (!art) return null;
  return (
    <div
      aria-hidden="true"
      data-testid="artwork-scene"
      className={cn(
        'pointer-events-none overflow-hidden',
        fade === 'round'
          ? '[mask-image:radial-gradient(ellipse_62%_78%_at_50%_55%,#000_55%,transparent_100%)]'
          : '[mask-image:linear-gradient(to_right,transparent,#000_35%)]',
        className,
      )}
      style={{ opacity: art.strength }}
    >
      <img src={art.src} alt="" draggable={false} className="h-full w-full select-none object-cover" />
    </div>
  );
}
