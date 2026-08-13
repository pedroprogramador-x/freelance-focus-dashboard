import { CircleDollarSign, Clock3, ExternalLink, FolderKanban, Plus, Trash2, TrendingUp, Users } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import type { PageId } from '../components/Layout'
import { ConfirmModal, Modal } from '../components/Modal'
import { useApp } from '../context/AppContext'
import type { Currency, PaymentStatus, Project, ProjectStatus, Proposal } from '../types'
import { projectMetrics } from '../utils/calculations'
import { toDateInput } from '../data/roadmap'
import { hasValidEntityReferences } from '../data/domain'

const statuses: ProjectStatus[] = ['Planejamento', 'Em desenvolvimento', 'Aguardando cliente', 'Em revisão', 'Entregue', 'Pausado', 'Cancelado']
const paymentStatuses: PaymentStatus[] = ['Pendente', 'Parcial', 'Pago']
const dateNow = () => toDateInput(new Date())
const timestamp = () => new Date().toISOString()
const money = (value: number, currency: Currency) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(value)
const emptyProject = (clientId: string): Project => ({ id: crypto.randomUUID(), clientId, proposalId: null, name: '', description: '', status: 'Planejamento', startDate: null, deadline: null, completedAt: null, amount: 0, currency: 'BRL', platformFeePercent: null, exchangeRateToBrl: null, estimatedHours: 0, workedHours: 0, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente', amountReceived: 0, notes: '', createdAt: timestamp(), updatedAt: timestamp() })
const projectFromProposal = (proposal: Proposal): Project => ({ ...emptyProject(proposal.clientId), proposalId: proposal.id, name: proposal.title, description: proposal.description, amount: proposal.amount, currency: proposal.currency, platformFeePercent: proposal.platformData?.platformFeePercent ?? null, estimatedHours: proposal.estimatedHours })

function ProjectForm({ initial, onClose }: { initial: Project; onClose: () => void }) {
  const { data, upsert, notify } = useApp()
  const [form, setForm] = useState(initial)
  const set = <K extends keyof Project>(key: K, value: Project[K]) => setForm((current) => ({ ...current, [key]: value }))
  const proposals = data.proposals.filter((item) => item.clientId === form.clientId)
  const setClient = (clientId: string) => setForm((current) => ({ ...current, clientId, proposalId: data.proposals.some((proposal) => proposal.id === current.proposalId && proposal.clientId === clientId) ? current.proposalId : null }))
  const setAmount = (amount: number) => setForm((current) => ({ ...current, amount, amountReceived: current.paymentStatus === 'Pago' ? amount : Math.min(current.amountReceived, amount), paymentStatus: current.paymentStatus === 'Parcial' && current.amountReceived >= amount ? 'Pago' : current.paymentStatus }))
  const setPayment = (paymentStatus: PaymentStatus) => setForm((current) => ({ ...current, paymentStatus, amountReceived: paymentStatus === 'Pendente' ? 0 : paymentStatus === 'Pago' ? current.amount : (current.amount > 0 ? Math.min(Math.max(current.amountReceived, current.amount / 2), Math.max(0, current.amount - 0.01)) : 0) }))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!form.name.trim() || !form.clientId) return
    const amountReceived = Math.min(Math.max(form.amountReceived, 0), form.amount)
    const paymentStatus: PaymentStatus = amountReceived <= 0 ? 'Pendente' : amountReceived >= form.amount ? 'Pago' : 'Parcial'
    const project = { ...form, name: form.name.trim(), amountReceived, paymentStatus, completedAt: form.status === 'Entregue' ? (form.completedAt || dateNow()) : null, updatedAt: timestamp() }
    if (!hasValidEntityReferences(data, 'projects', project)) { notify('Revise cliente e proposta: o relacionamento selecionado não é válido.', 'error'); return }
    upsert('projects', project)
    notify('Projeto salvo com sucesso.', 'success')
    onClose()
  }
  return <form className="form-grid" onSubmit={submit}>
    <label>Cliente *<select required value={form.clientId} onChange={(event) => setClient(event.target.value)}><option value="">Selecione</option>{data.clients.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
    <label>Proposta relacionada<select value={form.proposalId ?? ''} onChange={(event) => set('proposalId', event.target.value || null)}><option value="">Sem proposta relacionada</option>{proposals.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>
    <label className="wide">Nome do projeto *<input required value={form.name} onChange={(event) => set('name', event.target.value)} /></label>
    <label className="wide">Descrição<textarea value={form.description} onChange={(event) => set('description', event.target.value)} /></label>
    <label>Status<select value={form.status} onChange={(event) => set('status', event.target.value as ProjectStatus)}>{statuses.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Pagamento<select value={form.paymentStatus} onChange={(event) => setPayment(event.target.value as PaymentStatus)}>{paymentStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Início<input type="date" value={form.startDate ?? ''} onChange={(event) => set('startDate', event.target.value || null)} /></label>
    <label>Prazo<input type="date" value={form.deadline ?? ''} onChange={(event) => set('deadline', event.target.value || null)} /></label>
    {form.status === 'Entregue' && <label>Concluído em<input type="date" value={form.completedAt ?? ''} onChange={(event) => set('completedAt', event.target.value || null)} /></label>}
    <label>Valor<input type="number" min="0" step="0.01" value={form.amount} onChange={(event) => setAmount(Number(event.target.value))} /></label>
    <label>Moeda<select value={form.currency} onChange={(event) => set('currency', event.target.value as Currency)}><option value="BRL">Real (BRL)</option><option value="USD">Dólar (USD)</option></select></label>
    <label>Taxa da plataforma (%)<input type="number" min="0" max="100" step="0.1" value={form.platformFeePercent ?? ''} onChange={(event) => set('platformFeePercent', event.target.value === '' ? null : Number(event.target.value))} /></label>
    {form.currency === 'USD' && <label>Cotação registrada (BRL/USD)<input type="number" min="0.0001" step="0.0001" value={form.exchangeRateToBrl ?? ''} onChange={(event) => set('exchangeRateToBrl', event.target.value === '' ? null : Number(event.target.value))} /></label>}
    <label>Valor recebido<input type="number" min="0" max={form.amount} step="0.01" value={form.amountReceived} onChange={(event) => { const received = Number(event.target.value); setForm((current) => ({ ...current, amountReceived: received, paymentStatus: received <= 0 ? 'Pendente' : received >= current.amount ? 'Pago' : 'Parcial' })) }} /></label>
    <label>Horas estimadas<input type="number" min="0" step="0.25" value={form.estimatedHours} onChange={(event) => set('estimatedHours', Number(event.target.value))} /></label>
    <label>Horas trabalhadas<input type="number" min="0" step="0.25" value={form.workedHours} onChange={(event) => set('workedHours', Number(event.target.value))} /></label>
    <label className="wide">Repositório<input type="url" placeholder="https://github.com/..." value={form.repositoryUrl} onChange={(event) => set('repositoryUrl', event.target.value)} /></label>
    <label className="wide">URL publicada<input type="url" placeholder="https://" value={form.productionUrl} onChange={(event) => set('productionUrl', event.target.value)} /></label>
    <label className="wide">Notas<textarea value={form.notes} onChange={(event) => set('notes', event.target.value)} /></label>
    <div className="calculation-preview wide"><span>Contratado: <strong>{money(form.amount, form.currency)}</strong></span><span>Recebido: <strong>{money(Math.min(form.amountReceived, form.amount), form.currency)}</strong></span><span>Pendente: <strong>{money(Math.max(form.amount - form.amountReceived, 0), form.currency)}</strong></span></div>
    <div className="form-actions wide"><button type="button" className="button secondary" onClick={onClose}>Cancelar</button><button className="button primary">Salvar projeto</button></div>
  </form>
}

export function ProjectsPage({ navigate, proposalForProjectId, onProposalHandled }: { navigate: (page: PageId) => void; proposalForProjectId?: string | null; onProposalHandled?: () => void }) {
  const { data, remove, notify } = useApp()
  const [editing, setEditing] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState<Project | null>(null)
  useEffect(() => {
    if (!proposalForProjectId) return
    const proposal = data.proposals.find((item) => item.id === proposalForProjectId && item.status === 'Aceita')
    if (proposal && !data.projects.some((project) => project.proposalId === proposal.id)) setEditing(projectFromProposal(proposal))
    onProposalHandled?.()
  }, [proposalForProjectId, data.proposals, data.projects, onProposalHandled])
  const metrics = projectMetrics(data.projects)
  const clientName = (id: string) => data.clients.find((client) => client.id === id)?.name ?? 'Cliente indisponível'
  const startCreate = () => data.clients.length ? setEditing(emptyProject(data.clients[0].id)) : navigate('clients')
  const cards = [
    { label: 'Projetos ativos', value: metrics.active, icon: FolderKanban },
    { label: 'Contratado em BRL', value: money(metrics.contracted.BRL, 'BRL'), icon: CircleDollarSign },
    { label: 'Recebido em BRL', value: money(metrics.received.BRL, 'BRL'), icon: TrendingUp },
    { label: 'Pendente em BRL', value: money(metrics.pending.BRL, 'BRL'), icon: CircleDollarSign },
    { label: 'Contratado em USD', value: money(metrics.contracted.USD, 'USD'), icon: TrendingUp },
    { label: 'Horas trabalhadas', value: `${metrics.hours.toFixed(1)}h`, icon: Clock3 },
  ]
  return <div>
    <section className="page-intro"><div><span className="kicker">Execução e recebimentos</span><h2>Projetos</h2><p>Controle entregas, horas, links e pagamentos por cliente.</p></div><button className="button primary" onClick={startCreate}><Plus size={17} /> Novo projeto</button></section>
    {!data.clients.length && <section className="empty-callout card"><Users size={24} /><div><strong>Cadastre um cliente antes do primeiro projeto</strong><p>Todo projeto precisa estar ligado a um cliente existente.</p></div><button className="button primary" onClick={() => navigate('clients')}>Ir para Clientes</button></section>}
    <section className="stats-grid project-stats">{cards.map(({ label, value, icon: Icon }) => <article className="stat-card" key={label}><div className="stat-icon green"><Icon size={19} /></div><div><span>{label}</span><strong>{value}</strong></div></article>)}</section>
    {data.projects.length === 0 ? <div className="empty-state"><FolderKanban size={40} /><h3>Registre seu primeiro projeto</h3><p>Projetos substituem contratos e concentram execução, links e recebimentos.</p>{data.clients.length > 0 && <button className="button primary" onClick={startCreate}><Plus size={17} /> Adicionar projeto</button>}</div> : <div className="contract-grid">{data.projects.map((item) => <article className="card contract-card" key={item.id}><header><div><span>{clientName(item.clientId)}</span><h3>{item.name}</h3><p>{item.deadline ? `Prazo: ${item.deadline.split('-').reverse().join('/')}` : 'Sem prazo definido'}</p></div><span className={`status-pill project-${item.status.toLowerCase().replaceAll(' ', '-')}`}>{item.status}</span></header><div className="project-payment"><div><span>Valor</span><strong>{money(item.amount, item.currency)}</strong></div><div><span>Recebido</span><strong>{money(item.amountReceived, item.currency)}</strong></div><div><span>Pendente</span><strong>{money(item.amount - item.amountReceived, item.currency)}</strong></div></div><div className="project-links">{item.repositoryUrl && <a href={item.repositoryUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Repositório</a>}{item.productionUrl && <a href={item.productionUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Publicado</a>}</div><footer><button className="text-button" onClick={() => setEditing(item)}>Editar detalhes</button><button className="icon-button danger-icon" onClick={() => data.settings.confirmBeforeDelete ? setDeleting(item) : remove('projects', item.id)} aria-label="Excluir projeto"><Trash2 size={16} /></button></footer></article>)}</div>}
    {editing && <Modal title={data.projects.some((item) => item.id === editing.id) ? 'Editar projeto' : 'Novo projeto'} onClose={() => setEditing(null)} size="large"><ProjectForm initial={editing} onClose={() => setEditing(null)} /></Modal>}
    {deleting && <ConfirmModal title="Excluir projeto?" message={`Os dados de “${deleting.name}” serão removidos.`} danger onClose={() => setDeleting(null)} onConfirm={() => { remove('projects', deleting.id); notify('Projeto excluído.') }} />}
  </div>
}
