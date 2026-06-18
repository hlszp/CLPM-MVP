import type {
  EvidencePackage,
  SampleBatch,
  SampleImportMethod,
  SampleReadinessState,
  UserRole,
} from '../../types';

export interface WorkflowState {
  selectedLoopId: string;
  currentSampleId: string;
  currentPackageId: string;
}

export interface SampleReadinessWorkflow {
  importMethod: SampleImportMethod;
  readinessState: SampleReadinessState;
  selectedMappingField: string;
  isFrozen: boolean;
}

export interface AppSessionValue {
  role: UserRole;
  defaultRoute: string;
  workflow: WorkflowState;
  currentLoopId: string;
  currentSample?: SampleBatch;
  currentPackage?: EvidencePackage;
  sampleReadiness: SampleReadinessWorkflow;
  setRole: (role: UserRole) => void;
  selectLoop: (loopId: string) => void;
  setCurrentPackage: (packageId: string) => void;
  setImportMethod: (method: SampleImportMethod) => void;
  setReadinessState: (state: SampleReadinessState) => void;
  freezeSample: () => void;
}
