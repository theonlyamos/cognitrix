import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { UserProvider, WebSocketProvider, useUser } from '@/context/AppContext';
import Sidebar from '@/components/Sidebar';
import Home from '@/pages/Home';
import Login from '@/pages/Login';
import Signup from '@/pages/Signup';
import Agents from '@/pages/Agents';
import AgentPage from '@/pages/AgentPage';
import Tasks from '@/pages/Tasks';
import TaskPage from '@/pages/TaskPage';
import Teams from '@/pages/Teams';
import TeamPage from '@/pages/TeamPage';
import TeamInteraction from '@/pages/TeamInteraction';
import '@/app.css';

function LoadingScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-900">
      <div className="flex items-center gap-3">
        <svg className="animate-spin h-6 w-6 text-blue-500" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <span className="text-gray-400">Loading...</span>
      </div>
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useUser();
  
  if (isLoading) {
    return <LoadingScreen />;
  }
  
  if (!user) {
    return <Navigate to="/" replace />;
  }
  
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useUser();
  
  if (isLoading) {
    return <LoadingScreen />;
  }
  
  if (user) {
    return <Navigate to="/home" replace />;
  }
  
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />
      <Route 
        path="/home" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <Home />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/agents" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <Agents />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/agents/new" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <AgentPage />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/agents/:agentId" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <AgentPage />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/tasks" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <Tasks />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/tasks/new" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TaskPage />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/tasks/:taskId" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TaskPage />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/teams" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <Teams />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/teams/new" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TeamPage />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/teams/:teamId" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TeamPage />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/teams/:teamId/interact" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TeamInteraction />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/teams/:teamId/tasks/:taskId/interact" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TeamInteraction />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route 
        path="/teams/:teamId/tasks/:taskId/sessions/:sessionId/interact" 
        element={
          <ProtectedRoute>
            <div className="flex h-screen">
              <Sidebar />
              <TeamInteraction />
            </div>
          </ProtectedRoute>
        } 
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <UserProvider>
        <WebSocketProvider>
          <AppRoutes />
        </WebSocketProvider>
      </UserProvider>
    </BrowserRouter>
  );
}