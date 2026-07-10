import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { UserProvider, useUser } from '@/context/AppContext';
import { SessionProvider } from '@/context/SessionContext';
import { ThemeProvider } from '@/context/ThemeContext';
import AppLayout from '@/components/AppLayout';
import Login from '@/pages/Login';
import Signup from '@/pages/Signup';
import '@/app.css';

// Code-split the authenticated app; auth pages + shell stay eager for first paint.
const Home = lazy(() => import('@/pages/Home'));
const Agents = lazy(() => import('@/pages/Agents'));
const AgentPage = lazy(() => import('@/pages/AgentPage'));
const Tasks = lazy(() => import('@/pages/Tasks'));
const TaskPage = lazy(() => import('@/pages/TaskPage'));
const TaskDetail = lazy(() => import('@/pages/TaskDetail'));
const Teams = lazy(() => import('@/pages/Teams'));
const TeamPage = lazy(() => import('@/pages/TeamPage'));
const TeamInteraction = lazy(() => import('@/pages/TeamInteraction'));
const ApiKeys = lazy(() => import('@/pages/ApiKeys'));

function LoadingScreen() {
  return (
    <div className="flex h-full min-h-[40vh] flex-1 items-center justify-center bg-bg text-fg-dim">
      <div className="flex items-center gap-3 font-mono text-sm">
        <svg className="h-5 w-5 animate-spin text-accent" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" />
          <path className="opacity-90" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" d="M4 12a8 8 0 0 1 8-8" />
        </svg>
        loading…
      </div>
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useUser();
  if (isLoading) return <LoadingScreen />;
  if (!user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useUser();
  if (isLoading) return <LoadingScreen />;
  if (user) return <Navigate to="/home" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <Routes>
        <Route path="/" element={<PublicRoute><Login /></PublicRoute>} />
        <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />

        {/* Persistent shell: Sidebar mounts once, pages render in the Outlet. */}
        <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
          <Route path="/home" element={<Home />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/new" element={<AgentPage />} />
          <Route path="/agents/:agentId" element={<AgentPage />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/tasks/new" element={<TaskPage />} />
          <Route path="/tasks/:taskId" element={<TaskDetail />} />
          <Route path="/tasks/:taskId/edit" element={<TaskPage />} />
          <Route path="/teams" element={<Teams />} />
          <Route path="/teams/new" element={<TeamPage />} />
          <Route path="/teams/:teamId" element={<TeamPage />} />
          <Route path="/api-keys" element={<ApiKeys />} />
          <Route path="/teams/:teamId/interact" element={<TeamInteraction />} />
          <Route path="/teams/:teamId/tasks/:taskId/interact" element={<TeamInteraction />} />
          <Route path="/teams/:teamId/tasks/:taskId/sessions/:sessionId/interact" element={<TeamInteraction />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <ThemeProvider>
        <UserProvider>
          <SessionProvider>
            <AppRoutes />
          </SessionProvider>
        </UserProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
