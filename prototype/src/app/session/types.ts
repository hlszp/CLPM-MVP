import type { EvidencePackage, SampleBatch, UserRole } from '../../types';

export interface WorkflowState {
  selectedLoopId: string;
  currentSampleId: string;
  currentPackageId: string;
}

export interface AppSessionValue {
  role: UserRole;
  defaultRoute: string;
  workflow: WorkflowState;
  currentLoopId: string;
  currentSample?: SampleBatch;
  currentPackage?: EvidencePackage;
  setRole: (role: UserRole) => void;
  selectLoop: (loopId: string) => void;
  setCurrentPackage: (packageId: string) => void;
}
