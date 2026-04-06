import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { UserProvider, useUser } from '@/context/AppContext';
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

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user } = useUser();
  if (!user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  const { user } = useUser();
  
  return (
    <Routes>
      <Route path="/" element={user ? <Navigate to="/home" replace /> : <Login />} />
      <Route path="/signup" element={user ? <Navigate to="/home" replace /> : <Signup />} />
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
        <AppRoutes />
      </UserProvider>
    </BrowserRouter>
  );
}