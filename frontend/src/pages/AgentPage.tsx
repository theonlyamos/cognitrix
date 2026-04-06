import { useParams } from 'react-router-dom';

export default function AgentPage() {
  const { agentId } = useParams();
  return (
    <div className="flex-1 p-6">
      <h1 className="text-2xl font-bold">{agentId ? 'Edit Agent' : 'New Agent'}</h1>
      <p className="text-[var(--fg-2)] mt-4">Agent configuration coming soon...</p>
    </div>
  );
}