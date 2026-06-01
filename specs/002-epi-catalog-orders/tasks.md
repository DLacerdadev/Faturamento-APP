# Tasks: Catálogo de EPIs e Pedido de Compra com Solicitação

**Feature**: [spec.md](spec.md) · [plan.md](plan.md) · [research.md](research.md) · [data-model.md](data-model.md) · [contracts/rest-endpoints.md](contracts/rest-endpoints.md) · [quickstart.md](quickstart.md)
**Status**: Implementação em disco completa — pendente validação E2E (T015, T026, T031, T034, T036)

## User Stories (derivadas das Acceptance Scenarios da spec)

| ID | Prioridade | Objetivo | Mapeia para |
|---|---|---|---|
| **US1** | **P1 — MVP** | Cadastrar e gerenciar EPIs num catálogo (nome + tamanhos + valor por tamanho), com soft-delete e busca | AC1, AC2 |
| **US2** | **P1 — MVP** | Criar pedido de compra escolhendo EPI/tamanho do catálogo, com cálculos persistidos e Excel de solicitação gerado ao salvar | AC3, AC4, AC5, AC6 |
| **US3** | **P2** | Enviar a solicitação de compra por email (quando SMTP configurado) com destinatário editável | FR-16 (parte email) |
| **US4** | **P3** | Tratar pedidos legados da feature 001 com badge visual e ações de solicitação desabilitadas | AC7, FR-17 |

## Format

`- [ ] T### [P?] [US#?] Descrição com caminho`

---

## Phase 1 — Setup

- [x] T001 Aplicar migração 002 no `app.db` rodando o SQL da seção "Setup" do [quickstart.md](quickstart.md) (2 CREATE TABLE + 9 ALTER TABLE ADD COLUMN + 3 índices). Validar com `python -c "import sqlite3; print([r[0] for r in sqlite3.connect('app.db').execute('SELECT name FROM sqlite_master WHERE type=\"table\" AND name LIKE \"epi_catalog%\"').fetchall()])"` — esperado: `['epi_catalog', 'epi_catalog_sizes']`.
- [x] T002 Adicionar bloco de variáveis SMTP + `EPI_PURCHASE_EMAIL` no [.env.example](FATURAMENTO-APP/.env.example) com valores vazios/exemplo, e copiar o mesmo bloco em branco no [.env](FATURAMENTO-APP/.env) (sem expor credenciais).

## Phase 2 — Foundational (bloqueia todas as User Stories)

