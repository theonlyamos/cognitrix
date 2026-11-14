import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArtifactViewer } from '@/components/artifacts/ArtifactViewer';

/** /artifacts/:artifactId: one artifact on a page of its own. */
export default function ArtifactPage() {
  const { artifactId = '' } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <div className="h-full min-h-0">
      <ArtifactViewer key={artifactId} pageId={artifactId} variant="page" initialThread={params.get('thread')}
        onGone={() => navigate('/apps?tab=pages', { replace: true })} />
    </div>
  );
}
