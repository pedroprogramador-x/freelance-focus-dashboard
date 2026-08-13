import { describe, expect, it } from 'vitest'
import { createDefaultData } from '../data/defaults'
import { deletionBlockReason, hasValidEntityReferences, normalizeClientName } from '../data/domain'
import type { Client, Project, Proposal } from '../types'

const client: Client = { id: 'client-1', name: 'Clínica Vida', companyName: '', contactName: '', phone: '', email: '', source: 'Contato direto', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }
const proposal: Proposal = { id: 'proposal-1', clientId: client.id, serviceId: 'service-api', title: 'API', description: 'Integração', amount: 1000, currency: 'USD', source: 'Upwork', status: 'Aceita', createdAt: '2026-08-13', sentAt: '2026-08-13', validUntil: null, followUpDate: null, estimatedHours: 20, notes: '' }
const project: Project = { id: 'project-1', clientId: client.id, proposalId: proposal.id, name: 'API', description: 'Integração', status: 'Planejamento', startDate: null, deadline: null, completedAt: null, amount: 1000, currency: 'USD', estimatedHours: 20, workedHours: 0, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente', amountReceived: 0, notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }

const dataWithRelations = () => {
  const data = createDefaultData('2026-08-13')
  data.clients = [client]
  data.proposals = [proposal]
  data.projects = [project]
  return data
}

describe('integridade referencial do domínio', () => {
  it('valida referências na criação e edição de propostas e projetos', () => {
    const data = dataWithRelations()
    expect(hasValidEntityReferences(data, 'proposals', proposal)).toBe(true)
    expect(hasValidEntityReferences(data, 'proposals', { ...proposal, clientId: 'missing' })).toBe(false)
    expect(hasValidEntityReferences(data, 'proposals', { ...proposal, serviceId: 'missing' })).toBe(false)
    data.clients.push({ ...client, id: 'client-2' })
    expect(hasValidEntityReferences(data, 'proposals', { ...proposal, clientId: 'client-2' })).toBe(false)
    expect(hasValidEntityReferences(data, 'projects', project)).toBe(true)
    expect(hasValidEntityReferences(data, 'projects', { ...project, clientId: 'missing' })).toBe(false)
    expect(hasValidEntityReferences(data, 'projects', { ...project, proposalId: 'missing' })).toBe(false)
    expect(hasValidEntityReferences(data, 'projects', { ...project, clientId: 'other' })).toBe(false)
  })

  it('bloqueia exclusões que criariam órfãos e não exige cascade', () => {
    const data = dataWithRelations()
    expect(deletionBlockReason(data, 'clients', client.id)).toContain('relacionado')
    expect(deletionBlockReason(data, 'proposals', proposal.id)).toContain('projeto')
    expect(deletionBlockReason(data, 'services', proposal.serviceId!)).toContain('proposta')
    expect(deletionBlockReason(data, 'projects', project.id)).toBeNull()
  })

  it('normaliza apenas espaços, acentos e caixa para deduplicação', () => {
    const normalized = ['Clínica Vida', 'clinica vida', ' Clínica   Vida ', 'CLÍNICA VIDA'].map(normalizeClientName)
    expect(new Set(normalized).size).toBe(1)
    expect(normalizeClientName('Clínica Vida Norte')).not.toBe(normalized[0])
    expect(normalizeClientName('Clínica Viva')).not.toBe(normalized[0])
  })
})