- [x] T003 [P] Adicionar classes `EpiCatalog` e `EpiCatalogSize` em [FATURAMENTO-APP/app/models/epi_purchase.py](FATURAMENTO-APP/app/models/epi_purchase.py) conforme [data-model.md](data-model.md): `EpiCatalog(id, nome, ativo, created_at, updated_at)` com relationship `sizes` (cascade); `EpiCatalogSize(id, epi_id FK CASCADE, tamanho, valor)` com `back_populates="catalog_entry"`.
- [x] T004 [P] Adicionar campos novos em `EpiPurchasePackage` ([FATURAMENTO-APP/app/models/epi_purchase.py](FATURAMENTO-APP/app/models/epi_purchase.py)): `solicitante_nome` (String 200, nullable), `quantidade_total_geral` (Integer, nullable), `valor_total_compra_geral` (Float, nullable), `solicitacao_filename` (String 500, nullable), `solicitacao_generated_at` (DateTime, nullable).
- [x] T005 [P] Adicionar campos novos em `EpiPurchaseItem` ([FATURAMENTO-APP/app/models/epi_purchase.py](FATURAMENTO-APP/app/models/epi_purchase.py)): `epi_id` (Integer, FK `epi_catalog.id`, nullable, indexed), `tamanho` (String 20, nullable), `quantidade_por_funcionario` (Integer, nullable), `valor_unitario_catalogo` (Float, nullable).
- [x] T006 [P] Adicionar vars SMTP em [FATURAMENTO-APP/app/config.py](FATURAMENTO-APP/app/config.py) (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`, `EPI_PURCHASE_EMAIL`) + helper `is_smtp_configured() -> bool` que retorna `bool(SMTP_HOST and SMTP_FROM)`.
- [x] T007 Atualizar [FATURAMENTO-APP/RUNBOOK.md](FATURAMENTO-APP/RUNBOOK.md): adicionar seção "Migração 002 — Catálogo de EPIs e Solicitação" depois da "Migração 001" com o SQL completo + conferência.

---

## Phase 3 — User Story 1 (P1): Catálogo CRUD

**Goal**: usuário gerencia um catálogo de EPIs (cadastro, edição, desativação, reativação, busca) numa tela dedicada `/catalogo-epis`. Cada EPI tem N tamanhos com valor por tamanho.

**Independent test**: executar Cenários 1, 2, 3 do [quickstart.md](quickstart.md) — cadastra "Luva de raspa" com 3 tamanhos, "Protetor solar" com 1 tamanho, e tenta criar duplicata (bloqueado).

- [x] T008 [US1] [P] Adicionar Pydantic schemas em novo arquivo [FATURAMENTO-APP/app/routers/epi_catalog.py](FATURAMENTO-APP/app/routers/epi_catalog.py): `SizeInput { tamanho: str(min=1, max=20), valor: float(gt=0) }`, `EpiCatalogCreate { nome: str(min=1, max=200), sizes: list[SizeInput](min_length=1) }`. Validação extra de tamanhos duplicados.
- [x] T009 [US1] Implementar endpoints CRUD em [FATURAMENTO-APP/app/routers/epi_catalog.py](FATURAMENTO-APP/app/routers/epi_catalog.py) (`prefix="/api/epi-catalog"`): `GET /` (lista paginada com filtro `q`, `include_inactive`, com `in_use_count` agregado), `GET /{id}`, `POST /`, `PUT /{id}`, `DELETE /{id}` (soft-delete), `POST /{id}/reactivate`. Validações de unicidade (UPPER(nome) case-insensitive entre ativos) e de tamanhos duplicados → retornar 409.
- [x] T010 [US1] Registrar router em [FATURAMENTO-APP/app/main.py](FATURAMENTO-APP/app/main.py): adicionar `from app.routers.epi_catalog import router as epi_catalog_router` e `app.include_router(epi_catalog_router)` junto dos demais.
- [x] T011 [US1] Criar template [FATURAMENTO-APP/app/templates/catalogo_epis.html](FATURAMENTO-APP/app/templates/catalogo_epis.html) herdando `base.html`: card "Novo EPI" (campo nome + tabela dinâmica de tamanhos com botão + e ×) + card "EPIs Cadastrados" (busca por nome + tabela com colunas Nome / Tamanhos / Status / Em uso / Ações). Reuso do design system (paleta, JetBrains Mono, cards).
- [x] T012 [US1] Adicionar rota `GET /catalogo-epis` em [FATURAMENTO-APP/app/main.py](FATURAMENTO-APP/app/main.py) seguindo padrão de `/epis`: valida token, retorna `TemplateResponse("catalogo_epis.html", ...)`.
- [x] T013 [US1] [P] Implementar JS em [FATURAMENTO-APP/app/templates/catalogo_epis.html](FATURAMENTO-APP/app/templates/catalogo_epis.html): (a) `loadCatalog()` chama `GET /api/epi-catalog?q=...&include_inactive=...`. (b) Form: `addSizeRow()`/`removeSizeRow()`. (c) `submitEpi()` POSTa, em sucesso reseta form e recarrega lista; em erro mostra toast. (d) `editEpi(id)` carrega via GET, popula form, troca botão pra "Atualizar". (e) `toggleActive(id)` chama DELETE ou reactivate conforme estado, com confirm dialog quando `in_use_count > 0`.
- [x] T014 [US1] Adicionar link "Catálogo de EPIs" na nav de [FATURAMENTO-APP/app/templates/base.html](FATURAMENTO-APP/app/templates/base.html) entre "Clientes" e "EPIs": `<li><a href="/catalogo-epis?token={{ token }}" class="{% if '/catalogo-epis' in request.url.path %}active{% endif %}">Catálogo EPIs</a></li>`.
- [ ] T015 [US1] Validar Cenários 1, 2, 3 do [quickstart.md](quickstart.md) end-to-end (criar EPI multi-tamanho, criar EPI tamanho único, tentar duplicar nome). Documentar OK no commit.

**Checkpoint US1**: catálogo funcional e isolado. Pode ser shipado sozinho como parte do MVP.

---

## Phase 4 — User Story 2 (P1): Pedido com catálogo + Excel auto-gerado

**Goal**: ao criar/editar pedido em `/epis`, o usuário escolhe EPI + tamanho do catálogo (em vez de digitar). Valor é preenchido do catálogo, editável com aviso de divergência. Sumários (qtde total por item, valor total por item, total geral da compra) atualizam em tempo real. Ao salvar, totais são persistidos e um Excel de solicitação é gerado automaticamente, com link de download imediato.

**Independent test**: executar Cenários 4, 5, 6 do [quickstart.md](quickstart.md) — criar compra com 5 funcionários × 2 itens (10+5 linhas), conferir totais R$ 245,00, baixar Excel; testar override de valor com aviso; testar reatividade dos totais ao mudar funcionários.

- [x] T016 [US2] [P] Atualizar Pydantic schemas em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): substituir `EpiItemInput` pela nova versão `EpiItemInputV2 { epi_id: int, tamanho: str, quantidade_por_funcionario: int(ge=1), valor_unitario: float(gt=0) }`. Atualizar `EpiPackageCreateV2.items` para usar o novo schema.
- [x] T017 [US2] Atualizar `POST /api/epi-purchases` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): (a) validar que cada `item.epi_id` existe e está ativo (`ativo=True`), retornando 400 se não. (b) validar que `(epi_id, tamanho)` existe em `epi_catalog_sizes`. (c) carregar `valor_unitario_catalogo` da tabela `epi_catalog_sizes`. (d) preencher `solicitante_nome = user.full_name or user.email` a partir da session. (e) no `_expand_cartesian`, salvar `epi_id`, `tamanho`, `quantidade_por_funcionario`, `valor_unitario_catalogo` em cada linha; `descricao` recebe snapshot do `epi_catalog.nome`. (f) calcular `quantidade_total_geral` e `valor_total_compra_geral` somando todas as linhas e persistir no pacote.
- [x] T018 [US2] Atualizar `PUT /api/epi-purchases/{id}` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): mesma lógica do POST (validação contra catálogo + snapshot + cálculo de totais). Após `_expand_cartesian`, recalcular totais e atualizar pacote.
- [x] T019 [US2] Atualizar `package_to_dict()` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): adicionar campos `solicitante_nome`, `is_legacy` (computed: `all(item.epi_id IS NULL for item in items)`), `totais.quantidade_total_geral`, `totais.valor_total_compra_geral`, e novo bloco `agrupado_v2.itens` com `(epi_id, epi_nome, tamanho, quantidade_por_funcionario, valor_unitario, valor_unitario_catalogo, valor_unitario_difere_do_catalogo, quantidade_total_item, valor_total_item)` distintos.
- [x] T020 [US2] [P] Criar [FATURAMENTO-APP/app/services/epi_solicitation_excel.py](FATURAMENTO-APP/app/services/epi_solicitation_excel.py) com função pública `generate_solicitacao_xlsx(pkg: EpiPurchasePackage) -> bytes`. Layout (R6 da [research.md](research.md)): cabeçalho com empresa/CCU/competência/solicitante/data, tabela de itens distintos com colunas (Nome EPI, Tamanho, Qtde/func, Func. atendidos, Qtde total, Valor unit., Valor total), linha de TOTAL GERAL em destaque, e bloco "Funcionários atendidos" listando matrícula + nome. Usar `openpyxl`.
- [x] T021 [US2] Integrar geração de Excel no POST e PUT de [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): após commit do pacote, chamar `generate_solicitacao_xlsx(pkg)`, gravar bytes em `GENERATED_REPORTS_DIR / f"solicitacao_epi_{pkg.id}_{timestamp}.xlsx"`, e dar UPDATE no pacote setando `solicitacao_filename` e `solicitacao_generated_at`. No PUT, apagar arquivo anterior se existir.
- [x] T022 [US2] Implementar `GET /api/epi-purchases/{id}/solicitacao` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): retorna FileResponse do arquivo em `GENERATED_REPORTS_DIR / pkg.solicitacao_filename`. 404 se pacote inexistente ou filename NULL; 410 se filename existe mas arquivo sumiu do disco.
- [x] T023 [US2] Atualizar [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): trocar a tabela de itens livres por: dropdown "EPI" (autocomplete buscando `/api/epi-catalog?q=`), dropdown "Tamanho" reativo ao EPI (carrega `sizes` daquele EPI), input "Qtde por funcionário", input "Valor unit." (pré-preenchido do catálogo, com aviso amarelo quando diverge). Cada linha mostra qtde total e valor total do item calculados.
- [x] T024 [US2] [P] Atualizar JS em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): (a) `loadCatalog()` cacheia `/api/epi-catalog`. (b) `onEpiSelect(rowIndex)` repopula dropdown de tamanho e preenche valor. (c) `onValorChange(rowIndex)` compara com catálogo e mostra/oculta aviso de divergência. (d) `updateCounters()` (já existe) inclui total geral somando todos os itens. (e) `submitPurchase()` envia `items: [{epi_id, tamanho, quantidade_por_funcionario, valor_unitario}, ...]` no novo formato. (f) Após sucesso, mostrar bloco "Solicitação gerada" com link `/api/epi-purchases/{id}/solicitacao`.
- [x] T025 [US2] Atualizar `openPackage(id)` em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html) para usar `agrupado_v2.itens` (em vez de `agrupado.itens`) ao reabrir compra para edição: cada item recebe epi_id selecionado, tamanho, qtde por funcionário e valor.
- [ ] T026 [US2] Validar Cenários 4, 5, 6 do [quickstart.md](quickstart.md) end-to-end. Conferir Excel baixado tem cabeçalho + 2 itens + total R$ 245,00 + bloco de 5 funcionários.

**Checkpoint US2**: feature core completa. Catálogo + pedido + Excel funcionam end-to-end. MVP entregável.

---

## Phase 5 — User Story 3 (P2): Envio por email da solicitação

**Goal**: quando `SMTP_HOST` está configurado, usuário clica em "Enviar por email" no pedido salvo, edita opcionalmente o destinatário (default = `EPI_PURCHASE_EMAIL`) e o sistema envia a solicitação Excel como anexo. Quando SMTP não está configurado, o botão fica esmaecido com tooltip.

**Independent test**: executar Cenário 7 do [quickstart.md](quickstart.md) — com SMTP configurado, enviar e confirmar entrega. Sem SMTP, confirmar botão esmaecido.

- [x] T027 [US3] [P] Criar [FATURAMENTO-APP/app/services/email_sender.py](FATURAMENTO-APP/app/services/email_sender.py) com função `send_solicitacao_email(to: str, subject: str, body: str, attachment_bytes: bytes, attachment_filename: str, cc: Optional[str] = None)` usando `smtplib.SMTP` ou `SMTP_SSL` (conforme `SMTP_USE_TLS`), `email.mime.multipart.MIMEMultipart` e `email.mime.application.MIMEApplication` para o anexo. Usa as vars de `app.config`. Levanta exceção em falha de envio.
- [x] T028 [US3] Implementar `POST /api/epi-purchases/{id}/solicitacao/email` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): body `{to?: str, cc?: str, subject?: str}`. Se `is_smtp_configured() == False`, retornar 503 com mensagem. Se `solicitacao_filename IS NULL`, retornar 404. Carregar bytes do arquivo, montar subject default (`Solicitação de compra de EPI #{id} — {empresa} — {mes_ano}`) se vazio, chamar `send_solicitacao_email`. Retornar 200 com mensagem de sucesso.
- [x] T029 [US3] [P] Implementar `GET /api/epi-purchases/smtp-status` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): retorna `{smtp_configured: bool, default_recipient: str}` lendo de `app.config`.
- [x] T030 [US3] Atualizar JS de [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): no `DOMContentLoaded`, chamar `GET /api/epi-purchases/smtp-status` e armazenar `window.smtpAvailable`. Após salvar uma compra, mostrar botão "Enviar por email" (habilitado se `smtpAvailable=true`, esmaecido com tooltip "SMTP não configurado no servidor" caso contrário). Clique abre modal com input pré-preenchido (default_recipient), botão "Enviar" chama `POST .../email`.
- [ ] T031 [US3] Validar Cenário 7 do [quickstart.md](quickstart.md). Se não houver SMTP configurado no ambiente do usuário, validar apenas o estado esmaecido do botão.

