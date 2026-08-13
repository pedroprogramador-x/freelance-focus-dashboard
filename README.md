# Freelance Focus Dashboard V2

[![Deploy GitHub Pages](https://github.com/pedroprogramador-x/freelance-focus-dashboard/actions/workflows/deploy.yml/badge.svg)](https://github.com/pedroprogramador-x/freelance-focus-dashboard/actions/workflows/deploy.yml)
![React](https://img.shields.io/badge/React-18-149eca?logo=react)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6?logo=typescript)
![Vite](https://img.shields.io/badge/Vite-6-646cff?logo=vite)
![License MIT](https://img.shields.io/badge/license-MIT-0f766e)

Aplicação web responsiva para organizar o fluxo comercial e a execução do trabalho freelance. A V2 conecta **Clientes → Propostas → Projetos**, permite planejar tecnicamente cada projeto e preserva o Plano 90 Dias original. Todos os dados permanecem no navegador.

## Funcionalidades

- clientes com contatos, empresa, origem, indicação, status, busca e visão financeira;
- propostas em BRL ou USD relacionadas a clientes e, opcionalmente, a serviços;
- criação explícita de projeto a partir de uma proposta aceita;
- projetos com prazo, status, horas, links, pagamentos e página detalhada;
- planejamento técnico por projeto com problema, objetivo, requisitos, stack, arquitetura, decisões e riscos;
- tarefas de projeto com status, prioridade, prazo, filtros e progresso automático;
- dashboard com indicadores comerciais e alertas simples de execução;
- Plano 90 Dias com exatamente 90 tarefas em 13 semanas;
- catálogo editável de serviços, tema claro/escuro e backup JSON;
- persistência automática em `localStorage` e layout responsivo.

## Schema V3

O produto continua sendo Freelance Focus V2. `schemaVersion: 3` representa apenas a versão do formato persistido.

```text
clients             clientes e contatos
proposals           oportunidades relacionadas por clientId
projects            execução e pagamentos relacionados por clientId
projectPlannings    planejamento técnico relacionado por projectId
projectTasks        tarefas de execução relacionadas por projectId
services            catálogo de serviços
tasks               as 90 tarefas do Plano 90 Dias
settings            preferências locais
savedAt             data do último salvamento
```

Existe no máximo um `ProjectPlanning` por projeto. Cada planejamento mantém listas de requisitos funcionais e não funcionais, stack, arquitetura textual, decisões técnicas e riscos. Cada `ProjectTask` pertence a um projeto e usa os status `Pendente`, `Em andamento`, `Bloqueado` ou `Concluído`, com prioridade `Alta`, `Média` ou `Baixa`.

Ao concluir uma tarefa, `completedAt` recebe uma data local `YYYY-MM-DD`; ao retirar a conclusão, a data é removida. O progresso é sempre calculado por `tarefas concluídas / total de tarefas`. Quando não há tarefas, a interface informa isso sem exibir 100%.

## Integridade e exclusões

Relacionamentos são validados durante criação, edição, carregamento, migração e importação. O sistema rejeita planejamentos ou tarefas órfãs e dois planejamentos para o mesmo projeto.

Clientes, propostas e serviços com dependências continuam protegidos contra exclusões que criariam órfãos. Um projeto sem dados de execução respeita a configuração geral de confirmação. Um projeto com planejamento ou tarefas exige confirmação mesmo quando essa configuração está desativada; a mensagem informa as quantidades e a ação explícita remove projeto, planejamento e tarefas em uma única atualização. Não existe cascade delete silencioso.

## Valores financeiros

`Project.amount` é o valor bruto contratado com o cliente. `amountReceived` é quanto desse recebível foi efetivamente recebido, na mesma moeda. Taxa da plataforma e cotação histórica ficam em campos estruturados separados. Valores não aceitam números negativos, o recebido não pode superar o contratado e o status deve corresponder a zero, valor parcial ou total positivo. BRL e USD são totalizados separadamente, sem conversão implícita.

## Migrações

### V2 → V3

A migração preserva integralmente clientes, propostas, projetos, serviços, as 90 tarefas do roadmap, configurações, valores e relacionamentos. Ela apenas altera o número do schema e inicia `projectPlannings` e `projectTasks` vazios. Planejamentos são criados sob demanda quando o usuário os salva.

O carregamento procura, nesta ordem, `freelance-focus:data:v3`, `freelance-focus:data:v2` e `freelance-focus:data:v1`. Novos salvamentos usam `freelance-focus:data:v3`.

### V1 → V2 → V3

A compatibilidade anterior foi mantida:

- nomes de clientes são normalizados de forma conservadora para deduplicação: `trim`, espaços consecutivos reduzidos, decomposição Unicode NFD, remoção de acentos e minúsculas em `pt-BR`;
- propostas e contratos antigos viram propostas e projetos relacionados;
- o valor bruto contratado é preservado em `Project.amount`;
- todos os pagamentos V1 migram como pendentes, pois `Entregue` não comprova recebimento;
- roadmap, configurações e serviços são preservados;
- depois da conversão V1 → V2, a mesma migração V2 → V3 adiciona as novas coleções vazias.

A importação aceita backups V1, V2 e V3 válidos. Schema desconhecido, tipos incorretos, arrays ausentes, enums inválidos, datas impossíveis, IDs duplicados ou referências quebradas são rejeitados antes de substituir o estado atual.

## Plano 90 Dias x tarefas de projeto

`RoadmapTask` representa o programa pessoal de 90 dias e continua com 90 itens em 13 semanas, notas e reagendamento. `ProjectTask` representa uma ação de execução de um trabalho para cliente. Os dois tipos usam coleções, regras, progresso e telas independentes.

## Tecnologias e estrutura

React 18, TypeScript, Vite, CSS responsivo, Lucide React, Vitest, React Testing Library, ESLint e GitHub Actions.

```text
src/
├── components/   layout, modal e componentes visuais
├── context/      estado global e ações
├── data/         roadmap, domínio e dados iniciais
├── pages/        painel, plano, clientes, propostas, projetos e ajustes
├── services/     persistência, validação, migração e backup
├── styles/       sistema visual responsivo
├── test/         testes unitários, migração e interface
├── types/        tipos de domínio
└── utils/        cálculos e filtros
```

## Instalação e comandos

Requer Node.js 22 LTS.

```bash
npm install
npm run dev
```

| Comando | Finalidade |
| --- | --- |
| `npm run dev` | inicia o servidor local |
| `npm run lint` | verifica a qualidade do código |
| `npm test` | executa todos os testes uma vez |
| `npm run test:watch` | executa testes continuamente |
| `npm run build` | valida TypeScript e gera `dist` |
| `npm run preview` | abre localmente a build final |

## GitHub Pages

O Vite mantém `/freelance-focus-dashboard/` como `base` de produção. A navegação interna não depende de rotas do servidor. O workflow em `.github/workflows/deploy.yml` executa lint, testes e build antes do deploy.

## Armazenamento, privacidade e limitações

Todos os dados ficam no `localStorage` do navegador. Não existe backend, banco de dados, autenticação, sincronização, analytics ou integração com APIs externas. Limpar os dados do navegador remove os registros locais; exporte backups regularmente.

Limitações desta fase:

- não há sincronização entre dispositivos, login ou controle de acesso;
- arquitetura é apenas texto multilinha, sem diagrama ou geração automática;
- tarefas usam lista e filtros, sem Kanban ou drag and drop;
- decisões técnicas não são ADRs formais;
- riscos não possuem probabilidade ou impacto;
- não há dependências entre tarefas, anexos ou integração com repositórios;
- não há manutenção, planos mensais, IA, financeiro avançado ou conversão automática de moedas.

## Licença

Distribuído sob a [licença MIT](LICENSE).
