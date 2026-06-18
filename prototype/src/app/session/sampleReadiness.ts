import type { SampleImportMethod, SampleReadinessState } from '../../types';
import type { SampleReadinessWorkflow } from './types';

export function setImportMethodState(
  current: SampleReadinessWorkflow,
  importMethod: SampleImportMethod
): SampleReadinessWorkflow {
  return {
    ...current,
    importMethod,
    readinessState: current.isFrozen ? current.readinessState : 'importing',
  };
}

export function setReadinessWorkflowState(
  current: SampleReadinessWorkflow,
  readinessState: SampleReadinessState
): SampleReadinessWorkflow {
  return {
    ...current,
    readinessState,
  };
}

export function freezeSampleState(current: SampleReadinessWorkflow): SampleReadinessWorkflow {
  return {
    ...current,
    readinessState: 'frozen',
    isFrozen: true,
  };
}
