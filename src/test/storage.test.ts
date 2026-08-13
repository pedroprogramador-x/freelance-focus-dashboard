import { beforeEach, describe, expect, it } from 'vitest'
import { createDefaultData } from '../data/defaults'
import { exportBackup, isLocalDate, isValidAppData, LEGACY_STORAGE_KEY, loadData, migrateData, parseBackup, saveData, STORAGE_KEY } from '../services/storage'

function createV1() {
  const current = createDefaultData('2026-03-01')
  return {
    schemaVersion: 1,
    tasks: current.tasks,
    proposals: [
      { id: 'proposal-1', date: '2026-03-02', platform: 'Upwork', projectName: 'API', clientName: ' ACME   Ltda ', serviceType: 'Integração de APIs e FastAPI', budgetUsd: 1000, connects: 12, url: 'https://example.com/job', status: 'Contratado', deadline: '2026-03-30', estimatedHours: 20, estimatedHourlyRate: 50, nextStep: 'Iniciar', notes: 'Escopo aprovado', followUpDate: '' },
      { id: 'proposal-2', date: '2026-03-03', platform: '99Freelas', projectName: 'Dados', clientName: 'acme ltda', serviceType: 'Dados', budgetUsd: 500, connects: 0, url: '', status: 'Enviada', deadline: '', estimatedHours: 10, estimatedHourlyRate: 50, nextStep: 'Aguardar', notes: '', followUpDate: '2026-03-10' },
      { id: 'proposal-3', date: '2026-03-04', platform: 'LinkedIn', projectName: 'Site', clientName: 'Acme Holdings', serviceType: 'Site', budgetUsd: 300, connects: 0, url: '', status: 'Recusada', deadline: '', estimatedHours: 5, estimatedHourlyRate: 60, nextStep: '', notes: '', followUpDate: '' },
    ],
    contracts: [
      { id: 'contract-1', project: 'API', client: 'ACME LTDA', platform: 'Upwork', service: 'FastAPI', startDate: '2026-03-05', deadline: '2026-03-25', grossUsd: 1000, platformFeePercent: 10, exchangeRate: 5.5, hoursWorked: 18, status: 'Entregue', rating: 5, notes: 'Cliente satisfeito' },
    ],
    services: current.services,
    settings: { ...current.settings, userName: 'Pedro' },
    savedAt: '2026-04-01T10:00:00.000Z',
  }
}

