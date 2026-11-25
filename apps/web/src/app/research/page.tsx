import { Suspense } from 'react';
import { ResearchView } from '@/components/views/ResearchView';

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ResearchView />
    </Suspense>
  );
}
