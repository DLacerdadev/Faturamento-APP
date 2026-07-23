# Tasks: Relatório de Conciliação Contábil

**Feature**: [spec.md](spec.md) · [plan.md](plan.md) · [data-model.md](data-model.md) · [contracts/rest-endpoints.md](contracts/rest-endpoints.md)
**Status**: Draft
**Origem**: Plano de Execução — Etapa 3 ([docs/PLANO-EXECUCAO-STATUS.md](../../docs/PLANO-EXECUCAO-STATUS.md))

## Format

`- [ ] T### [P?] [US#?] Description with file path`

- **T###** — sequential ID
- **[P]** — parallelizable (different files, no dependency on incomplete task)
- **[US#]** — belongs to user story (required in user story phases)

## User Stories (mapeadas dos cenários da spec)

- **US1 (P1)** — Gestor gera a conciliação de uma competência e vê a ponte (competência inteira → fora do recorte → recorte mensal), decomposição por codcal→evento e resíduo. *(Scenarios 1, 5)* — **MVP**
- **US2 (P1)** — Classificar codcal (global) na tela para o resíduo fechar; codcal novo aparece como "não classificado" e trava o status `fechada`. *(Scenarios 1, 3)*
- **US3 (P2)** — Exportar a conciliação em planilha (Resumo/Decomposição/Eventos) para a contabilidade conferir. *(Scenario 2)*
- **US4 (P3)** — Documento de conciliação aprovado + validação real que fecha a Etapa 3. *(FR-8/FR-9, SC-1/SC-2/SC-5)*
- **Transversal** — Acesso gestor+ e auditoria em toda a feature. *(Scenario 4, FR-7)*

---

## Phase 1 — Setup

- [x] T001 Criar a estrutura de pastas/arquivos-esqueleto da feature: `app/models/codcal_classification.py`, `app/services/conciliacao.py`, `app/routers/conciliacao.py`, `app/templates/conciliacao.html` (stubs vazios com imports base), sem lógica ainda.

## Phase 2 — Foundational (blocking)

- [x] T002 Implementar o model `CodcalClassification` em `app/models/codcal_classification.py` conforme [data-model.md](data-model.md) (colunas: id, codcal unique/index, descricao, recorte_mensal, origem default "manual", observacao, created_at, updated_at).
- [x] T003 Registrar o novo model em `app/models/__init__.py` (import + `__all__`) para o `Base.metadata` enxergar a tabela.
- [x] T004 Garantir criação idempotente da tabela `codcal_classifications` no `init_db` de `app/db.py` (mesmo mecanismo das tabelas 001–003) e documentar a migração aditiva no `RUNBOOK.md`.
- [x] T005 Registrar o router de conciliação em `app/main.py` (`from app.routers.conciliacao import router as conciliacao_router` + `app.include_router(...)`).
- [x] T006 Adicionar o item "Conciliação" no menu lateral de `app/templates/base.html` (bloco de nav, com marcação `active` por path), seguindo o padrão dos demais links.

**Checkpoint**: app sobe, tabela existe, rota registrada e menu aparece — sem funcionalidade ainda.

---

## Phase 3 — US1 (P1): Gerar e ver a conciliação

**Goal**: Gestor seleciona competência (e CCU opcional), dispara a geração via job assíncrono e vê totais, ponte, resíduo e decomposição por codcal→evento (agregado, sem dados de funcionário).
**Independent test**: Em DEV_MODE, gerar uma competência com dados locais e conferir os cards de totais, o resíduo e o drill-down por evento sem nenhum nome/CPF; os números batem com a soma da folha local.

- [x] T007 [US1] Implementar `montar_conciliacao(payroll_rows, classificacoes)` (função pura) em `app/services/conciliacao.py`: agrega eventos por (codcal, codigo_evento) com valor total (com sinal) e qtde de lançamentos; calcula competência inteira / recorte mensal / fora / resíduo; define status (`fechada`/`incompleta`/`com_residuo`); lista `nao_classificados`. Nenhum campo de funcionário no retorno. Estrutura de saída conforme [data-model.md](data-model.md).
- [x] T008 [US1] Implementar `_run_conciliacao_job(job_id, periodo, codccu)` em `app/routers/conciliacao.py`: chama `fetch_payroll(periodo, numemp=6, codccu, progress_cb=->set_progress)` (todos os CCUs via `fetch_all_cost_centers` quando `codccu` ausente), carrega classificações do banco, chama `montar_conciliacao`, serializa JSON e `finish_ok(job_id, json_bytes, "conciliacao_<periodo>.json", "application/json")`; erro → `finish_error` (P2: prod barulhento).
- [x] T009 [US1] Endpoint `POST /api/conciliacao/gerar` em `app/routers/conciliacao.py`: valida payload (Pydantic `ConciliacaoGerarIn`: periodo, codccu opcional), `require_role(..., "gestor")`, `create_job`, dispara `threading.Thread(_run_conciliacao_job, daemon=True)`, `audit("conciliacao.gerar", ...)`, retorna `{success, job_id}`.
- [x] T010 [P] [US1] Endpoint `GET /api/conciliacao/status/{job_id}` em `app/routers/conciliacao.py`: retorna status/percent/message/error do `ExportJob` (404 se expirado).
- [x] T011 [P] [US1] Endpoint `GET /api/conciliacao/resultado/{job_id}` em `app/routers/conciliacao.py`: retorna o JSON da conciliação quando `done` (409 se em andamento, 404 se expirado).
- [x] T012 [US1] View `GET /conciliacao` (HTMLResponse) em `app/routers/conciliacao.py`: `require_role(..., "gestor")` (redirect 303 `/login` sem sessão), renderiza `conciliacao.html` com user/token/lista de CCUs.
- [x] T013 [US1] Construir `app/templates/conciliacao.html` herdando `base.html`: seletor de competência + CCU, botão Gerar, barra de progresso (poll em `/status`), cards de totais (competência inteira / recorte mensal / fora / resíduo + badge de status), tabela de decomposição por codcal.
- [x] T014 [US1] JS (inline no template ou `app/static/`) de poll do job e render dos cards + tabela a partir do `/resultado`; drill-down por evento (expandir codcal → eventos agregados). Reusar padrão do JS da tela de exportação.

**Checkpoint**: US1 entregue — geração e visualização funcionam ponta a ponta (com tudo "não classificado" ainda).

---

## Phase 4 — US2 (P1): Classificar codcal

**Goal**: Gestor classifica cada codcal (descrição + mensal/fora) na própria tela; a classificação é global e auditada; codcal sem linha aparece destacado como "não classificado" e o status só vira `fechada` quando tudo estiver classificado.
**Independent test**: Partindo de uma conciliação `incompleta`, classificar todos os codcal e ver o status migrar para `fechada` com resíduo R$ 0,00; remover uma classificação e ver voltar a `incompleta`.

- [x] T015 [P] [US2] Endpoint `GET /api/conciliacao/classificacoes` em `app/routers/conciliacao.py`: lista as classificações (require gestor+).
- [x] T016 [US2] Endpoint `PUT /api/conciliacao/classificacoes/{codcal}` (upsert) em `app/routers/conciliacao.py`: Pydantic `ClassificacaoIn` (descricao, recorte_mensal, observacao, `origem` opcional restrita a `manual`|`heuristica`, default `manual`; `oficial` → 422), grava a origem recebida, `audit("conciliacao.classificar", detalhe={antes, depois})`, retorna item. (FR-3, FR-4)
- [x] T017 [P] [US2] Endpoint `DELETE /api/conciliacao/classificacoes/{codcal}` em `app/routers/conciliacao.py`: remove a classificação (codcal volta a "não classificado"), `audit("conciliacao.classificar", detalhe={antes, depois:null})` com estado anterior, 404 se inexistente. (FR-3 — reverter classificação; sustenta SC-3)
- [x] T018 [US2] Na tela `conciliacao.html`: destaque visual dos codcal "não classificados", edição inline (descrição + toggle mensal/fora + observação) chamando o PUT e ação de remover (DELETE) com confirmação; re-render dos totais/status após salvar.
- [x] T019 [US2] Sugestão de heurística na tela (não grava sozinho): para codcal não classificado, sugerir classificação a partir dos eventos agregados (ex.: presença de "SALARIO DIA" → provável mensal); ao aceitar, o gestor grava via PUT com `origem="heuristica"` (digitar do zero grava `origem="manual"`). Nada é classificado silenciosamente (SC-3, FR-4).

**Checkpoint**: US2 entregue — a ponte fecha; nenhum codcal novo passa despercebido.

---

## Phase 5 — US3 (P2): Exportar planilha

**Goal**: Exportar a conciliação em .xlsx (abas Resumo/Decomposição/Eventos) derivada do JSON retido no job — sem segunda ida ao WS.
**Independent test**: Após uma geração `done`, exportar e conferir as 3 abas contra a tela; export após >1h retorna 404 com orientação.

- [x] T020 [US3] Função `conciliacao_para_xlsx(resultado_json) -> bytes` em `app/services/conciliacao.py` (openpyxl): aba Resumo (período, CCU código+nome, gerado em/por, totais, status), aba Decomposição (por codcal), aba Eventos (codcal×evento). Sem dados de funcionário.
- [x] T021 [US3] Endpoint `GET /api/conciliacao/export/{job_id}` em `app/routers/conciliacao.py`: lê o JSON retido no `ExportJob`, chama `conciliacao_para_xlsx`, `StreamingResponse` com `Content-Disposition` (`Conciliacao_<periodo>[_<ccu>].xlsx`), `audit("conciliacao.export", ...)`; 409/404 conforme contrato.
- [x] T022 [US3] Botão "Exportar planilha" em `conciliacao.html` habilitado quando o job está `done`, apontando para `/export/{job_id}`.

**Checkpoint**: US3 entregue — contabilidade recebe a planilha da ponte.

---

## Phase 6 — US4 (P3): Documento de conciliação + validação real

**Goal**: Documentar a diferença de recorte com exemplos reais e validar o ciclo real (fecha a Etapa 3 do Plano de Execução).
**Independent test**: `docs/CONCILIACAO.md` existe com 2 exemplos numéricos reais (só totais/codcal), seções de aprovação e follow-up TIPCAL; conferência de uma competência real fecha usando apenas a planilha.

- [x] T023 [P] [US4] Criar `docs/CONCILIACAO.md`: explicação do recorte (competência inteira × relatório mensal Senior), placeholder para 2 exemplos numéricos reais (somente totais/codcal — sem dados pessoais), seção "Aprovação" (nome/função/data) e seção "Pendência TIPCAL na Senior" com data do último follow-up.
- [x] T024 [US4] Linkar `docs/CONCILIACAO.md` a partir da tela `conciliacao.html` (nota/atalho para o documento de critérios).
- [ ] T025 [US4] Validação real (quickstart, prod): gerar competência já conferida, classificar os ~10 codcal, confirmar resíduo R$ 0,00 (SC-2), bater o recorte mensal contra o relatório Senior; preencher os exemplos reais no `docs/CONCILIACAO.md` e colher aprovação (SC-5). Atualizar `docs/PLANO-EXECUCAO-STATUS.md` (Etapa 3).

---

## Phase 7 — Polish & Cross-Cutting

- [x] T026 [P] Revisar cobertura de auditoria e RBAC de todos os endpoints (gestor+ em 100%, operador → 403) — Scenario 4/FR-7.
- [x] T027 [P] Tratar edge cases na tela e no serviço: competência sem dados (mensagem clara), WS fora em prod na geração (job `error` + tentar de novo), lista de CCUs indisponível no carregamento da tela (renderiza com aviso + entrada manual/retry, sem quebrar), valores negativos somados com sinal, job expirado (>1h) — conforme "Edge Cases" da spec e contrato.
- [x] T028 [P] Conferir aderência ao design system (`base.html`, JetBrains Mono, cards, paleta) e ao SC-4 (geração < 2 min com todos os CCUs, aproveitando cache/throttle da feature 003).
- [x] T029 Atualizar o bloco `<!-- SPECKIT -->` do `CLAUDE.md` para status "implementada" quando US1–US3 estiverem no ar.

---

## Dependencies

```text
Phase 1 (Setup) → Phase 2 (Foundational) → US1 → US2 → US3 → US4 → Polish
```

- US2 depende de US1 (precisa da geração/tela para classificar e ver o status mudar).
- US3 depende de US1 (exporta o resultado gerado); independe de US2 (exporta mesmo `incompleta`).
- US4 depende de US1–US3 (valida o fluxo completo em ciclo real).
- Transversal (auditoria/RBAC) é aplicada dentro de cada endpoint à medida que é criado; T026 é a revisão final.

## Parallel Opportunities

- **Foundational**: T005 (main.py) e T006 (base.html) em paralelo após T002–T004.
- **US1**: T010 [P] + T011 [P] (endpoints de status/resultado, arquivos/handlers distintos) após T008–T009.
- **US2**: T015 [P] + T017 [P] (GET lista + DELETE) em paralelo.
- **US4**: T023 [P] (documento) enquanto US3 é finalizada.
- **Polish**: T026 [P] + T027 [P] + T028 [P].

## MVP Scope

**US1 + US2** entregam o incremento mínimo utilizável: gerar a conciliação e classificar os codcal até o resíduo fechar — já responde "a ponte bate?". US3 (export) é o que torna a conferência autônoma pela contabilidade; US4 fecha formalmente a Etapa 3.
