import type { Priority, RoadmapTask } from '../types'

export const WEEK_PHASES = [
  'Fundação', 'Portfólio', 'Projeto demonstrativo', 'Presença profissional',
  'Perfil Upwork', 'Pesquisa de mercado', 'Primeiras propostas', 'Propostas e ajustes',
  'Vendas e contratos', 'Entrega profissional', 'Diversificação', 'Escala sustentável', 'Revisão final',
]

export const ROADMAP_TITLES = [
  'Criar uma pasta central para materiais de freelance e organização do projeto.',
  'Listar dez habilidades que já consigo aplicar em projetos reais.',
  'Definir o Serviço 1: correção e melhoria de scripts Python.',
  'Definir o Serviço 2: automação e processamento de dados.',
  'Definir o Serviço 3: integração de APIs e pequenos backends.',
  'Escrever limites de escopo e preço mínimo dos três serviços.',
  'Revisar a semana e escrever uma apresentação de três frases sobre o que ofereço.',
  'Revisar o Job Hunter Bot e listar as partes que comprovam minhas habilidades.',
  'Separar imagens, fluxos e resultados demonstráveis do Job Hunter Bot.',
  'Escrever o estudo de caso do Job Hunter Bot.',
  'Revisar o Sports Analysis Bot e selecionar as partes relevantes para clientes.',
  'Escrever o estudo de caso do Sports Analysis Bot sem focar em apostas.',
  'Planejar um projeto comercial simples de automação de relatórios CSV.',
  'Revisar os estudos de caso e simplificar os termos técnicos.',
  'Criar o repositório do automatizador de relatórios e definir seu README.',
  'Implementar leitura de arquivos CSV.',
  'Adicionar validação de colunas e mensagens de erro.',
  'Implementar limpeza de dados e remoção de duplicidades.',
  'Adicionar métricas simples e geração do relatório final.',
  'Adicionar logs, tratamento de exceções e dependências.',
  'Testar o projeto, criar exemplos e produzir imagens demonstrativas.',
  'Reescrever em inglês o README do projeto demonstrativo.',
  'Melhorar o README do Job Hunter Bot para leitura de clientes.',
  'Melhorar o README do Sports Analysis Bot para leitura de clientes.',
  'Fixar os três projetos principais no GitHub.',
  'Criar uma descrição profissional para o perfil do GitHub.',
  'Preparar uma biografia profissional curta em inglês.',
  'Revisar links, imagens e instruções dos projetos.',
  'Escolher uma foto profissional adequada.',
  'Definir o título do perfil focado em Python Automation.',
  'Escrever a primeira versão da descrição em inglês.',
  'Revisar a descrição para focar em problemas resolvidos.',
  'Selecionar habilidades, experiência e disponibilidade.',
  'Cadastrar os três itens do portfólio.',
  'Definir valor por hora e revisar completamente o perfil.',
  'Pesquisar anúncios utilizando cinco palavras-chave.',
  'Salvar cinco projetos adequados e registrar os motivos.',
  'Salvar mais cinco projetos e comparar escopo, valor e concorrência.',
  'Criar critérios Verde, Amarelo e Vermelho para selecionar anúncios.',
  'Criar uma checklist de golpes e clientes problemáticos.',
  'Analisar propostas de exemplo e identificar boas aberturas.',
  'Escolher os dois tipos de trabalho com melhor frequência e compatibilidade.',
  'Criar um modelo-base de proposta.',
  'Selecionar e enviar a primeira proposta personalizada.',
  'Selecionar e enviar a segunda proposta personalizada.',
  'Melhorar uma parte do portfólio com base nos anúncios encontrados.',
  'Selecionar e enviar a terceira proposta personalizada.',
  'Selecionar e enviar a quarta proposta personalizada.',
  'Enviar a quinta proposta e registrar os aprendizados.',
  'Verificar visualizações, respostas e qualidade das propostas.',
  'Reescrever as duas primeiras linhas do modelo de proposta.',
  'Enviar uma proposta para correção de script Python.',
  'Enviar uma proposta para automação ou processamento de dados.',
  'Enviar uma proposta para integração de API ou FastAPI.',
  'Revisar preço, prazo e perguntas utilizadas.',
  'Enviar até duas propostas adicionais realmente compatíveis.',
  'Preparar cinco perguntas para esclarecer o escopo.',
  'Criar um modelo simples de escopo com entregas e limites.',
  'Criar uma checklist para avaliar prazo e esforço.',
  'Preparar uma mensagem curta de atualização de progresso.',
  'Preparar uma mensagem para negociar mudança de escopo.',
  'Simular uma entrevista de cliente em inglês.',
  'Revisar milestones, pagamento protegido e regras da plataforma.',
  'Criar um modelo de repositório para projetos de clientes.',
  'Criar uma checklist de testes antes da entrega.',
  'Criar um modelo de README de instalação e utilização.',
  'Criar uma checklist de arquivos da entrega.',
  'Definir regra de revisões e suporte após a entrega.',
  'Simular a entrega completa do projeto demonstrativo.',
  'Anotar falhas encontradas e corrigir o processo.',
  'Criar ou revisar o perfil no 99Freelas.',
  'Cadastrar os três serviços e projetos relevantes.',
  'Pesquisar oportunidades brasileiras e salvar cinco.',
  'Enviar uma proposta bem selecionada.',
  'Atualizar o LinkedIn com foco em Python, APIs e automações.',
  'Preparar uma publicação mostrando o projeto demonstrativo.',
  'Revisar qual canal trouxe oportunidades com menor esforço.',
  'Identificar o serviço mais fácil de repetir e padronizar.',
  'Criar pacotes Básico, Padrão e Avançado.',
  'Definir quando aumentar os preços.',
  'Criar uma oferta de manutenção ou automação recorrente.',
  'Listar possíveis clientes locais.',
  'Preparar uma mensagem curta para indicações.',
  'Calcular o valor líquido por hora considerando todo o tempo gasto.',
  'Revisar quantas propostas foram enviadas, visualizadas e respondidas.',
  'Revisar contratos, ganhos, horas e avaliações.',
  'Identificar o principal gargalo.',
  'Escolher três mudanças para o próximo ciclo.',
  'Definir metas financeiras e de contratos para o próximo trimestre.',
  'Registrar os resultados, reconhecer o progresso e iniciar um novo ciclo.',
]

