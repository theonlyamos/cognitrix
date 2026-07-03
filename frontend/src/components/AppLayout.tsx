import { Outlet } from 'react-router-dom';
import Sidebar from '@/components/Sidebar';

/** Persistent shell: the Sidebar mounts once; pages swap through the Outlet. */
export default function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-bg text-fg">
      <Sidebar />
      <Outlet />
    </div>
  );
}
