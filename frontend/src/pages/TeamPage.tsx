import { useParams } from 'react-router-dom';

export default function TeamPage() {
  const { teamId } = useParams();
  return (
    <div className="flex-1 p-6">
      <h1 className="text-2xl font-bold">{teamId ? 'Edit Team' : 'New Team'}</h1>
      <p className="text-[var(--fg-2)] mt-4">Team configuration coming soon...</p>
    </div>
  );
}