# Plano de Execução — Status (base: PDF de 22/07/2026)

> Documento-guia: **"Plano-de-Execucao-Faturamento-RH - Daniel.pdf"** (22/07/2026, responsável Daniel, acompanhamento Matheus/Grupo Opus).
> A partir de 22/07 todo spec e commit referencia uma etapa deste plano. Este arquivo é o espelho vivo do status — atualizar a cada avanço.

## Legenda

- ✅ concluído · 🔨 código pronto, falta deploy/validação · ⏸️ em espera (dependência externa) · ⬜ não iniciado · 🔁 recorrente

---

## Etapa 1 — Pendências rápidas de produção e dados (BAIXA)

| # | Tarefa | Status | Situação real no código |
|---|--------|--------|--------------------------|
| 1.1 | Modelos Skyrail e "Geral Total" em produção | 🔨 | Código pronto em dev: `BillingModel` (modelo GERAL base em `app/db.py`), export Skyrail em `app/routers/integrations.py` (`/senior/billing/export-skyrail`), fórmula de salário projetado segura em `app/services/formula_salario.py`. **Falta: deploy na VPS + validação com uma competência real.** |
| 1.2 | Protocolar chamado do módulo de Benefícios na Senior | ⏸️ | Texto já redigido. Ação externa: protocolar e registrar número + previsão. |
| 1.3 | Popular preços do catálogo TOTVS (com a Operação) | ⬜ | Tela `/catalogo-produtos` pronta (4.147 itens importados, preço editável). Falta popular os preços — acompanhamento semanal do % precificado. |
| 1.4 | Montar catálogo de treinamentos (com a Operação) | ⬜ | Tela `/catalogo-treinamentos` pronta e **já ligada ao faturamento** (TrainingRecord → coluna TREINAMENTOS → Sub-Total; verificado 22/07). Falta popular os treinamentos dos contratos ativos. |

**Conclusão da etapa:** bloqueada apenas por deploy (1.1) e ações operacionais/externas (1.2–1.4). Não há desenvolvimento novo pendente.

## Etapa 2 — Decisões de negócio com prazo (BAIXA · RETORNO ALTO ~R$ 35 mil/mês)

> **STATUS GERAL: ⏸️ EM ESPERA** — a reunião única de decisão **já foi agendada**, mas **ainda sem resposta** da gestão do contrato. Permanece em espera até a resposta; ao confirmar, pauta fechada com os 4 itens abaixo e decisão na sala.

| # | Decisão | Status | Suporte no sistema hoje |
|---|---------|--------|--------------------------|
| 2.1 | Alocação dos eventos não faturados (horas extras, adicionais, dissídio — ~R$ 35 mil/mês nos CCUs FEMSA) | ⏸️ | Classificação hoje é implícita (evento sem mapeamento em `EVENT_TO_FEMSA_MAPPING` é descartado). Quando decidido: criar flag/whitelist explícita e aplicar. |
| 2.2 | Critério de salário Skyrail: projetado × realizado | ⏸️ | Ambos suportados — `formula_salario.py` permite fórmula própria (ex.: `salario / 29 * 30`). Falta a decisão contratual + configuração no modelo. |
| 2.3 | Valor contratual do seguro de vida + rescisões no faturamento | ⏸️ | Coluna SEGURO DE VIDA mapeável via `/beneficios` (BenefitEvent); rescisões entram como eventos Senior (ex.: 1550 → SALDO SALARIO DIA RESCISAO). Falta parametrizar o valor/regra decididos. |
| 2.4 | Ata/documento de critérios de faturamento por contrato | ⬜ | Não existe. `contract_params` guarda só 3 percentuais (encargos, taxa adm, imposto) do contrato padrão. Criar documento versionado após a reunião. |

## Etapa 3 — Conciliação com a contabilidade (MÉDIA · 1–2 semanas)

| # | Tarefa | Status | Situação |
|---|--------|--------|----------|
| 3.1 | Documentar diferença de recorte (competência inteira × relatório mensal Senior) com exemplos reais | ⬜ | Conhecimento levantado (WS soma todos os ~10 codcal da competência; relatório "mensal" é recorte; 132/132 eventos batem) mas **sem documento formal**. |
| 3.2 | Relatório de conciliação no sistema (ponte entre recortes) | ⬜ | **Nada no código** — nenhuma rota/tela/serviço de conciliação. É o próximo desenvolvimento (spec 004). |
| 3.3 | Follow-up com a Senior: marcação de tipo de cálculo (TIPCAL) | ⏸️ 🔁 | Pendência conhecida; registrar formalmente junto à Senior com follow-up mensal. |

## Etapa 4 — Testes automatizados dos cálculos (MÉDIA · 2–3 semanas)

