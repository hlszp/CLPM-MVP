import { currentBatch, evidencePackage, primaryLoopId } from '../../data/mockData';
import type { UserRole } from '../../types';
import type { WorkflowState } from './types';

export const initialRole: UserRole = 'engineer';

export const initialWorkflowState: WorkflowState = {
  selectedLoopId: primaryLoopId,
  currentSampleId: currentBatch.id,
  currentPackageId: evidencePackage.id,
};
