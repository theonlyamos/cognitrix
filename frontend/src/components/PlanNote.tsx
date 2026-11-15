import { Link } from 'react-router-dom';
import { CHATGPT_USAGE_URL } from '@/lib/chatgptPlan';
import { tr } from '@/lib/i18n';

/** Under a provider picker set to the ChatGPT plan: where it's connected and
 *  where its usage is managed. */
export default function PlanNote({ provider }: { provider: string }) {
  if (provider !== 'chatgpt') return null;
  return (
    <p className="text-[12px] font-normal leading-relaxed text-fg-dim">
      {tr('ui.planNote.usingChatgptPlanRunsOn')}{' '}
      <Link to="/settings/models" className="text-accent-ink hover:underline">{tr('ui.planNote.settingsModelsAndKeys')}</Link>{tr('ui.planNote.notAnApiKey')}{' '}
      <a href={CHATGPT_USAGE_URL} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{tr('ui.planNote.manageUsage')}</a>
    </p>
  );
}