| # | Tarefa | Status | Situação |
|---|--------|--------|----------|
| 4.1 | Tabelar casos de teste (encargos, taxa adm, impostos, gross-up + bordas) | ⬜ | Não existe. |
| 4.2 | Automatizar suíte (unidade + ponta a ponta por modelo) | ⬜ | **Zero testes no repo** — sem `tests/`, sem pytest em `requirements.txt`/`pyproject.toml`. |
| 4.3 | Cobrir importação de modelo por planilha | ⬜ | Não existe. |

**Regra de governança:** a partir da conclusão desta etapa, **deploy só com suíte verde**.

## Etapa 5 — Confiabilidade da operação (MÉDIA · 2–3 semanas)

Contexto: servidor único ficou 4 dias fora do ar (18–22/jul) — risco operacional nº 1.

| # | Tarefa | Status | Situação |
|---|--------|--------|----------|
| 5.1 | Monitoramento com alerta (<5 min) do servidor, app e integração Senior | ⬜ | Existem `GET /health` e `/health/ping`, e `monitor_buffer.py` (eventos SOAP, só DEV_MODE). **Sem alerta externo/uptime check.** |
| 5.2 | Backup diário automatizado + teste de restauração | ⬜ | Volume Docker + `dump.sql` de init + backups manuais em `~/backups` na VPS. **Sem cron de pg_dump nem restore testado/registrado.** |
| 5.3 | Runbook de contingência de fechamento | ⬜ | `RUNBOOK.md` cobre setup/migrações/troubleshooting, mas não o cenário "servidor caiu na semana de faturamento". |
| 5.4 | Proposta de redundância mínima com custo | ⬜ | Não existe. |

## Etapa 6 — Higiene técnica e navegação (MÉDIA · 1–2 semanas)

| # | Tarefa | Status | Situação |
|---|--------|--------|----------|
| 6.1 | Aposentar telas/trilhos legados | ⬜ | Nada marcado como legado explicitamente; candidatos a revisar: `upload_page.html` × `data_upload.html` (rotas `/upload` e `/data-upload` coexistem), pacotes EPI "(legado)". Mapear e remover. |
| 6.2 | Padronizar navegação entre módulos | ⬜ | Base institucional (`base.html`) já é o padrão das telas novas; validar uniformidade com operadores. |
| 6.3 | Varredura de permissões pós-remoção | ⬜ | Base boa: em 22/07 todas as rotas passaram a exigir login + RBAC gestor/admin (backdoor removido). Refazer a varredura **após** remover o legado. |

## Etapa 7 — Módulo de Benefícios da Senior (ALTA · depende de terceiro)

Corre em paralelo desde o protocolo do chamado (1.2).

| # | Tarefa | Status | Situação |
|---|--------|--------|----------|
| 7.1 | Follow-up quinzenal do chamado (escala à gestão após 60 dias) | ⏸️ 🔁 | Aguarda o protocolo (1.2). |
| 7.2 | Integrar custo real dos benefícios quando o módulo for exposto | ⏸️ | Depende da Senior. |
| 7.3 | Manter aproximação por evento com limitação documentada | 🔨 | Aproximação implementada (`BenefitEvent` + tela `/beneficios`, eventos → colunas FEMSA). **Falta a nota de limitação visível no relatório de faturamento.** |

---

## Fila de desenvolvimento derivada (specs)

Etapas 1–2 não têm dev novo (deploy + decisões). Sequência de specs conforme a ordem do plano:

1. **004 — Relatório de conciliação contábil** (Etapa 3.2, inclui doc do 3.1)
2. **005 — Suíte de testes dos cálculos críticos** (Etapa 4)
3. **006 — Confiabilidade: monitoramento, backup e contingência** (Etapa 5)
4. **007 — Higiene técnica e navegação** (Etapa 6)
5. **008 — Benefícios pelo custo real Senior** (Etapa 7 — quando a Senior expor o módulo)
6. Pós-reunião da Etapa 2: parametrizações decididas (eventos não faturados, Skyrail, seguro/rescisões) + documento de critérios

## Pendências externas (monitorar, com dono e data)

| Pendência | Dono | Status |
|-----------|------|--------|
| Reunião de decisões da Etapa 2 | Gestão do contrato | ⏸️ agendada, **aguardando resposta** |
| Chamado módulo de Benefícios na Senior | Daniel (protocolo) / Senior (resposta) | ⏸️ texto pronto, protocolar |
| TIPCAL (tipo de cálculo) na Senior | Senior | ⏸️ registrar com follow-up mensal |
| Preços TOTVS + catálogo de treinamentos | Operação | ⬜ acompanhamento semanal |
| Deploy do código novo na VPS | Daniel | 🔨 pendente |
