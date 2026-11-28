import { useEffect, useState, type ReactNode } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { pageInApp } from '@/lib/hostedApps';
import { tailnetAppUrl } from '@/lib/tailscale';
import { tr } from '@/lib/i18n';

/** An app's link opened by someone who isn't signed in. Its owner may have shared it with
 *  their tailnet: then Tailscale tells the server who this is, and the app opens with no
 *  Cognitrix account. Anyone else gets `otherwise` (the sign-in page). */
export function TailnetGuest({ otherwise, goTo = (url: string) => window.location.replace(url) }: {
  otherwise: ReactNode; goTo?: (url: string) => void;
}) {
  const { slug = '' } = useParams();
  const [params] = useSearchParams();
  const at = params.get('at');
  const [refused, setRefused] = useState(false);

  useEffect(() => {
    let live = true;
    tailnetAppUrl(slug)
      .then((url) => { if (live) goTo(url + pageInApp(at)); })
      .catch(() => { if (live) setRefused(true); });
    return () => { live = false; };
    // goTo is the page's own navigation: it never changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, at]);

  if (refused) return <>{otherwise}</>;
  return (
    <div role="status" className="grid min-h-dvh place-items-center bg-bg px-4 text-center text-[13.5px] text-fg-dim">
      {tr('ui.hostedApps.tailnetGuest.openingTheApp')}
    </div>
  );
}
