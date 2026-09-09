// Gate 8 — a lógica pura da aba de Contexto.
//
// Dois invariantes que não podem estar errados sem alguém perceber:
//
// 1. `stale` **nunca** é mostrado sem o motivo — os dois motivos pedem ações diferentes;
// 2. o corpo do `PATCH` só carrega o que mudou. Enviar `source_refs` sempre faria o backend
//    reconfirmar a linha de base a cada correção de digitação, e uma entrada desatualizada
//    "curaria" sozinha ao ser editada (docs/architecture/03 §3).

import { describe, expect, it } from 'vitest'
import {
  buildEntryPatch,
  emptyEntryForm,
  entryFormOf,
  isFormDirty,
  messageOf,
  parseStructured,
  stateLabel,
  stateTone,
  type EntryForm,
} from '../utils/contextEntries'
import { WorkspaceApiError } from '../services/workspaceApi'
import type { ContextEntry } from '../services/contextApi'
import { describePlanningSeed, toPlanningImport, totalSeedEntries } from '../utils/planningSeed'
import type { ProjectPlanning } from '../types'

const entryFixture = (over: Partial<ContextEntry> = {}): ContextEntry => ({
  id: 'e1',
  workspace_id: 'w1',
  domain: 'modules',
  title: 'Módulo src',
  body: 'Descreve o código sob src/.',
  structured: null,
  tags: ['backend'],
  source_refs: ['src/**'],
  content_hash: 'c'.repeat(64),
  source_hash: 's'.repeat(64),
  source_hash_commit: 'a'.repeat(40),
  state: 'fresh',
  stale_reason: null,
  origin: 'manual',
  last_verified_at: null,
  last_verified_commit: null,
  created_at: '2026-09-05T10:00:00+00:00',
  updated_at: '2026-09-05T10:00:00+00:00',
  ...over,
})

describe('indicador de estado', () => {
  it('distingue os quatro estados, e nunca mostra stale sem motivo', () => {
    expect(stateLabel({ state: 'fresh', stale_reason: null })).toBe('Atualizada')
    expect(stateLabel({ state: 'unknown', stale_reason: null })).toBe('Não verificável')
    expect(stateLabel({ state: 'stale', stale_reason: 'sources_changed' })).toBe(
      'Desatualizada · o código mudou',
    )
    expect(stateLabel({ state: 'stale', stale_reason: 'working_tree' })).toBe(
      'Desatualizada · trabalho não commitado',
    )
  })

  it('dá um tom visual distinto a cada um dos quatro', () => {
    const tons = [
      stateTone({ state: 'fresh', stale_reason: null }),
      stateTone({ state: 'unknown', stale_reason: null }),
      stateTone({ state: 'stale', stale_reason: 'sources_changed' }),
      stateTone({ state: 'stale', stale_reason: 'working_tree' }),
    ]
    expect(new Set(tons).size).toBe(4)
  })
})

describe('corpo do PATCH', () => {
  const baseline = entryFormOf(entryFixture())

  it('omite source_refs quando só o conteúdo mudou — o baseline fica intacto', () => {
    const form: EntryForm = { ...baseline, title: 'Outro título', body: 'Outro corpo' }

    const patch = buildEntryPatch(form, baseline, null)

    expect(patch).toEqual({ title: 'Outro título', body: 'Outro corpo' })
    expect('source_refs' in patch).toBe(false)
  })

  it('omite source_refs até quando a lista é reordenada para o mesmo conteúdo', () => {
    const form: EntryForm = { ...baseline, sourceRefs: ['src/**'] }
    expect('source_refs' in buildEntryPatch(form, baseline, null)).toBe(false)
  })

  it('inclui source_refs quando a lista realmente muda', () => {
    const form: EntryForm = { ...baseline, sourceRefs: ['src/**', 'docs/*.md'] }

    expect(buildEntryPatch(form, baseline, null)).toEqual({
      source_refs: ['src/**', 'docs/*.md'],
    })
  })

  it('inclui uma lista vazia — que é o pedido explícito de zerar o baseline', () => {
    const form: EntryForm = { ...baseline, sourceRefs: [] }

    const patch = buildEntryPatch(form, baseline, null)

    expect(patch.source_refs).toEqual([])
    expect('source_refs' in patch).toBe(true)
  })

  it('não envia nada quando nada mudou', () => {
    expect(buildEntryPatch({ ...baseline }, baseline, null)).toEqual({})
  })

  it('envia structured nulo quando o campo foi esvaziado', () => {
    const comStruct = entryFormOf(entryFixture({ structured: { mitigation: 'x' } }))
    const form: EntryForm = { ...comStruct, structuredText: '' }

    expect(buildEntryPatch(form, comStruct, null)).toEqual({ structured: null })
  })
})

