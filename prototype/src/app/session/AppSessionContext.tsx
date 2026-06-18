import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { currentBatch, evidencePackage } from '../../data/mockData';
import { getDefaultRouteForRole } from '../../routes/roleAccess';
import type { UserRole } from '../../types';
import { initialRole, initialWorkflowState } from './seed';
import type { AppSessionValue, WorkflowState } from './types';

const AppSessionContext = createContext<AppSessionValue | null>(null);

export function AppSessionProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<UserRole>(initialRole);
  const [workflow, setWorkflow] = useState<WorkflowState>(initialWorkflowState);

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
      setRole: (nextRole) => setRoleState(nextRole),
      selectLoop: (loopId) => setWorkflow((prev) => ({ ...prev, selectedLoopId: loopId })),
      setCurrentPackage: (packageId) => setWorkflow((prev) => ({ ...prev, currentPackageId: packageId })),
    }),
    [currentPackage, currentSample, role, workflow]
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