export const toDateInput = (date: Date) => {
  const offset = date.getTimezoneOffset()
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 10)
}

export function addDays(dateString: string, days: number) {
  const date = new Date(`${dateString}T12:00:00`)
  date.setDate(date.getDate() + days)
  return toDateInput(date)
}

export function createRoadmap(startDate = toDateInput(new Date())): RoadmapTask[] {
  return ROADMAP_TITLES.map((title, index) => {
    const day = index + 1
    const week = Math.ceil(day / 7)
    const priority: Priority = day % 7 === 0 ? 'Média' : day % 3 === 0 ? 'Alta' : 'Média'
    return {
      id: `task-${String(day).padStart(2, '0')}`,
      day,
      plannedDate: addDays(startDate, index),
      week,
      phase: WEEK_PHASES[week - 1],
      title,
      description: `Concentre-se nesta ação prática da fase ${WEEK_PHASES[week - 1].toLowerCase()} e registre o resultado nas observações.`,
      estimatedMinutes: 20 + (index % 5) * 10,
      priority,
      status: 'Pendente',
      notes: '',
      rescheduledDate: null,
      completedAt: null,
    }
  })
}

export function recalculateRoadmapDates(tasks: RoadmapTask[], startDate: string) {
  return tasks.map((task, index) => ({ ...task, plannedDate: addDays(startDate, index) }))
}
