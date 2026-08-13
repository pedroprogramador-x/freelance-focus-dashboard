export type TaskStatus = 'Pendente' | 'Em andamento' | 'Concluído' | 'Adiado'
export type Priority = 'Alta' | 'Média' | 'Baixa'
export type Theme = 'light' | 'dark' | 'system'
export type Currency = 'BRL' | 'USD'

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

export type ClientStatus = 'Lead' | 'Em negociação' | 'Cliente ativo' | 'Cliente inativo'
export type ClientSource = 'Indicação' | 'WhatsApp' | 'Instagram' | 'Upwork' | '99Freelas' | 'LinkedIn' | 'Contato direto' | 'Outro'

export interface Client {
  id: string
  name: string
  companyName: string
  contactName: string
  phone: string
  email: string
  source: ClientSource
  referredBy: string
  status: ClientStatus
  notes: string
  createdAt: string
  updatedAt: string
}

export type ProposalStatus = 'Rascunho' | 'Enviada' | 'Aguardando resposta' | 'Aceita' | 'Recusada' | 'Expirada'

export interface ProposalPlatformData {
  url?: string
  connects?: number
  platformFeePercent?: number
}

export interface Proposal {
  id: string
  clientId: string
  serviceId: string | null
  title: string
  description: string
  amount: number
  currency: Currency
  source: ClientSource
  status: ProposalStatus
  createdAt: string
  sentAt: string | null
  validUntil: string | null
  followUpDate: string | null
  estimatedHours: number
  notes: string
  platformData?: ProposalPlatformData
}

export type ProjectStatus = 'Planejamento' | 'Em desenvolvimento' | 'Aguardando cliente' | 'Em revisão' | 'Entregue' | 'Pausado' | 'Cancelado'
export type PaymentStatus = 'Pendente' | 'Parcial' | 'Pago'

export interface Project {
  id: string
  clientId: string
  proposalId: string | null
  name: string
  description: string
  status: ProjectStatus
  startDate: string | null
  deadline: string | null
  completedAt: string | null
  amount: number
  currency: Currency
  /** Taxa percentual informada pela plataforma; não altera o valor contratado. */
  platformFeePercent?: number | null
  /** Cotação histórica BRL por USD, quando registrada; não implica conversão automática. */
  exchangeRateToBrl?: number | null
  estimatedHours: number
  workedHours: number
  repositoryUrl: string
  productionUrl: string
  paymentStatus: PaymentStatus
  amountReceived: number
  notes: string
  createdAt: string
  updatedAt: string
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
  primaryCurrency: Currency
  defaultExchangeRate: number
  theme: Theme
  notificationsEnabled: boolean
  confirmBeforeDelete: boolean
}

export interface AppData {
  schemaVersion: 2
  clients: Client[]
  proposals: Proposal[]
  projects: Project[]
  services: FreelanceService[]
  tasks: RoadmapTask[]
  settings: Settings
  savedAt: string
}

export interface Backup extends AppData {
  exportedAt: string
  app: 'Freelance Focus'
}
