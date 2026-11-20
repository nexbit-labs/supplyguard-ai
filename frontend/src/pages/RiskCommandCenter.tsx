import { Navigate, useSearchParams } from 'react-router-dom';
import { RiskInbox } from '@/features/risk/inbox/RiskInbox';

export default function RiskCommandCenter() {
  const [params] = useSearchParams();
  const caseId = params.get('case');
  // R-03 supplier screens link here as /risk?case=RC-...
  if (caseId && /^RC-[A-Za-z0-9-]+$/.test(caseId)) {
    return <Navigate replace to={`/risk/${caseId}`} />;
  }
  return <RiskInbox />;
}
