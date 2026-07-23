export type TaskStatus = 'Pendente' | 'Em andamento' | 'Concluído' | 'Adiado'
export type Priority = 'Alta' | 'Média' | 'Baixa'
export type Theme = 'light' | 'dark' | 'system'

export interface RoadmapTask {
  id: string
  day: number
  plannedDate: string
  week: number
  phase: string
  title: string
  description: string
  estimatedMinutes: number
  priority: Priority
  status: TaskStatus
  notes: string
  rescheduledDate: string | null
  completedAt: string | null
}

export type ProposalStatus = 'Salva' | 'Enviada' | 'Visualizada' | 'Entrevista' | 'Contratado' | 'Recusada' | 'Ignorada'
export type Platform = 'Upwork' | '99Freelas' | 'LinkedIn' | 'Indicação' | 'Outra'

export interface Proposal {
  id: string
  date: string
  platform: Platform
  projectName: string
  clientName: string
  serviceType: string
  budgetUsd: number
  connects: number
  url: string
  status: ProposalStatus
  deadline: string
  estimatedHours: number
  estimatedHourlyRate: number
  nextStep: string
  notes: string
  followUpDate: string
}

export type ContractStatus = 'Em negociação' | 'Em andamento' | 'Entregue' | 'Pausado' | 'Cancelado'
export interface Contract {
  id: string
  project: string
  client: string
  platform: Platform
  service: string
  startDate: string
  deadline: string
  grossUsd: number
  platformFeePercent: number
  exchangeRate: number
  hoursWorked: number
  status: ContractStatus
  rating: number | null
  notes: string
}

export type ServiceStatus = 'Rascunho' | 'Pronto' | 'Publicado' | 'Vendido'
export interface FreelanceService {
  id: string
  name: string
  startingPriceUsd: number
  estimatedTime: string
  scope: string
  included: string
  excluded: string
  status: ServiceStatus
}

export interface Settings {
  userName: string
  roadmapStartDate: string
  weeklyGoalUsd: number
  weeklyHours: number
  primaryCurrency: 'USD' | 'BRL'
  defaultExchangeRate: number
  theme: Theme
  notificationsEnabled: boolean
  confirmBeforeDelete: boolean
}

export interface AppData {
  schemaVersion: 1
  tasks: RoadmapTask[]
  proposals: Proposal[]
  contracts: Contract[]
  services: FreelanceService[]
  settings: Settings
  savedAt: string
}

export interface Backup extends AppData {
  exportedAt: string
  app: 'Freelance Focus'
}