describe('alterações não salvas', () => {
  it('detecta mudança em qualquer campo do formulário', () => {
    const baseline = entryFormOf(entryFixture())
    expect(isFormDirty(baseline, baseline)).toBe(false)
    expect(isFormDirty({ ...baseline, title: 'x' }, baseline)).toBe(true)
    expect(isFormDirty({ ...baseline, body: 'x' }, baseline)).toBe(true)
    expect(isFormDirty({ ...baseline, tags: [] }, baseline)).toBe(true)
    expect(isFormDirty({ ...baseline, sourceRefs: ['outro'] }, baseline)).toBe(true)
    expect(isFormDirty({ ...baseline, structuredText: '{}' }, baseline)).toBe(true)
    expect(isFormDirty({ ...baseline, domain: 'risks' }, baseline)).toBe(true)
  })

  it('formulário novo nasce vazio e limpo', () => {
    const vazio = emptyEntryForm()
    expect(isFormDirty(vazio, emptyEntryForm())).toBe(false)
    expect(vazio.sourceRefs).toEqual([])
  })
})

describe('campo estruturado', () => {
  it('aceita objeto e branco, e recusa o resto', () => {
    expect(parseStructured('')).toEqual({ ok: true, value: null })
    expect(parseStructured('   ')).toEqual({ ok: true, value: null })
    expect(parseStructured('{"mitigation": "x"}')).toEqual({ ok: true, value: { mitigation: 'x' } })
    expect(parseStructured('[1,2]').ok).toBe(false)
    expect(parseStructured('"texto"').ok).toBe(false)
    expect(parseStructured('null').ok).toBe(false)
    expect(parseStructured('{quebrado').ok).toBe(false)
  })
})

describe('mensagem de erro', () => {
  it('prefere a mensagem do backend', () => {
    expect(messageOf(new WorkspaceApiError('invalid_source_refs', 'source_ref aponta para segredo'))).toBe(
      'source_ref aponta para segredo',
    )
    expect(messageOf(new Error('boom'))).toBe('boom')
    expect(messageOf('qualquer coisa')).toBe('Erro inesperado.')
  })
})

// ------------------------------------------------------------------------ seed de import

const planningFixture = (over: Partial<ProjectPlanning> = {}): ProjectPlanning => ({
  id: 'p1',
  projectId: 'proj1',
  problem: 'Clientes perdem prazo.',
  objective: 'Painel único.',
  functionalRequirements: ['Cadastrar cliente', '   '],
  nonFunctionalRequirements: ['Offline'],
  stack: ['React', ''],
  architecture: 'SPA + backend local.',
  technicalDecisions: [
    { id: 'd1', title: 'Usar SQLite', decision: 'Arquivo local', reason: 'Zero setup' },
    { id: 'd2', title: '  ', decision: '', reason: '' },
  ],
  risks: [
    { id: 'r1', description: 'OneDrive corrompe o banco', mitigation: 'data_dir fora' },
    { id: 'r2', description: '  ', mitigation: '' },
  ],
  createdAt: '2026-09-05T10:00:00+00:00',
  updatedAt: '2026-09-05T10:00:00+00:00',
  ...over,
})

describe('tradução do planejamento para o seed', () => {
  it('converte para snake_case e descarta itens em branco', () => {
    const seed = toPlanningImport(planningFixture())

    expect(seed).toEqual({
      problem: 'Clientes perdem prazo.',
      objective: 'Painel único.',
      functional_requirements: ['Cadastrar cliente'],
      non_functional_requirements: ['Offline'],
      stack: ['React'],
      architecture: 'SPA + backend local.',
      technical_decisions: [
        { title: 'Usar SQLite', decision: 'Arquivo local', reason: 'Zero setup' },
      ],
      risks: [{ description: 'OneDrive corrompe o banco', mitigation: 'data_dir fora' }],
    })
  })

  it('o payload não carrega nenhum identificador do domínio comercial', () => {
    // ADR-0002: o backend não sabe de onde veio. Nem `id`, nem `projectId`, nem timestamps
    // do modelo comercial atravessam a fronteira.
    const seed = toPlanningImport(planningFixture())
    const serializado = JSON.stringify(seed)

    expect(serializado).not.toContain('projectId')
    expect(serializado).not.toContain('proj1')
    expect(serializado).not.toContain('createdAt')
    expect(seed.technical_decisions?.[0]).not.toHaveProperty('id')
    expect(seed.risks?.[0]).not.toHaveProperty('id')
  })
})

describe('prévia da importação', () => {
  it('conta as entradas conforme a tabela de docs/architecture/03 §1', () => {
    const rows = describePlanningSeed(toPlanningImport(planningFixture()))

    expect(Object.fromEntries(rows.map((row) => [row.domain, row.count]))).toEqual({
      objective: 1,
      requirements: 2,
      stack: 1,
      architecture: 1,
      decisions: 1,
      risks: 1,
    })
    expect(totalSeedEntries(rows)).toBe(7)
  })

  it('problema e objetivo entram numa entrada só', () => {
    const soProblema = describePlanningSeed({ problem: 'x' })
    const ambos = describePlanningSeed({ problem: 'x', objective: 'y' })

    expect(soProblema.find((row) => row.domain === 'objective')?.count).toBe(1)
    expect(ambos.find((row) => row.domain === 'objective')?.count).toBe(1)
  })

  it('campo vazio não vira entrada', () => {
    const rows = describePlanningSeed({})
    expect(totalSeedEntries(rows)).toBe(0)

    const soLixo = describePlanningSeed({ stack: ['  ', ''], architecture: '   ' })
    expect(totalSeedEntries(soLixo)).toBe(0)
  })
})
