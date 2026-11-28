import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation, useParams } from 'react-router-dom';
import { UserProvider, useUser } from '@/context/AppContext';
import { SessionProvider } from '@/context/SessionContext';
import { AccountThemeSync, ThemeProvider } from '@/context/ThemeContext';
import AppLayout from '@/components/AppLayout';
import DesktopFrame from '@/components/DesktopFrame';
import { isDesktop } from '@/lib/desktop';
import { rememberAfterSignIn } from '@/lib/afterSignIn';
import { TailnetGuest } from '@/components/hosted-apps/TailnetGuest';
import Login from '@/pages/Login';
import Signup from '@/pages/Signup';
import ServerSetup from '@/pages/ServerSetup';
import OAuthComplete from '@/pages/OAuthComplete';
import ResetPassword from '@/pages/ResetPassword';
import '@/app.css';
import { tr } from '@/lib/i18n';

// Code-split the authenticated app; auth pages + shell stay eager for first paint.
const ChatScreen = lazy(() => import('@/pages/ChatScreen'));
const ChillChatScreen = lazy(() => import('@/pages/ChatScreen').then((m) => ({ default: m.ChillChatScreen })));
const TeamChatLink = lazy(() => import('@/pages/ChatScreen').then((m) => ({ default: m.TeamChatLink })));
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Agents = lazy(() => import('@/pages/Agents'));
const AgentDetail = lazy(() => import('@/pages/AgentDetail'));
const AgentPage = lazy(() => import('@/pages/AgentPage'));
const Tasks = lazy(() => import('@/pages/Tasks'));
const TaskPage = lazy(() => import('@/pages/TaskPage'));
const TaskDetail = lazy(() => import('@/pages/TaskDetail'));
const TeamDetail = lazy(() => import('@/pages/TeamDetail'));
const TeamPage = lazy(() => import('@/pages/TeamPage'));
const ApiKeys = lazy(() => import('@/pages/ApiKeys'));
const Inbox = lazy(() => import('@/pages/Inbox'));
const Apps = lazy(() => import('@/pages/Apps'));
const AppDetail = lazy(() => import('@/pages/AppDetail'));
const CreateApp = lazy(() => import('@/pages/CreateApp'));
const OpenApp = lazy(() => import('@/pages/OpenApp'));
const ArtifactPage = lazy(() => import('@/pages/ArtifactPage'));
const PublicArtifact = lazy(() => import('@/pages/PublicArtifact'));
const PasskeyHandoff = lazy(() => import('@/pages/PasskeyHandoff'));
const Projects = lazy(() => import('@/pages/Projects'));
const ProjectDetail = lazy(() => import('@/pages/ProjectDetail'));
const Meetings = lazy(() => import('@/pages/Meetings'));
const Board = lazy(() => import('@/pages/Board'));
const Payments = lazy(() => import('@/pages/Payments'));
const Settings = lazy(() => import('@/pages/Settings'));
const QuickAsk = lazy(() => import('@/pages/QuickAsk'));
const SharedChat = lazy(() => import('@/pages/SharedChat'));

function LoadingScreen({ waitingForServer = false }: { waitingForServer?: boolean }) {
  return (
    <div className="flex h-full min-h-[40vh] flex-1 items-center justify-center bg-bg text-fg-dim">
      <div className="flex items-center gap-3 font-mono text-sm">
        <svg className="h-5 w-5 animate-spin text-accent" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" />
          <path className="opacity-90" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" d="M4 12a8 8 0 0 1 8-8" />
        </svg>
        {waitingForServer ? (isDesktop() ? tr('ui.app.waitingForTheEngine') : tr('ui.app.waitingForTheServer')) : tr('ui.app.loading')}
      </div>
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading, waitingForServer } = useUser();
  if (isLoading) return <DesktopFrame><LoadingScreen waitingForServer={waitingForServer} /></DesktopFrame>;
  if (!user) return <SignInFirst />;
  return <>{children}</>;
}

/** Signed out: remember an app link so signing in comes back to it. */
function SignInFirst() {
  const location = useLocation();
  rememberAfterSignIn(location.pathname + location.search);
  return <Navigate to="/" replace />;
}

