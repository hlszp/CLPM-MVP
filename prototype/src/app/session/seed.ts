import { currentBatch, evidencePackage, loops } from '../../data/mockData';
import type { UserRole } from '../../types';
import type { WorkflowState } from './types';

export const initialRole: UserRole = 'engineer';

export const initialWorkflowState: WorkflowState = {
  selectedLoopId: loops[0]?.id ?? 'TIC-1115',
  currentSampleId: currentBatch.id,
  currentPackageId: evidencePackage.id,
};
