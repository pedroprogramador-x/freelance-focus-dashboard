import type { AppData, Client, ClientSource, ClientStatus, FreelanceService, Project, Proposal } from '../types'

export const CLIENT_STATUSES: ClientStatus[] = ['Lead', 'Em negociação', 'Cliente ativo', 'Cliente inativo']
export const CLIENT_SOURCES: ClientSource[] = ['Indicação', 'WhatsApp', 'Instagram', 'Upwork', '99Freelas', 'LinkedIn', 'Contato direto', 'Outro']

export function filterClients(clients: Client[], search: string, status: string) {
  const term = search.trim().toLocaleLowerCase('pt-BR')
  return clients.filter((client) => (!term || `${client.name} ${client.companyName} ${client.contactName} ${client.phone} ${client.email}`.toLocaleLowerCase('pt-BR').includes(term)) && (!status || client.status === status))
}

export const normalizeClientName = (value: string) => value
  .trim()
  .replace(/\s+/g, ' ')
  .normalize('NFD')
  .replace(/[\u0300-\u036f]/g, '')
  .toLocaleLowerCase('pt-BR') || 'cliente não informado'

export type EntityCollection = 'clients' | 'proposals' | 'projects' | 'services'
export type DomainEntity = Client | Proposal | Project | FreelanceService

export function hasValidEntityReferences(data: AppData, collection: EntityCollection, value: DomainEntity) {
  if (collection === 'proposals') {
    const proposal = value as Proposal
    return data.clients.some((client) => client.id === proposal.clientId)
      && (proposal.serviceId === null || data.services.some((service) => service.id === proposal.serviceId))
      && data.projects.every((project) => project.proposalId !== proposal.id || project.clientId === proposal.clientId)
  }
  if (collection === 'projects') {
    const project = value as Project
    if (!data.clients.some((client) => client.id === project.clientId)) return false
    if (project.proposalId === null) return true
    return data.proposals.some((proposal) => proposal.id === project.proposalId && proposal.clientId === project.clientId)
  }
  return true
}

export function deletionBlockReason(data: AppData, collection: EntityCollection, id: string) {
  if (collection === 'clients' && (data.proposals.some((item) => item.clientId === id) || data.projects.some((item) => item.clientId === id))) return 'Cliente possui proposta ou projeto relacionado.'
  if (collection === 'proposals' && data.projects.some((item) => item.proposalId === id)) return 'Proposta possui projeto relacionado.'
  if (collection === 'services' && data.proposals.some((item) => item.serviceId === id)) return 'Serviço possui proposta relacionada.'
  return null
}