**Checkpoint US3**: ciclo completo (cadastro → compra → Excel → email).

---

## Phase 6 — User Story 4 (P3): Tratamento visual de pedidos legados da 001

**Goal**: pedidos antigos sem `epi_id` no banco aparecem na listagem com badge "Legado" e botões de solicitação (download/email) desabilitados com tooltip explicativo. Nenhuma migração automática.

**Independent test**: executar Cenário 8 do [quickstart.md](quickstart.md) — confirmar visual do badge e tooltips.

- [x] T032 [US4] [P] Atualizar `GET /api/epi-purchases` em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py) (caso ainda não cubra): garantir que `is_legacy` é calculado por pacote (linhas com `epi_id IS NULL`) e devolvido em cada item da lista.
- [x] T033 [US4] Atualizar `renderPackages()` em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): mostrar `<span class="legacy-badge">Legado</span>` ao lado do `#id` quando `p.is_legacy === true`. Desabilitar (visualmente e com `disabled`) os botões "Baixar solicitação" e "Enviar por email" nesses pacotes, com `title` explicativo ("Compra criada antes do catálogo de EPIs…").
- [ ] T034 [US4] Validar Cenário 8 do [quickstart.md](quickstart.md) — abrir um pacote legado e conferir visual.

**Checkpoint US4**: feature 002 completa em produção, com legados tratados.

