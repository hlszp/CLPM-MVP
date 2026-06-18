import { currentBatch, evidencePackage, primaryLoopId } from '../../data/mockData';
import type { UserRole } from '../../types';
import type { PerformanceRankingState, SampleReadinessWorkflow, WorkflowState } from './types';

export const initialRole: UserRole = 'engineer';

export const initialWorkflowState: WorkflowState = {
  selectedLoopId: primaryLoopId,
  currentSampleId: currentBatch.id,
  currentPackageId: evidencePackage.id,
};

export const initialSampleReadinessState: SampleReadinessWorkflow = {
  importMethod: 'csv',
  readinessState: 'partial',
  selectedMappingField: 'mode',
  isFrozen: false,
};

export const initialPerformanceRankingState: PerformanceRankingState = {
  filters: {
    risk: 'all',
    status: 'all',
    keyword: '',
    sortBy: 'score',
  },
  selectedLoopIds: [],
};
