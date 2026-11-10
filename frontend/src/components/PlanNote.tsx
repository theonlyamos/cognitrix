import { Link } from 'react-router-dom';
import { CHATGPT_USAGE_URL } from '@/lib/chatgptPlan';

/** Under a provider picker set to the ChatGPT plan: where it's connected and
 *  where its usage is managed. */
export default function PlanNote({ provider }: { provider: string }) {
  if (provider !== 'chatgpt') return null;
  return (
    <p className="text-[12px] font-normal leading-relaxed text-fg-dim">
      Using ChatGPT plan: runs on the ChatGPT account connected in{' '}
      <Link to="/settings/models" className="text-accent-ink hover:underline">Settings › Models and keys</Link>, not an API key.{' '}
      <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Manage usage</a>
    </p>
  );
}
