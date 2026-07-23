import { createRoadmap, toDateInput } from './roadmap'
import type { AppData, FreelanceService } from '../types'

export const DEFAULT_SERVICES: FreelanceService[] = [
  { id: 'service-python', name: 'Correção de scripts Python', startingPriceUsd: 35, estimatedTime: '1 a 3 horas', scope: 'Correção e melhoria de scripts Python existentes.', included: 'Script corrigido, testado e com instruções.', excluded: 'Reescrita completa de sistemas grandes.', status: 'Pronto' },
  { id: 'service-data', name: 'Automação e processamento de dados', startingPriceUsd: 60, estimatedTime: '2 a 5 horas', scope: 'Rotinas de automação, limpeza e transformação de dados.', included: 'Validação, limpeza, exportação e logs.', excluded: 'Infraestrutura complexa ou operação contínua.', status: 'Pronto' },
  { id: 'service-api', name: 'Integração de APIs e FastAPI', startingPriceUsd: 90, estimatedTime: '4 a 8 horas', scope: 'Integrações e pequenos backends em Python.', included: 'Integração ou pequeno backend documentado.', excluded: 'Sistema completo, alta escala ou segurança crítica.', status: 'Pronto' },
]

export function createDefaultData(startDate = toDateInput(new Date())): AppData {
  return {
    schemaVersion: 1,
    tasks: createRoadmap(startDate), proposals: [], contracts: [], services: DEFAULT_SERVICES.map((item) => ({ ...item })),
    settings: { userName: '', roadmapStartDate: startDate, weeklyGoalUsd: 200, weeklyHours: 10, primaryCurrency: 'USD', defaultExchangeRate: 5.5, theme: 'system', notificationsEnabled: true, confirmBeforeDelete: true },
    savedAt: new Date().toISOString(),
  }
}
