import { useParams } from 'react-router-dom';

export default function TeamInteraction() {
  const { teamId, taskId, sessionId } = useParams();
  
  return (
    <div className="flex-1 p-6">
      <h1 className="text-2xl font-bold">Team Interaction</h1>
      <p className="text-[var(--fg-2)] mt-4">
        Team: {teamId} | Task: {taskId || 'None'} | Session: {sessionId || 'None'}
      </p>
    </div>
  );
}