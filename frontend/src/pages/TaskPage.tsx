import { useParams } from 'react-router-dom';

export default function TaskPage() {
  const { taskId } = useParams();
  return (
    <div className="flex-1 p-6">
      <h1 className="text-2xl font-bold">{taskId ? 'Edit Task' : 'New Task'}</h1>
      <p className="text-[var(--fg-2)] mt-4">Task configuration coming soon...</p>
    </div>
  );
}