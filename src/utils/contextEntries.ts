// Lógica pura da aba de Contexto — sem React, sem rede, testável isoladamente.
//
// O que mora aqui é justamente o que não pode estar errado sem que alguém perceba:
// como cada um dos quatro estados de docs/architecture/03 §3 é apresentado, e como o
// `PATCH` distingue "campo ausente" de "campo informado".

import type { ContextEntry, ContextEntryUpdateInput, ContextDomain } from '../services/contextApi'
import { WorkspaceApiError } from '../services/workspaceApi'

export function messageOf(error: unknown): string {
  if (error instanceof WorkspaceApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Erro inesperado.'
}

type EntryStatus = Pick<ContextEntry, 'state' | 'stale_reason'>

// `stale` **nunca** aparece sem o motivo. "Desatualizada" sozinha não diz se o código
// commitado mudou (reapontar as fontes resolve) ou se há trabalho não commitado (commitar,
// ou aceitar que a execução usa o commit congelado) — são ações diferentes.
export function stateLabel(entry: EntryStatus): string {
  if (entry.state === 'fresh') return 'Atualizada'
  if (entry.state === 'unknown') return 'Não verificável'
  return entry.stale_reason === 'working_tree'
    ? 'Desatualizada · trabalho não commitado'
    : 'Desatualizada · o código mudou'
}

export function stateTone(entry: EntryStatus): string {
  if (entry.state === 'fresh') return 'fresh'
  if (entry.state === 'unknown') return 'unknown'
  return entry.stale_reason === 'working_tree' ? 'stale-working-tree' : 'stale-sources'
}

export function stateHint(entry: EntryStatus): string {
  if (entry.state === 'fresh') return 'As fontes conferem com a linha de base confirmada.'
  if (entry.state === 'unknown') {
    return 'A entrada declara depender de código e a verificação não foi possível: o workspace pode não ser um repositório Git, não ter commits, ou os caminhos não resolverem para nenhum arquivo.'
  }
  if (entry.stale_reason === 'working_tree') {
    return 'Há alteração não commitada em um caminho coberto. A execução usa o commit congelado, então essa alteração não entraria automaticamente.'
  }
  return 'O código commitado mudou desde a linha de base. Reapontar as fontes reconfirma a base; verificar de novo, sozinho, não muda nada.'
}

export interface EntryForm {
  domain: ContextDomain
  title: string
  body: string
  tags: string[]
  sourceRefs: string[]
  structuredText: string
}

export const emptyEntryForm = (): EntryForm => ({
  domain: 'modules',
  title: '',
  body: '',
  tags: [],
  sourceRefs: [],
  structuredText: '',
})

export const entryFormOf = (entry: ContextEntry): EntryForm => ({
  domain: entry.domain,
  title: entry.title,
  body: entry.body,
  tags: [...entry.tags],
  sourceRefs: [...entry.source_refs],
  structuredText: entry.structured ? JSON.stringify(entry.structured, null, 2) : '',
})

export const sameList = (a: string[], b: string[]) =>
  a.length === b.length && a.every((item, index) => item === b[index])

export function isFormDirty(form: EntryForm, baseline: EntryForm): boolean {
  return (
    form.domain !== baseline.domain ||
    form.title !== baseline.title ||
    form.body !== baseline.body ||
    form.structuredText !== baseline.structuredText ||
    !sameList(form.tags, baseline.tags) ||
    !sameList(form.sourceRefs, baseline.sourceRefs)
  )
}

export type StructuredParse =
  | { ok: true; value: Record<string, unknown> | null }
  | { ok: false; error: string }

export function parseStructured(text: string): StructuredParse {
  const trimmed = text.trim()
  if (!trimmed) return { ok: true, value: null }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, error: 'O campo estruturado precisa ser um objeto JSON.' }
    }
    return { ok: true, value: parsed as Record<string, unknown> }
  } catch {
    return { ok: false, error: 'JSON inválido no campo estruturado.' }
  }
}

// Corpo do `PATCH`: **só o que mudou**.
//
// É aqui que a regra do baseline vive do lado do cliente (docs/architecture/03 §3). Enviar
// `source_refs` sempre — mesmo idêntico — faria o backend reconfirmar a linha de base a
// cada correção de digitação no corpo, e uma entrada `stale` "curaria" sozinha ao ser
// editada. Omitir o campo é o que preserva o baseline; mandá-lo é um ato deliberado.
export function buildEntryPatch(
  form: EntryForm,
  baseline: EntryForm,
  structured: Record<string, unknown> | null,
): ContextEntryUpdateInput {
  const patch: ContextEntryUpdateInput = {}
  if (form.title !== baseline.title) patch.title = form.title
  if (form.body !== baseline.body) patch.body = form.body
  if (form.structuredText !== baseline.structuredText) patch.structured = structured
  if (!sameList(form.tags, baseline.tags)) patch.tags = form.tags
  if (!sameList(form.sourceRefs, baseline.sourceRefs)) patch.source_refs = form.sourceRefs
  return patch
}
