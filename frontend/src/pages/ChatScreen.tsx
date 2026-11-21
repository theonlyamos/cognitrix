import { lazy, Suspense } from 'react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';

const Home = lazy(() => import('@/pages/Home'));
const TeamChat = lazy(() => import('@/pages/TeamChat'));

/** The main chat screen: a chat with one agent, or with a team (?team=<id>). */
export default function ChatScreen() {
  const [params] = useSearchParams();
  const team = params.get('team');
  return (
    <Suspense fallback={<div className="h-full min-w-0 flex-1 bg-bg" />}>
      {team ? <TeamChat key={team} teamId={team} /> : <Home />}
    </Suspense>
  );
}

/** /teams/:teamId/chat, the team chat's old address. */
export function TeamChatLink() {
  const { teamId = '' } = useParams();
  const [params] = useSearchParams();
  const next = new URLSearchParams({ team: teamId });
  const chat = params.get('chat');
  if (chat) next.set('chat', chat);
  return <Navigate to={`/home?${next.toString()}`} replace />;
}
