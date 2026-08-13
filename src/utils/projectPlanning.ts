import type { ProjectPlanning } from '../types'

const comparablePlanning = (planning: ProjectPlanning) => ({
  problem: planning.problem,
  objective: planning.objective,
  functionalRequirements: planning.functionalRequirements,
  nonFunctionalRequirements: planning.nonFunctionalRequirements,
  stack: planning.stack,
  architecture: planning.architecture,
  technicalDecisions: planning.technicalDecisions.map(({ title, decision, reason }) => ({ title, decision, reason })),
  risks: planning.risks.map(({ description, mitigation }) => ({ description, mitigation })),
})

export const hasUnsavedPlanningChanges = (current: ProjectPlanning, persisted: ProjectPlanning) => JSON.stringify(comparablePlanning(current)) !== JSON.stringify(comparablePlanning(persisted))
