# Freelance Focus Dashboard V2

[![Deploy GitHub Pages](https://github.com/USUARIO/freelance-focus-dashboard/actions/workflows/deploy.yml/badge.svg)](https://github.com/USUARIO/freelance-focus-dashboard/actions/workflows/deploy.yml)
![React](https://img.shields.io/badge/React-18-149eca?logo=react)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6?logo=typescript)
![Vite](https://img.shields.io/badge/Vite-6-646cff?logo=vite)
![License MIT](https://img.shields.io/badge/license-MIT-0f766e)

Aplicação web responsiva para organizar o fluxo comercial e a execução do trabalho freelance. A V2 conecta **Clientes → Propostas → Projetos** e preserva o Plano 90 Dias original, sem enviar dados para um servidor.

## Funcionalidades

- clientes com contatos, empresa, origem, indicação, status, busca e visão financeira;
- propostas em BRL ou USD relacionadas a clientes e, opcionalmente, a serviços;
- suporte a Upwork, 99Freelas, indicação, contato direto e outras origens;
- campos específicos de plataforma exibidos apenas quando relevantes;
- projetos com prazo, status, horas, repositório, URL publicada e pagamentos;
- dashboard com clientes, leads, propostas, projetos e valores por moeda;
- Plano 90 Dias com exatamente 90 tarefas em 13 semanas;
- catálogo editável de serviços;
- tema claro, escuro ou do sistema;
- exportação, validação e importação de backups JSON V1 e V2;
- persistência automática em `localStorage`;
- layout acessível e responsivo para desktop e celular.

## Schema V2

O estado persistido usa `schemaVersion: 2` e contém:

```text
clients      clientes e contatos
proposals    oportunidades relacionadas por clientId
projects     execução e pagamentos relacionados por clientId
services     catálogo de serviços
tasks        as 90 tarefas do plano
settings     preferências locais
savedAt      data do último salvamento
```

Propostas podem se relacionar a um serviço por `serviceId`. Projetos podem se relacionar opcionalmente a uma proposta por `proposalId`. Todos os relacionamentos são validados durante criação, edição, exclusão, carregamento e importação. Clientes, propostas e serviços com dependências não são excluídos; não existe exclusão em cascata silenciosa.

`Project.amount` é o valor bruto contratado com o cliente, tanto para clientes locais quanto para plataformas. `amountReceived` é quanto desse recebível foi efetivamente recebido, na mesma moeda do projeto. A taxa da plataforma e a cotação histórica ficam em campos estruturados separados e não reduzem o valor contratado. Valores de projeto e recebimentos não aceitam números negativos. `amountReceived` não pode superar `amount`, e o status de pagamento deve concordar com o valor recebido: zero é pendente, um valor intermediário é parcial e o valor total positivo é pago. BRL e USD são totalizados separadamente; não há soma ou conversão implícita entre moedas.

## Migração V1 → V2

A atualização lê a chave anterior `freelance-focus:data:v1` quando não encontra dados V2 válidos.

- nomes de clientes são normalizados somente para deduplicação conservadora: espaços externos são removidos, sequências de espaços viram um espaço, acentos são removidos por decomposição Unicode NFD e o texto passa para minúsculas em `pt-BR`; pontuação, ordem e demais caracteres são preservados;
- o primeiro nome original é preservado para exibição;
- IDs de clientes são determinísticos para o mesmo nome normalizado;
- propostas antigas são ligadas ao cliente criado e passam a usar moeda USD explícita;
- os status antigos são mapeados para o pipeline V2;
- contratos são convertidos em projetos;
- `Project.amount` recebe o valor bruto contratado; taxa da plataforma e cotação são preservadas também em campos estruturados, enquanto detalhes históricos e avaliação continuam nas notas;
- todos os pagamentos migram como pendentes, inclusive contratos entregues, porque o V1 não registrava o recebimento financeiro;
- o status `Entregue` é preservado, mas não cria uma data de conclusão que o V1 não possuía;
- roadmap, configurações e serviços são preservados sem mudança de semântica;
- dados V2 produzidos pela regra financeira anterior são reparados uma única vez a partir do bloco identificável da migração, sem alterar projetos nativos nem duplicar notas.

A chave V1 permanece como cópia de segurança local durante a migração; novos salvamentos são feitos em `freelance-focus:data:v2`.

## Tecnologias

React 18, TypeScript, Vite, CSS responsivo, Lucide React, Vitest, React Testing Library, ESLint e GitHub Actions.

## Estrutura

```text
src/
├── components/   layout, modal e componentes visuais
├── context/      estado global e ações
├── data/         roadmap, domínio e dados iniciais
├── pages/        painel, plano, clientes, propostas, projetos e ajustes
├── services/     persistência, validação, migração e backup
├── styles/       sistema visual responsivo
├── test/         testes unitários, migração e interface
├── types/        tipos de domínio V2
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

O Vite mantém `/freelance-focus-dashboard/` como `base` de produção. A navegação continua baseada em hash, evitando erros 404 no GitHub Pages. O workflow em `.github/workflows/deploy.yml` executa lint, testes e build antes do deploy.

## Armazenamento, privacidade e backup

Todos os dados continuam locais no `localStorage` do navegador. Não existe backend, banco de dados, autenticação, sincronização, analytics ou integração com APIs externas.

Os dados são específicos do navegador, dispositivo e perfil em uso. Limpar os dados do navegador remove os registros locais. Exporte backups JSON regularmente, principalmente antes de trocar de dispositivo ou navegador.

A importação aceita backups V2 válidos e migra backups V1 válidos. Arquivos inválidos são rejeitados sem substituir o estado atual.

## Limitações atuais

- não há sincronização entre dispositivos;
- não há login nem controle de acesso;
- não há backend, PostgreSQL ou armazenamento em nuvem;
- tarefas de projeto e planos de manutenção ainda não fazem parte desta fase;
- a cotação antiga fica estruturada e documentada nas notas da migração, mas a V2 não converte moedas automaticamente;
- exclusões de cliente, proposta ou serviço são bloqueadas enquanto houver registros dependentes.

## Fora de escopo desta fase

IA, autenticação, backend, banco de dados, GitHub, WhatsApp, n8n, e-mail, cobrança, nota fiscal, PWA e aplicativo mobile permanecem como possibilidades futuras.

## Licença

Distribuído sob a [licença MIT](LICENSE).
