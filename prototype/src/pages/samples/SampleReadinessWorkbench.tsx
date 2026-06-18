import { useAppSession } from '../../app/session/AppSessionContext';
import { SampleFreezePanel } from './SampleFreezePanel';
import { SampleValidationPanel } from './SampleValidationPanel';

export function SampleReadinessWorkbench() {
  const { sampleReadiness, freezeSample } = useAppSession();

  return (
    <section className="sample-readiness-workbench">
      <SampleValidationPanel readinessState={sampleReadiness.readinessState} />
      <SampleFreezePanel
        readinessState={sampleReadiness.readinessState}
        isFrozen={sampleReadiness.isFrozen}
        onFreeze={freezeSample}
      />
    </section>
  );
}
