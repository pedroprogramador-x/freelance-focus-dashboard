import { ArrowUpDown, BriefcaseBusiness, Copy, ExternalLink, FolderPlus, Plus, Search, Send, Trash2, UserRoundCheck } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { ConfirmModal, Modal } from '../components/Modal'
import type { PageId } from '../components/Layout'
import { useApp } from '../context/AppContext'
import type { ClientSource, Currency, Proposal, ProposalStatus } from '../types'
import { proposalMetrics } from '../utils/calculations'
import { toDateInput } from '../data/roadmap'
import { CLIENT_SOURCES, deletionBlockReason, hasValidEntityReferences } from '../data/domain'

const statuses: ProposalStatus[] = ['Rascunho', 'Enviada', 'Aguardando resposta', 'Aceita', 'Recusada', 'Expirada']
const dateNow = () => toDateInput(new Date())
const money = (value: number, currency: Currency) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(value)
const emptyProposal = (clientId: string): Proposal => ({ id: crypto.randomUUID(), clientId, serviceId: null, title: '', description: '', amount: 0, currency: 'BRL', source: 'Contato direto', status: 'Rascunho', createdAt: dateNow(), sentAt: null, validUntil: null, followUpDate: null, estimatedHours: 0, notes: '' })

function ProposalForm({ initial, onClose }: { initial: Proposal; onClose: () => void }) {
  const { data, upsert, notify } = useApp()
  const [form, setForm] = useState(initial)
  const set = <K extends keyof Proposal>(key: K, value: Proposal[K]) => setForm((current) => ({ ...current, [key]: value }))
  const platformRelevant = form.source === 'Upwork' || form.source === '99Freelas'
  const setPlatform = (key: 'url' | 'connects' | 'platformFeePercent', value: string | number | undefined) => setForm((current) => ({ ...current, platformData: { ...current.platformData, [key]: value } }))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!form.title.trim() || !form.clientId) return
    const platformData = form.platformData && Object.values(form.platformData).some((value) => value !== undefined && value !== '' && value !== 0) ? form.platformData : undefined
    const proposal = { ...form, title: form.title.trim(), sentAt: form.status === 'Rascunho' ? null : (form.sentAt || dateNow()), ...(platformData ? { platformData } : { platformData: undefined }) }
    if (!hasValidEntityReferences(data, 'proposals', proposal)) { notify('Revise cliente e serviço: a alteração quebraria um relacionamento existente.', 'error'); return }
    upsert('proposals', proposal)
    notify('Proposta salva com sucesso.', 'success')
    onClose()
  }
  return <form onSubmit={submit} className="form-grid">
    <label>Cliente *<select required value={form.clientId} onChange={(event) => set('clientId', event.target.value)}><option value="">Selecione</option>{data.clients.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>Serviço<select value={form.serviceId ?? ''} onChange={(event) => set('serviceId', event.target.value || null)}><option value="">Sem serviço relacionado</option>{data.services.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label className="wide">Título *<input required value={form.title} onChange={(event) => set('title', event.target.value)} /></label>
    <label className="wide">Descrição<textarea value={form.description} onChange={(event) => set('description', event.target.value)} /></label>
    <label>Valor<input type="number" min="0" step="0.01" value={form.amount} onChange={(event) => set('amount', Number(event.target.value))} /></label>
    <label>Moeda<select value={form.currency} onChange={(event) => set('currency', event.target.value as Currency)}><option value="BRL">Real (BRL)</option><option value="USD">Dólar (USD)</option></select></label>
    <label>Origem<select value={form.source} onChange={(event) => set('source', event.target.value as ClientSource)}>{CLIENT_SOURCES.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Status<select value={form.status} onChange={(event) => set('status', event.target.value as ProposalStatus)}>{statuses.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Criada em<input type="date" value={form.createdAt.slice(0, 10)} onChange={(event) => set('createdAt', event.target.value)} /></label>
    <label>Enviada em<input type="date" value={form.sentAt ?? ''} onChange={(event) => set('sentAt', event.target.value || null)} /></label>
    <label>Válida até<input type="date" value={form.validUntil ?? ''} onChange={(event) => set('validUntil', event.target.value || null)} /></label>
    <label>Follow-up<input type="date" value={form.followUpDate ?? ''} onChange={(event) => set('followUpDate', event.target.value || null)} /></label>
    <label>Horas estimadas<input type="number" min="0" step="0.5" value={form.estimatedHours} onChange={(event) => set('estimatedHours', Number(event.target.value))} /></label>
    {platformRelevant && <label>Taxa da plataforma (%)<input type="number" min="0" max="100" step="0.1" value={form.platformData?.platformFeePercent ?? 0} onChange={(event) => setPlatform('platformFeePercent', Number(event.target.value))} /></label>}
    {form.source === 'Upwork' && <label>Connects<input type="number" min="0" value={form.platformData?.connects ?? 0} onChange={(event) => setPlatform('connects', Number(event.target.value))} /></label>}
    <label className="wide">Link da oportunidade<input type="url" placeholder="https://" value={form.platformData?.url ?? ''} onChange={(event) => setPlatform('url', event.target.value || undefined)} /></label>
    <label className="wide">Notas<textarea value={form.notes} onChange={(event) => set('notes', event.target.value)} /></label>
    <div className="form-actions wide"><button type="button" className="button secondary" onClick={onClose}>Cancelar</button><button className="button primary">Salvar proposta</button></div>
  </form>
}

export function ProposalsPage({ navigate, onCreateProject }: { navigate: (page: PageId) => void; onCreateProject?: (proposalId: string) => void }) {
  const { data, upsert, remove, notify } = useApp()
  const [editing, setEditing] = useState<Proposal | null>(null)
  const [deleting, setDeleting] = useState<Proposal | null>(null)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [source, setSource] = useState('')
  const [sortDesc, setSortDesc] = useState(true)
  const metrics = proposalMetrics(data.proposals)
  const clientName = (id: string) => data.clients.find((client) => client.id === id)?.name ?? 'Cliente indisponível'
  const list = useMemo(() => data.proposals.filter((item) => {
    const relatedClient = data.clients.find((client) => client.id === item.clientId)?.name ?? 'Cliente indisponível'
    return (!search || `${item.title} ${relatedClient} ${item.description}`.toLocaleLowerCase('pt-BR').includes(search.toLocaleLowerCase('pt-BR'))) && (!status || item.status === status) && (!source || item.source === source)
  }).sort((a, b) => sortDesc ? b.createdAt.localeCompare(a.createdAt) : a.createdAt.localeCompare(b.createdAt)), [data.proposals, data.clients, search, status, source, sortDesc])
  const startCreate = () => data.clients.length ? setEditing(emptyProposal(data.clients[0].id)) : navigate('clients')
  const duplicate = (item: Proposal) => { upsert('proposals', { ...item, id: crypto.randomUUID(), title: `${item.title} (cópia)`, createdAt: dateNow(), sentAt: null, status: 'Rascunho' }); notify('Proposta duplicada.', 'success') }
  const requestDelete = (item: Proposal) => {
    const blocked = deletionBlockReason(data, 'proposals', item.id)
    if (blocked) { notify('Não é possível excluir: há um projeto relacionado.', 'error'); return }
    if (data.settings.confirmBeforeDelete) setDeleting(item)
    else remove('proposals', item.id)
  }
  const won = data.proposals.filter((item) => item.status === 'Aceita').reduce<Record<Currency, number>>((total, item) => { total[item.currency] += item.amount; return total }, { BRL: 0, USD: 0 })
  return <div>
    <section className="page-intro"><div><span className="kicker">Pipeline comercial</span><h2>Propostas</h2><p>Acompanhe oportunidades em reais ou dólares, independentemente da plataforma.</p></div><button className="button primary" onClick={startCreate}><Plus size={17} /> Nova proposta</button></section>
    {!data.clients.length && <section className="empty-callout card"><UserRoundCheck size={24} /><div><strong>Cadastre um cliente antes da primeira proposta</strong><p>A V2 usa relacionamentos por ID para manter o histórico consistente.</p></div><button className="button primary" onClick={() => navigate('clients')}>Ir para Clientes</button></section>}
    <section className="metric-strip"><div><Send /><span>Enviadas<strong>{metrics.sent}</strong></span></div><div><UserRoundCheck /><span>Em aberto<strong>{metrics.open}</strong></span></div><div><BriefcaseBusiness /><span>Aceitas<strong>{metrics.accepted}</strong></span></div><div><span className="connect-symbol">C</span><span>Connects gastos<strong>{metrics.connects}</strong></span></div><div><span className="money-symbol">$</span><span>Valor aceito<strong>{money(won.BRL, 'BRL')} · {money(won.USD, 'USD')}</strong></span></div></section>
    <section className="table-toolbar card"><div className="search-field"><Search size={18} /><input placeholder="Pesquisar propostas..." value={search} onChange={(event) => setSearch(event.target.value)} /></div><select aria-label="Filtrar por status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Todos os status</option>{statuses.map((item) => <option key={item}>{item}</option>)}</select><select aria-label="Filtrar por origem" value={source} onChange={(event) => setSource(event.target.value)}><option value="">Todas as origens</option>{CLIENT_SOURCES.map((item) => <option key={item}>{item}</option>)}</select><button className="button ghost" onClick={() => setSortDesc((value) => !value)}><ArrowUpDown size={16} /> Data</button></section>
    {list.length === 0 ? <div className="empty-state"><BriefcaseBusiness size={38} /><h3>{data.proposals.length ? 'Nenhuma proposta encontrada' : 'Sua próxima oportunidade começa aqui'}</h3><p>{data.proposals.length ? 'Altere os filtros para encontrar outro registro.' : 'Registre propostas e acompanhe respostas, validade e follow-up.'}</p>{data.clients.length > 0 && <button className="button primary" onClick={startCreate}><Plus size={17} /> Adicionar proposta</button>}</div> : <div className="responsive-table card"><table><thead><tr><th>Cliente / título</th><th>Valor</th><th>Status</th><th>Envio</th><th>Validade</th><th>Follow-up</th><th>Origem</th><th aria-label="Ações" /></tr></thead><tbody>{list.map((item) => { const follow = item.followUpDate && item.followUpDate <= dateNow() && ['Enviada', 'Aguardando resposta'].includes(item.status); const hasProject = data.projects.some((project) => project.proposalId === item.id); return <tr key={item.id} className={follow ? 'needs-followup' : ''}><td><button className="table-main" onClick={() => setEditing(item)}>{item.title}<small>{clientName(item.clientId)}</small></button></td><td>{money(item.amount, item.currency)}</td><td><span className={`status-pill proposal-${item.status.toLowerCase().replaceAll(' ', '-')}`}>{item.status}</span></td><td>{item.sentAt?.split('-').reverse().join('/') || '—'}</td><td>{item.validUntil?.split('-').reverse().join('/') || '—'}</td><td>{follow && <span className="follow-badge">Acompanhar</span>}{item.followUpDate?.split('-').reverse().join('/') || '—'}</td><td>{item.source}</td><td><div className="table-actions">{item.status === 'Aceita' && !hasProject && onCreateProject && <button className="icon-button" onClick={() => onCreateProject(item.id)} aria-label="Criar projeto desta proposta" title="Criar projeto"><FolderPlus size={16} /></button>}{item.platformData?.url && <a href={item.platformData.url} target="_blank" rel="noreferrer" className="icon-button" aria-label="Abrir oportunidade"><ExternalLink size={16} /></a>}<button className="icon-button" onClick={() => duplicate(item)} aria-label="Duplicar proposta"><Copy size={16} /></button><button className="icon-button danger-icon" onClick={() => requestDelete(item)} aria-label="Excluir proposta"><Trash2 size={16} /></button></div></td></tr> })}</tbody></table></div>}
    {editing && <Modal title={data.proposals.some((item) => item.id === editing.id) ? 'Editar proposta' : 'Nova proposta'} onClose={() => setEditing(null)} size="large"><ProposalForm initial={editing} onClose={() => setEditing(null)} /></Modal>}
    {deleting && <ConfirmModal title="Excluir proposta?" message={`A proposta “${deleting.title}” será removida permanentemente.`} danger onClose={() => setDeleting(null)} onConfirm={() => { remove('proposals', deleting.id); notify('Proposta excluída.') }} />}
  </div>
}
