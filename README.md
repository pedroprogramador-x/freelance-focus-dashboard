# Freelance Focus Dashboard

[![Deploy GitHub Pages](https://github.com/USUARIO/freelance-focus-dashboard/actions/workflows/deploy.yml/badge.svg)](https://github.com/USUARIO/freelance-focus-dashboard/actions/workflows/deploy.yml)
![React](https://img.shields.io/badge/React-18-149eca?logo=react)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6?logo=typescript)
![Vite](https://img.shields.io/badge/Vite-6-646cff?logo=vite)
![License MIT](https://img.shields.io/badge/license-MIT-0f766e)

Uma aplicação web responsiva para conduzir um roadmap de 90 dias rumo ao trabalho freelance com Python. O Freelance Focus transforma um plano longo em uma meta diária clara e reúne propostas, contratos, ganhos e serviços sem enviar dados para um servidor.

> Espaço preparado para screenshot: publique a aplicação e adicione uma captura em `docs/screenshot.png`.

## Funcionalidades

- Roadmap original com exatamente 90 metas, organizado em 13 semanas;
- tarefa do dia com início, conclusão, observações e reagendamento;
- progresso geral, semanal, sequência, alertas e próximas metas;
- pesquisa, filtros e visualização por lista ou semanas;
- pipeline de propostas com taxas, Connects e acompanhamento;
- contratos com valores bruto, líquido, em reais e por hora;
- catálogo editável com três serviços iniciais;
- tema claro, escuro ou do sistema;
- exportação, validação e importação de backup JSON;
- persistência automática em `localStorage`;
- layout acessível e responsivo para desktop e celular.

## Tecnologias

React 18, TypeScript, Vite, CSS responsivo, Lucide React, Vitest, React Testing Library, ESLint e GitHub Actions.

## Estrutura

```text
src/
├── components/   # Layout, modal e componentes visuais
├── context/      # Estado global e ações da aplicação
├── data/         # Roadmap e dados iniciais
├── pages/        # Seis áreas da aplicação
├── services/     # Persistência e backup
├── styles/       # Sistema visual responsivo
├── test/         # Testes unitários e de interface
├── types/        # Tipos de domínio
└── utils/        # Cálculos e filtros
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
| `npm run test:watch` | executa testes em modo contínuo |
| `npm run build` | valida o TypeScript e gera `dist` |
| `npm run preview` | abre localmente a build final |

## Publicação no GitHub Pages

O Vite usa `/freelance-focus-dashboard/` como `base` somente na build e a navegação é feita por hash/estado, evitando erros 404.

1. Crie o repositório `freelance-focus-dashboard` no GitHub.
2. Envie o código para a branch `main`.
3. No repositório, acesse **Settings**.
4. Abra **Pages**.
5. Em **Source**, selecione **GitHub Actions**.
6. Execute manualmente ou aguarde o workflow `Deploy GitHub Pages`.
7. Acesse `https://USUARIO.github.io/freelance-focus-dashboard/`.

Antes de publicar, substitua `USUARIO` neste README pelo seu usuário do GitHub.

## Persistência e privacidade

Os dados são salvos apenas no navegador, em uma chave versionada do `localStorage`. A aplicação não possui autenticação, backend, analytics ou integração com APIs externas. Use a exportação JSON regularmente: limpar os dados do navegador também remove os registros locais.

## Limitações

- os dados não são sincronizados entre dispositivos;
- a cotação do dólar é manual;
- notificações são internas à aplicação;
- a receita mensal reflete os contratos registrados localmente.

## Roadmap futuro

- relatórios mensais comparativos;
- impressão de resumo trimestral;
- modelos reutilizáveis de propostas;
- importação opcional de CSV.

## Licença

Distribuído sob a [licença MIT](LICENSE).
