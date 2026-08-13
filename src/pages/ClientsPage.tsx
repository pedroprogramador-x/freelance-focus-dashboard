import { Building2, Mail, Phone, Plus, Search, Trash2, UserRound, Users } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { ConfirmModal, Modal } from '../components/Modal'
import { useApp } from '../context/AppContext'
import { CLIENT_SOURCES, CLIENT_STATUSES, filterClients } from '../data/domain'
import type { Client, ClientSource, ClientStatus, Currency } from '../types'
import { clientFinancials } from '../utils/calculations'

const money = (value: number, currency: Currency) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(value)
const now = () => new Date().toISOString()
const emptyClient = (): Client => ({ id: crypto.randomUUID(), name: '', companyName: '', contactName: '', phone: '', email: '', source: 'Contato direto', referredBy: '', status: 'Lead', notes: '', createdAt: now(), updatedAt: now() })

function ClientForm({ initial, onClose }: { initial: Client; onClose: () => void }) {
  const { upsert, notify } = useApp()
  const [form, setForm] = useState(initial)
  const set = <K extends keyof Client>(key: K, value: Client[K]) => setForm((current) => ({ ...current, [key]: value }))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!form.name.trim()) return
    upsert('clients', { ...form, name: form.name.trim(), updatedAt: now() })
    notify('Cliente salvo com sucesso.', 'success')
    onClose()
  }
  return <form className="form-grid" onSubmit={submit}>
    <label>Nome *<input required value={form.name} onChange={(event) => set('name', event.target.value)} /></label>
    <label>Empresa<input value={form.companyName} onChange={(event) => set('companyName', event.target.value)} /></label>
    <label>Nome do contato<input value={form.contactName} onChange={(event) => set('contactName', event.target.value)} /></label>
    <label>Telefone<input type="tel" value={form.phone} onChange={(event) => set('phone', event.target.value)} /></label>
    <label>E-mail<input type="email" value={form.email} onChange={(event) => set('email', event.target.value)} /></label>
    <label>Status<select value={form.status} onChange={(event) => set('status', event.target.value as ClientStatus)}>{CLIENT_STATUSES.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Origem<select value={form.source} onChange={(event) => set('source', event.target.value as ClientSource)}>{CLIENT_SOURCES.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Indicado por<input value={form.referredBy} onChange={(event) => set('referredBy', event.target.value)} disabled={form.source !== 'Indicação'} placeholder={form.source === 'Indicação' ? 'Nome de quem indicou' : 'Disponível para indicação'} /></label>
    <label className="wide">Notas<textarea value={form.notes} onChange={(event) => set('notes', event.target.value)} /></label>
    <div className="form-actions wide"><button type="button" className="button secondary" onClick={onClose}>Cancelar</button><button className="button primary">Salvar cliente</button></div>
  </form>
}

function ClientDetails({ client, onClose, onEdit }: { client: Client; onClose: () => void; onEdit: () => void }) {
  const { data } = useApp()
  const proposals = data.proposals.filter((item) => item.clientId === client.id)
  const projects = data.projects.filter((item) => item.clientId === client.id)
  const totals = clientFinancials(client.id, data.projects)
  return <Modal title={client.name} onClose={onClose} size="large">
    <div className="client-detail-grid">
      <section className="client-profile"><span className={`status-pill client-${client.status.toLowerCase().replaceAll(' ', '-')}`}>{client.status}</span><h3>{client.companyName || 'Sem empresa informada'}</h3><p><UserRound size={15} /> {client.contactName || client.name}</p><p><Phone size={15} /> {client.phone || 'Telefone não informado'}</p><p><Mail size={15} /> {client.email || 'E-mail não informado'}</p><p><Building2 size={15} /> Origem: {client.source}{client.referredBy ? ` · indicação de ${client.referredBy}` : ''}</p></section>
      <section className="client-financials"><div><span>Total contratado</span><strong>{money(totals.contracted.BRL, 'BRL')}</strong><small>{money(totals.contracted.USD, 'USD')}</small></div><div><span>Total recebido</span><strong>{money(totals.received.BRL, 'BRL')}</strong><small>{money(totals.received.USD, 'USD')}</small></div></section>
    </div>
    <section className="related-block"><h3>Propostas relacionadas ({proposals.length})</h3>{proposals.length ? <ul>{proposals.map((item) => <li key={item.id}><span>{item.title}</span><strong>{money(item.amount, item.currency)} · {item.status}</strong></li>)}</ul> : <p>Nenhuma proposta relacionada.</p>}</section>
    <section className="related-block"><h3>Projetos relacionados ({projects.length})</h3>{projects.length ? <ul>{projects.map((item) => <li key={item.id}><span>{item.name}</span><strong>{money(item.amount, item.currency)} · {item.status}</strong></li>)}</ul> : <p>Nenhum projeto relacionado.</p>}</section>
    <section className="related-block"><h3>Notas</h3><p>{client.notes || 'Nenhuma nota registrada.'}</p></section>
    <div className="form-actions"><button className="button secondary" onClick={onClose}>Fechar</button><button className="button primary" onClick={onEdit}>Editar cliente</button></div>
  </Modal>
}

export function ClientsPage() {
  const { data, remove, notify } = useApp()
  const [editing, setEditing] = useState<Client | null>(null)
  const [viewing, setViewing] = useState<Client | null>(null)
  const [deleting, setDeleting] = useState<Client | null>(null)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const list = useMemo(() => filterClients(data.clients, search, status), [data.clients, search, status])
  const requestDelete = (client: Client) => {
    const related = data.proposals.some((item) => item.clientId === client.id) || data.projects.some((item) => item.clientId === client.id)
    if (related) { notify('Este cliente possui propostas ou projetos relacionados e não pode ser excluído.', 'error'); return }
    if (data.settings.confirmBeforeDelete) setDeleting(client)
    else { remove('clients', client.id); notify('Cliente excluído.') }
  }
  return <div>
    <section className="page-intro"><div><span className="kicker">Relacionamentos comerciais</span><h2>Clientes</h2><p>Centralize contatos, origens, propostas e projetos relacionados.</p></div><button className="button primary" onClick={() => setEditing(emptyClient())}><Plus size={17} /> Novo cliente</button></section>
    <section className="table-toolbar card"><div className="search-field"><Search size={18} /><input aria-label="Pesquisar clientes" placeholder="Pesquisar clientes..." value={search} onChange={(event) => setSearch(event.target.value)} /></div><select aria-label="Filtrar clientes por status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Todos os status</option>{CLIENT_STATUSES.map((item) => <option key={item}>{item}</option>)}</select></section>
    {list.length === 0 ? <div className="empty-state"><Users size={40} /><h3>{data.clients.length ? 'Nenhum cliente encontrado' : 'Cadastre seu primeiro cliente'}</h3><p>{data.clients.length ? 'Altere a busca ou o filtro de status.' : 'Clientes conectam suas propostas e projetos em uma única visão.'}</p><button className="button primary" onClick={() => setEditing(emptyClient())}><Plus size={17} /> Adicionar cliente</button></div> : <div className="responsive-table card"><table><thead><tr><th>Nome</th><th>Empresa</th><th>Telefone</th><th>Origem</th><th>Indicação</th><th>Status</th><th aria-label="Ações" /></tr></thead><tbody>{list.map((client) => <tr key={client.id}><td><button className="table-main" onClick={() => setViewing(client)}>{client.name}<small>{client.email || 'Sem e-mail'}</small></button></td><td>{client.companyName || '—'}</td><td>{client.phone || '—'}</td><td>{client.source}</td><td>{client.referredBy || '—'}</td><td><span className={`status-pill client-${client.status.toLowerCase().replaceAll(' ', '-')}`}>{client.status}</span></td><td><div className="table-actions"><button className="text-button" onClick={() => setEditing(client)}>Editar</button><button className="icon-button danger-icon" onClick={() => requestDelete(client)} aria-label={`Excluir ${client.name}`}><Trash2 size={16} /></button></div></td></tr>)}</tbody></table></div>}
    {editing && <Modal title={data.clients.some((item) => item.id === editing.id) ? 'Editar cliente' : 'Novo cliente'} onClose={() => setEditing(null)} size="large"><ClientForm initial={editing} onClose={() => setEditing(null)} /></Modal>}
    {viewing && <ClientDetails client={viewing} onClose={() => setViewing(null)} onEdit={() => { setEditing(viewing); setViewing(null) }} />}
    {deleting && <ConfirmModal title="Excluir cliente?" message={`O cliente “${deleting.name}” será removido.`} danger onClose={() => setDeleting(null)} onConfirm={() => { remove('clients', deleting.id); notify('Cliente excluído.') }} />}
  </div>
}
