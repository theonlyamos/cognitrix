import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { errorMessage } from '@/lib/api';
import { openUrl } from '@/lib/hostedApps';

/** /apps/open/:slug, the app's stable link: signs you in, then goes to the running app. */
export default function OpenApp() {
  const { slug = '' } = useParams();
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    openUrl(slug).then((url) => { if (live) window.location.replace(url); })
      .catch((e) => { if (live) setError(errorMessage(e, 'You can’t open this app.')); });
    return () => { live = false; };
  }, [slug]);
  return (
    <div className="grid min-h-dvh place-items-center bg-bg px-4 text-fg">
      <div className="max-w-sm text-center">
        {error ? (
          <>
            <h1 className="text-[17px] font-semibold">This app didn’t open</h1>
            <p className="mt-2 text-[13.5px] text-fg-dim">{error}</p>
            <Link to="/apps" className="mt-4 inline-flex h-9 items-center rounded-md border border-line px-3 text-[13px] font-medium hover:bg-panel-2">Go to Apps</Link>
          </>
        ) : (
          <p className="text-[13.5px] text-fg-dim">Opening the app… it may take a few seconds to wake up.</p>
        )}
      </div>
    </div>
  );
}
