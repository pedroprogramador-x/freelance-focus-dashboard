// Tradução do planejamento comercial para o seed anônimo do Context Registry.
//
// Esta é a **única** ponte entre as duas persistências (ADR-0002): o modelo comercial vive
// em `localStorage`, o Context Registry vive no SQLite do backend, e nada os sincroniza. A
// tradução acontece aqui, no frontend, e é uma **cópia de mão única** — enviada uma vez,
// sob confirmação explícita do usuário.
//
// O backend recebe `snake_case` e não sabe de onde veio (docs/architecture/03 §1).

import type { PlanningImportInput } from '../services/contextApi'
import type { ProjectPlanning } from '../types'

export function toPlanningImport(planning: ProjectPlanning): PlanningImportInput {
  return {
    problem: planning.problem,
    objective: planning.objective,
    functional_requirements: planning.functionalRequirements.filter((item) => item.trim()),
    non_functional_requirements: planning.nonFunctionalRequirements.filter((item) => item.trim()),
    stack: planning.stack.filter((item) => item.trim()),
    architecture: planning.architecture,
    technical_decisions: planning.technicalDecisions
      .filter((item) => item.title.trim())
      .map((item) => ({ title: item.title, decision: item.decision, reason: item.reason })),
    risks: planning.risks
      .filter((item) => item.description.trim())
      .map((item) => ({ description: item.description, mitigation: item.mitigation })),
  }
}

export interface SeedPreviewRow {
  domain: string
  label: string
  count: number
}

// Prévia do que a importação vai criar, seguindo a tabela de docs/architecture/03 §1.
//
// É uma **estimativa** derivada da mesma tabela congelada que o backend implementa: campo
// vazio não vira entrada (o `body` de uma entrada não pode ser vazio), e problema e
// objetivo entram juntos numa única entrada `objective`. A contagem real vem na resposta da
// importação, e é ela que a UI mostra depois de confirmar.
export function describePlanningSeed(seed: PlanningImportInput): SeedPreviewRow[] {
  const filled = (value?: string) => (value ?? '').trim().length > 0
  const nonEmpty = (items?: string[]) => (items ?? []).filter((item) => item.trim()).length > 0

  return [
    {
      domain: 'objective',
      label: 'Objetivo (problema + objetivo)',
      count: filled(seed.problem) || filled(seed.objective) ? 1 : 0,
    },
    {
      domain: 'requirements',
      label: 'Requisitos (funcionais e não funcionais)',
      count:
        (nonEmpty(seed.functional_requirements) ? 1 : 0) +
        (nonEmpty(seed.non_functional_requirements) ? 1 : 0),
    },
    { domain: 'stack', label: 'Stack', count: nonEmpty(seed.stack) ? 1 : 0 },
    { domain: 'architecture', label: 'Arquitetura', count: filled(seed.architecture) ? 1 : 0 },
    {
      domain: 'decisions',
      label: 'Decisões técnicas',
      count: (seed.technical_decisions ?? []).length,
    },
    { domain: 'risks', label: 'Riscos', count: (seed.risks ?? []).length },
  ]
}

export function totalSeedEntries(rows: SeedPreviewRow[]): number {
  return rows.reduce((total, row) => total + row.count, 0)
}