/** An app's link: signed in, the app (OpenApp tries the tailnet way in when the account
 *  has none); signed out, the way in for someone on the owner's tailnet when the app was
 *  shared with them, and the sign-in page for everyone else. */
function AppLinkRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading, waitingForServer } = useUser();
  if (isLoading) return <DesktopFrame><LoadingScreen waitingForServer={waitingForServer} /></DesktopFrame>;
  if (!user) return isDesktop() ? <SignInFirst /> : <TailnetGuest otherwise={<SignInFirst />} />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading, waitingForServer } = useUser();
  if (isLoading) return <DesktopFrame><LoadingScreen waitingForServer={waitingForServer} /></DesktopFrame>;
  if (user) return <Navigate to="/dashboard" replace />;
  return <DesktopFrame>{children}</DesktopFrame>;
}

/** /chat/<id>: the short form of a chat's link. */
function ChatLink() {
  const { chatId = '' } = useParams();
  return <Navigate to={`/home?chat=${encodeURIComponent(chatId)}`} replace />;
}

function AppRoutes() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <Routes>
        <Route path="/" element={<PublicRoute><Login /></PublicRoute>} />
        <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />
        <Route path="/setup" element={<PublicRoute><ServerSetup /></PublicRoute>} />
        <Route path="/oauth/complete" element={<OAuthComplete />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        {/* The desktop app's passkey step, opened in the default browser. */}
        <Route path="/passkey" element={<PasskeyHandoff />} />
        {/* A public artifact link: readable signed out. */}
        <Route path="/s/:token" element={<PublicArtifact />} />
        {/* The desktop app's Alt+Space window: no app shell around it. */}
        <Route path="/quick" element={<ProtectedRoute><QuickAsk /></ProtectedRoute>} />
        {/* An app's stable link: signs you in, then goes to the running app. */}
        <Route path="/apps/open/:slug" element={<AppLinkRoute><OpenApp /></AppLinkRoute>} />

        {/* Persistent shell: Sidebar mounts once, pages render in the Outlet. */}
        <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/home" element={<ChatScreen />} />
          <Route path="/chill" element={<ChillChatScreen />} />
          <Route path="/chat/:chatId" element={<ChatLink />} />
          <Route path="/shared/:chatId" element={<SharedChat />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/new" element={<AgentPage />} />
          <Route path="/agents/:agentId" element={<AgentDetail />} />
          <Route path="/agents/:agentId/:tab" element={<AgentDetail />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/tasks/new" element={<TaskPage />} />
          <Route path="/tasks/:taskId" element={<TaskDetail />} />
          <Route path="/tasks/:taskId/edit" element={<TaskPage />} />
          <Route path="/teams" element={<Navigate to="/agents?tab=teams" replace />} />
          <Route path="/teams/new" element={<TeamPage />} />
          <Route path="/teams/:teamId" element={<TeamDetail />} />
          <Route path="/teams/:teamId/edit" element={<TeamPage />} />
          <Route path="/teams/:teamId/chat" element={<TeamChatLink />} />
          <Route path="/api-keys" element={<ApiKeys />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/automations" element={<Navigate to="/tasks?show=scheduled" replace />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/apps/new" element={<CreateApp />} />
          <Route path="/apps/:appId" element={<AppDetail />} />
          {/* Artifacts live under Apps now (Pages, Images, Docs tabs). */}
          <Route path="/artifacts" element={<Navigate to="/apps?tab=pages" replace />} />
          <Route path="/artifacts/:artifactId" element={<ArtifactPage />} />
          <Route path="/knowledge" element={<Navigate to="/apps?tab=knowledge" replace />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/projects/:projectId/:tab" element={<ProjectDetail />} />
          <Route path="/meetings" element={<Meetings />} />
          <Route path="/board" element={<Board />} />
          <Route path="/payments" element={<Payments />} />
          <Route path="/notifications" element={<Navigate to="/inbox?tab=recent" replace />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/:section" element={<Settings />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

/** Follows the account's theme while signed in (themes made on another device or by an agent). */
function ThemeAccount() {
  const { user } = useUser();
  return <AccountThemeSync signedIn={!!user} />;
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <ThemeProvider>
        <UserProvider>
          <ThemeAccount />
          <SessionProvider>
            <AppRoutes />
          </SessionProvider>
        </UserProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