describe('persistência, migração e backup V2', () => {
  beforeEach(() => localStorage.clear())

  it('salva e carrega schema V2 usando a chave atual', () => {
    const data = createDefaultData('2026-03-01')
    data.settings.userName = 'Pedro'
    saveData(data)
    expect(localStorage.length).toBe(1)
    expect(localStorage.getItem(STORAGE_KEY)).toBeTruthy()
    expect(loadData().settings.userName).toBe('Pedro')
  })

  it('se recupera de JSON corrompido e de estruturas internas inválidas', () => {
    localStorage.setItem(STORAGE_KEY, '{dados quebrados')
    expect(loadData().tasks).toHaveLength(90)
    const corrupted = createDefaultData('2026-03-01')
    corrupted.tasks[0] = null as never
    localStorage.setItem(STORAGE_KEY, JSON.stringify(corrupted))
    expect(loadData().tasks[0].id).toBe('task-01')
  })

  it('migra V1 válido para V2 válido e preserva roadmap, settings e serviços', () => {
    const legacy = createV1()
    const migrated = migrateData(legacy)
    expect(migrated?.schemaVersion).toBe(2)
    expect(isValidAppData(migrated)).toBe(true)
    expect(migrated?.tasks).toEqual(legacy.tasks)
    expect(migrated?.settings).toEqual(legacy.settings)
    expect(migrated?.services).toEqual(legacy.services)
  })

  it('deduplica somente nomes normalizados e liga propostas ao cliente estável', () => {
    const migrated = migrateData(createV1())!
    expect(migrated.clients).toHaveLength(2)
    expect(migrated.clients[0].name).toBe('ACME Ltda')
    expect(migrated.proposals[0].clientId).toBe(migrated.proposals[1].clientId)
    expect(migrated.proposals[2].clientId).not.toBe(migrated.proposals[0].clientId)
    expect(migrateData(createV1())?.clients.map((client) => client.id)).toEqual(migrated.clients.map((client) => client.id))
  })

  it('migra propostas com moeda, origem, status, serviço e dados opcionais', () => {
    const proposal = migrateData(createV1())!.proposals[0]
    expect(proposal).toMatchObject({ id: 'proposal-1', title: 'API', amount: 1000, currency: 'USD', source: 'Upwork', status: 'Aceita', serviceId: 'service-api', platformData: { connects: 12, url: 'https://example.com/job' } })
    expect(proposal.notes).toContain('Próximo passo no V1: Iniciar')
  })

  it('migra contratos usando o bruto contratado e sem inventar recebimento ou conclusão', () => {
    const project = migrateData(createV1())!.projects[0]
    expect(project).toMatchObject({ id: 'contract-1', proposalId: 'proposal-1', name: 'API', status: 'Entregue', amount: 1000, currency: 'USD', platformFeePercent: 10, exchangeRateToBrl: 5.5, workedHours: 18, paymentStatus: 'Pendente', amountReceived: 0, completedAt: null })
    expect(project.notes).toContain('Valor bruto: USD 1000.00')
    expect(project.notes).toContain('Avaliação: 5/5')
    expect(project.notes).toContain('não registrava pagamentos')
  })

  it('repara uma V2 já migrada pela regra antiga sem afetar projetos nativos', () => {
    const current = migrateData(createV1())!
    current.projects[0] = { ...current.projects[0], amount: 900, amountReceived: 900, paymentStatus: 'Pago', completedAt: '2026-03-25', platformFeePercent: undefined, exchangeRateToBrl: undefined }
    current.projects.push({ ...current.projects[0], id: 'native', notes: 'Projeto criado manualmente', amount: 90, amountReceived: 0, paymentStatus: 'Pendente', completedAt: null, platformFeePercent: undefined, exchangeRateToBrl: undefined })
    const repaired = migrateData(current)!
    expect(repaired.projects[0]).toMatchObject({ amount: 1000, amountReceived: 0, paymentStatus: 'Pendente', completedAt: null, platformFeePercent: 10, exchangeRateToBrl: 5.5 })
    expect(repaired.projects[1].amount).toBe(90)
  })

  it('carrega automaticamente a chave V1 e rejeita dados antigos inválidos', () => {
    localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify(createV1()))
    expect(loadData().projects).toHaveLength(1)
    const invalid = createV1()
    invalid.proposals[0].budgetUsd = -1
    expect(migrateData(invalid)).toBeNull()
  })

  it('valida valores e coerência de pagamento em V2', () => {
    const invalid = createDefaultData('2026-03-01')
    invalid.clients.push({ id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Outro', referredBy: '', status: 'Lead', notes: '', createdAt: '2026-03-01', updatedAt: '2026-03-01' })
    invalid.projects.push({ id: 'project-1', clientId: 'client-1', proposalId: null, name: 'API', description: '', status: 'Planejamento', startDate: null, deadline: null, completedAt: null, amount: 100, currency: 'BRL', estimatedHours: 0, workedHours: 0, repositoryUrl: '', productionUrl: '', paymentStatus: 'Parcial', amountReceived: 120, notes: '', createdAt: '2026-03-01', updatedAt: '2026-03-01' })
    expect(isValidAppData(invalid)).toBe(false)
  })

  it.each(['BRL', 'USD'] as const)('aceita pagamentos coerentes em %s e exige pendente para valor zero', (currency) => {
    const data = createDefaultData('2026-03-01')
    data.clients.push({ id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Outro', referredBy: '', status: 'Lead', notes: '', createdAt: '2026-03-01', updatedAt: '2026-03-01' })
    const base = { id: 'project-1', clientId: 'client-1', proposalId: null, name: 'API', description: '', status: 'Planejamento' as const, startDate: null, deadline: null, completedAt: null, amount: 100, currency, estimatedHours: 0, workedHours: 0, repositoryUrl: '', productionUrl: '', notes: '', createdAt: '2026-03-01', updatedAt: '2026-03-01' }
    data.projects = [{ ...base, paymentStatus: 'Pendente', amountReceived: 0 }, { ...base, id: 'partial', paymentStatus: 'Parcial', amountReceived: 50 }, { ...base, id: 'paid', paymentStatus: 'Pago', amountReceived: 100 }]
    expect(isValidAppData(data)).toBe(true)
    data.projects[0] = { ...base, amount: 0, paymentStatus: 'Pago', amountReceived: 0 }
    expect(isValidAppData(data)).toBe(false)
    data.projects[0] = { ...base, amount: 0, paymentStatus: 'Pendente', amountReceived: 0 }
    expect(isValidAppData(data)).toBe(true)
  })

  it('valida datas locais reais sem converter o dia para UTC', () => {
    expect(isLocalDate('2026-08-13')).toBe(true)
    expect(isLocalDate('2026-02-29')).toBe(false)
    expect(isLocalDate('2024-02-29')).toBe(true)
    expect(isLocalDate('2026-08-13T00:00:00.000Z')).toBe(false)
    const invalid = createDefaultData('2026-08-13')
    invalid.tasks[0].plannedDate = '2026-02-30'
    expect(isValidAppData(invalid)).toBe(false)
  })

  it('exporta V2, importa V1 com migração e não altera estado ao rejeitar backup', () => {
    const current = createDefaultData('2026-03-01')
    const before = JSON.stringify(current)
    expect(parseBackup(JSON.stringify(exportBackup(current))).schemaVersion).toBe(2)
    expect(parseBackup(JSON.stringify(createV1())).projects).toHaveLength(1)
    expect(() => parseBackup('{"qualquer":true}')).toThrow('backup V1 ou V2 válido')
    expect(JSON.stringify(current)).toBe(before)
  })

  it('rejeita JSON inválido, schema desconhecido, arrays ausentes e tipos incorretos', () => {
    const current = createDefaultData('2026-03-01')
    expect(() => parseBackup('{')).toThrow('JSON válido')
    expect(() => parseBackup(JSON.stringify({ ...current, schemaVersion: 99 }))).toThrow('backup V1 ou V2 válido')
    const withoutClients: Partial<typeof current> = { ...current }
    delete withoutClients.clients
    expect(() => parseBackup(JSON.stringify(withoutClients))).toThrow('backup V1 ou V2 válido')
    expect(() => parseBackup(JSON.stringify({ ...current, projects: 'incorreto' }))).toThrow('backup V1 ou V2 válido')
  })

  it('rejeita referências quebradas de cliente, serviço e proposta', () => {
    const data = migrateData(createV1())!
    const cases = [
      { ...data, proposals: data.proposals.map((item, index) => index ? item : { ...item, clientId: 'missing' }) },
      { ...data, proposals: data.proposals.map((item, index) => index ? item : { ...item, serviceId: 'missing' }) },
      { ...data, projects: data.projects.map((item) => ({ ...item, clientId: 'missing' })) },
      { ...data, projects: data.projects.map((item) => ({ ...item, proposalId: 'missing' })) },
    ]
    for (const invalid of cases) expect(() => parseBackup(JSON.stringify(invalid))).toThrow('backup V1 ou V2 válido')
  })

  it('deduplica acentos, caixa e espaços, sem unir nomes semanticamente diferentes', () => {
    const legacy = createV1()
    legacy.proposals = ['Clínica Vida', 'clinica vida', '  Clínica   Vida  ', 'CLÍNICA VIDA', 'Clínica Vida Norte', 'Clínica Viva'].map((clientName, index) => ({ ...legacy.proposals[0], id: `clinic-${index}`, projectName: `Projeto ${index}`, clientName }))
    legacy.contracts = []
    const migrated = migrateData(legacy)!
    expect(migrated.clients).toHaveLength(3)
    expect(new Set(migrated.proposals.slice(0, 4).map((item) => item.clientId)).size).toBe(1)
    expect(migrated.proposals[4].clientId).not.toBe(migrated.proposals[0].clientId)
    expect(migrated.proposals[5].clientId).not.toBe(migrated.proposals[0].clientId)
  })
})
