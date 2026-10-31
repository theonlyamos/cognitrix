import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '@/context/AppContext';

/** Landing page after Google/GitHub sign-in on the web: the token arrives in
 *  the URL fragment (never sent to a server), is stored, and the hash cleared. */
export default function OAuthComplete() {
  const { checkAuth } = useUser();
  const navigate = useNavigate();

  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get('token');
    window.history.replaceState(null, '', window.location.pathname);
    if (!token) {
      navigate('/?oauth_error=Sign-in+did+not+finish.+Please+try+again.', { replace: true });
      return;
    }
    localStorage.setItem('token', token);
    void checkAuth().then(() => navigate('/home', { replace: true }));
  }, [checkAuth, navigate]);

  return (
    <div role="status" className="flex h-full min-h-screen items-center justify-center bg-bg font-mono text-sm text-fg-dim">
      signing you in…
    </div>
  );
}