---

## Phase 7 — Polish & Cross-Cutting

- [x] T035 [P] Confirmar que [FATURAMENTO-APP/app/templates/catalogo_epis.html](FATURAMENTO-APP/app/templates/catalogo_epis.html) segue o design system: paleta gold/gray, JetBrains Mono nos números, mesmos padrões de cards/inputs de `epis.html`. Sem CSS inline novo fora do `<style>` em `extra_css`.
- [ ] T036 [P] Smoke test de regressão (SC-6): `/billing`, `/customers`, `/reports`, `/dashboard`, `/epis` (legacy data continua carregando), `/catalogo-epis`. Console limpo. Sem 5xx no log.
- [x] T037 [P] Remover `console.log`, `print`, `breakpoint` de debug deixados durante o desenvolvimento. Buscar com `grep -rn "console.log\|^\s*print\|breakpoint" app/templates/catalogo_epis.html app/templates/epis.html app/routers/epi_catalog.py app/routers/epi_purchases.py app/services/epi_solicitation_excel.py app/services/email_sender.py`.
- [x] T038 Atualizar [CLAUDE.md](CLAUDE.md) na seção "Active Spec Feature" indicando que feature 002 está implementada (data, status, e ajustes finais).
- [x] T039 Atualizar [.env.example](FATURAMENTO-APP/.env.example) com a documentação inline dos blocos SMTP e `EPI_PURCHASE_EMAIL`.

