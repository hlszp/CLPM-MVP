import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { currentBatch, evidencePackage } from '../../data/mockData';
import { getDefaultRouteForRole } from '../../routes/roleAccess';
import type { UserRole } from '../../types';
import { initialPerformanceRankingState, initialRole, initialSampleReadinessState, initialWorkflowState } from './seed';
import { clearLoopSelection, mergePerformanceFilters, toggleLoopSelection } from './performanceRanking';
import { freezeSampleState, setImportMethodState, setReadinessWorkflowState } from './sampleReadiness';
import type { AppSessionValue, PerformanceRankingState, SampleReadinessWorkflow, WorkflowState } from './types';

const AppSessionContext = createContext<AppSessionValue | null>(null);

export function AppSessionProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<UserRole>(initialRole);
  const [workflow, setWorkflow] = useState<WorkflowState>(initialWorkflowState);
  const [sampleReadiness, setSampleReadiness] = useState<SampleReadinessWorkflow>(initialSampleReadinessState);
  const [performanceRanking, setPerformanceRanking] = useState<PerformanceRankingState>(initialPerformanceRankingState);

  const currentSample = workflow.currentSampleId === currentBatch.id ? currentBatch : undefined;
  const currentPackage = workflow.currentPackageId === evidencePackage.id ? evidencePackage : undefined;

  const value = useMemo<AppSessionValue>(
    () => ({
      role,
      defaultRoute: getDefaultRouteForRole(role),
      workflow,
      currentLoopId: workflow.selectedLoopId,
      currentSample,
      currentPackage,
      sampleReadiness,
      performanceRanking,
      setRole: (nextRole) => setRoleState(nextRole),
      selectLoop: (loopId) => setWorkflow((prev) => ({ ...prev, selectedLoopId: loopId })),
      setCurrentPackage: (packageId) => setWorkflow((prev) => ({ ...prev, currentPackageId: packageId })),
      setImportMethod: (method) => setSampleReadiness((prev) => setImportMethodState(prev, method)),
      setReadinessState: (state) => setSampleReadiness((prev) => setReadinessWorkflowState(prev, state)),
      freezeSample: () => setSampleReadiness((prev) => freezeSampleState(prev)),
      setPerformanceFilters: (filters) => setPerformanceRanking((prev) => mergePerformanceFilters(prev, filters)),
      toggleRankedLoopSelection: (loopId) => setPerformanceRanking((prev) => toggleLoopSelection(prev, loopId)),
      clearRankedLoopSelection: () => setPerformanceRanking((prev) => clearLoopSelection(prev)),
    }),
    [currentPackage, currentSample, performanceRanking, role, sampleReadiness, workflow]
  );

  return <AppSessionContext.Provider value={value}>{children}</AppSessionContext.Provider>;
}

export function useAppSession() {
  const context = useContext(AppSessionContext);

  if (!context) {
    throw new Error('useAppSession must be used within AppSessionProvider');
  }

  return context;
}