---

## Dependencies

```text
Phase 1 (Setup: T001, T002)
    ↓
Phase 2 (Foundational: T003 [P], T004 [P], T005 [P], T006 [P], T007)
    ↓
Phase 3 (US1: T008 [P], T009 → T010 → T011 → T012 → T013 [P] → T014 → T015)
    ↓
Phase 4 (US2: T016 [P] → T017 → T018 → T019 → T020 [P] → T021 → T022 → T023 → T024 [P] → T025 → T026)
    ↓
Phase 5 (US3: T027 [P] → T028 → T029 [P] → T030 → T031)
    ↓
Phase 6 (US4: T032 [P] → T033 → T034)
    ↓
Phase 7 (Polish: T035 [P], T036 [P], T037 [P], T038, T039 [P])
```

US3 e US4 não dependem entre si — podem rodar em paralelo após US2 terminar. US4 inclusive pode adiantar T032 em paralelo com US3.

## Parallel Opportunities

- **Phase 2**: T003/T004/T005/T006 — 4 mudanças em arquivos distintos (epi_purchase.py vs config.py).
- **Phase 3 (US1)**: T008 (schemas) ∥ T011 (HTML), T013 (JS) começa depois de T011+T009. T014 (nav) é independente do JS.
- **Phase 4 (US2)**: T016 (schemas) ∥ T020 (excel module); T024 (JS) ∥ T023 (template) — arquivos diferentes.
- **Phase 5 (US3)**: T027 (email service) ∥ T029 (smtp-status endpoint).
- **Phase 6 (US4)**: T032 pode rodar em paralelo com qualquer tarefa de US3.
- **Phase 7**: T035, T036, T037, T039 todos paralelos.

## MVP Scope

**US1 + US2** entregam o valor central: catálogo de EPIs + pedido com Excel gerado automaticamente. Recomendado shipar essas duas primeiro e tratar US3 (email) e US4 (legados) em iteração seguinte. US3 só agrega se o ambiente do cliente tiver SMTP configurado (ver `.env`).

## Validação do formato

Total de tarefas: **39**. Distribuição:

- Setup: 2
- Foundational: 5
- US1 (P1): 8
- US2 (P1): 11
- US3 (P2): 5
- US4 (P3): 3
- Polish: 5

Todas seguem o formato `- [ ] T### [P?] [US#?] descrição com caminho`. Labels `[US#]` aparecem apenas em phases 3–6. Caminhos linkados onde aplicável.
