# Tasks: Fluxo de Compra de EPIs por Funcionário

**Feature**: [spec.md](spec.md) · [plan.md](plan.md) · [contracts/rest-endpoints.md](contracts/rest-endpoints.md) · [data-model.md](data-model.md) · [quickstart.md](quickstart.md)
**Status**: Implementação concluída em disco — pendente validação E2E (T014, T017, T024, T026)

## User Stories (derivadas das Acceptance Scenarios da spec)

| ID | Prioridade | Objetivo | Mapeia para |
|---|---|---|---|
| **US1** | **P1 — MVP** | Criar uma nova compra de EPI escolhendo CCU + funcionários ativos + itens, com geração cartesiana ao salvar | AC1, AC2, AC3, AC4, AC5 |
| **US2** | **P2** | Bloquear salvamento se algum funcionário selecionado deixou de estar ativo (revalidação server-side) | FR-13 + edge case de demissão |
| **US3** | **P3** | Listar, abrir, editar e excluir compras de EPI já criadas | AC6, AC7 |

## Format

`- [ ] T### [P?] [US#?] Descrição com caminho`

---

## Phase 1 — Setup

- [x] T001 Confirmar via `python -c "import sqlite3; con=sqlite3.connect('app.db'); print([r[1] for r in con.execute('PRAGMA table_info(epi_purchase_items)')])"` que `app.db` em [FATURAMENTO-APP/app.db](FATURAMENTO-APP/app.db) ainda **não** tem as colunas `employee_numcad` e `employee_nome` (esperado: schema antigo). Se já tiver, registrar e pular T002.
- [x] T002 Aplicar migração no `app.db` rodando o SQL da seção "Setup" do [quickstart.md](quickstart.md) (ALTER TABLE em `epi_purchase_packages` e `epi_purchase_items`, mais os 2 CREATE INDEX). Validar re-rodando o PRAGMA do T001.

## Phase 2 — Foundational (bloqueia todas as User Stories)

- [x] T003 [P] Atualizar modelo em [FATURAMENTO-APP/app/models/epi_purchase.py](FATURAMENTO-APP/app/models/epi_purchase.py): adicionar `codccu = Column(String(20), nullable=True, index=True)` em `EpiPurchasePackage`; adicionar `employee_numcad = Column(Integer, nullable=True, index=True)` e `employee_nome = Column(String(200), nullable=True)` em `EpiPurchaseItem`. Manter colunas existentes inalteradas.
- [x] T004 [P] Adicionar função pura `is_employee_active(emp: dict, today: date | None = None) -> bool` em [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py), implementando a regra de R3 da [research.md](research.md): sentinel `31/12/1900`, sem `datafa`, ou `datafa > today` → ativo.
- [x] T005 Adicionar função `fetch_active_employees(codccu: str) -> List[Dict[str, Any]]` em [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py) que chama `fetch_employees_telos()`, filtra por `codccu` (string-compara), e aplica `is_employee_active` (depende de T004).
- [x] T006 Atualizar [FATURAMENTO-APP/RUNBOOK.md](FATURAMENTO-APP/RUNBOOK.md) adicionando seção "Migração 001 — EPI por funcionário" com o SQL de ALTER TABLE + verificação. Referenciar a partir da seção "Variáveis de ambiente".

---

## Phase 3 — User Story 1 (P1): Criar nova compra com produto cartesiano

**Goal**: usuário acessa `/epis`, abre form de nova compra, escolhe um CCU, marca N funcionários ativos do CCU, adiciona M itens, salva, e o sistema persiste N×M linhas com snapshot de funcionário e atributos do item replicados.

**Independent test**: executar Cenário 1 do [quickstart.md](quickstart.md) — 10 funcionários × 3 itens = 30 linhas persistidas com `valor_total_compra = R$ 800`.

- [x] T007 [US1] Estender `GET /api/integrations/senior/employees` em [FATURAMENTO-APP/app/routers/integrations.py:589-599](FATURAMENTO-APP/app/routers/integrations.py#L589-L599) adicionando query params `codccu: Optional[str] = None` e `active_only: bool = False`. Quando `codccu` informado, filtrar `r["codccu"] == codccu`. Quando `active_only=True`, aplicar `is_employee_active` (importar de `senior_connector`). Manter retro-compatibilidade: chamadas sem params retornam todos.
- [x] T008 [US1] [P] Adicionar Pydantic schemas em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): `EmployeeSelection { numcad: int, nome: str }` e `EpiPackageCreateV2 { empresa: str = "FEMSA", mes_ano: str, codccu: str, observacao: Optional[str], employees: List[EmployeeSelection], items: List[EpiItemData] }`. Validações: `employees` e `items` com `min_length=1`; cada `item.quantidade >= 1` e `item.valor_unitario > 0`.
- [x] T009 [US1] Modificar `POST /api/epi-purchases` em [FATURAMENTO-APP/app/routers/epi_purchases.py:71-96](FATURAMENTO-APP/app/routers/epi_purchases.py#L71-L96) para aceitar `EpiPackageCreateV2`. Criar `EpiPurchasePackage` com `codccu` preenchido. Expandir cartesiano: para cada par `(emp, item)`, criar 1 `EpiPurchaseItem` com `descricao`, `quantidade`, `valor_unitario`, `valor_total = quantidade * valor_unitario`, `employee_numcad = emp.numcad`, `employee_nome = emp.nome`. **Não** incluir revalidação server-side ainda (fica para US2). Validar mínimos (≥1 funcionário, ≥1 item) — retornar `400` com `{status: "error", message}` se falhar.
- [x] T010 [US1] Atualizar `package_to_dict()` em [FATURAMENTO-APP/app/routers/epi_purchases.py:41-68](FATURAMENTO-APP/app/routers/epi_purchases.py#L41-L68) para devolver as chaves novas: `codccu`, `linhas_flat` (lista atual de itens, incluindo `employee_numcad` e `employee_nome`), `agrupado` (`{funcionarios: [{numcad,nome} distintos], itens: [{descricao,quantidade,valor_unitario} distintos]}`), e `totais` (`funcionarios_distintos`, `itens_distintos`, `total_linhas`, `valor_total_compra`). Manter `items` e `total_geral` legados para retro-compat com chamadas antigas.
- [x] T011 [US1] Criar template [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html) herdando estrutura visual de [FATURAMENTO-APP/app/templates/billing.html](FATURAMENTO-APP/app/templates/billing.html): mesmo header com logo, nav, cards. Conteúdo: card "Nova Compra de EPI" com 4 seções sequenciais (CCU, Funcionários, Itens, Documentos) + card "Compras existentes" (tabela, placeholder até US3).
- [x] T012 [US1] Adicionar rota `GET /epis` em [FATURAMENTO-APP/app/main.py](FATURAMENTO-APP/app/main.py) seguindo o padrão da rota `/billing` (linhas 211-213): valida token via session_manager, redireciona pra `/login` se inválido, senão `templates.TemplateResponse("epis.html", {"request", "user", "token"})`.
- [x] T013 [US1] [P] Implementar JS inline em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): (a) `loadCostCenters()` chamando `GET /integrations/senior/cost-centers`, popula `<select>`. (b) Ao trocar CCU, `loadEmployees(codccu)` chamando `GET /api/integrations/senior/employees?codccu=XXX&active_only=true`, popula lista de checkboxes com filtro textual por nome/matrícula. (c) UI de itens com `addItem()`/`removeItem()`; `valor_total` calcula automaticamente. (d) `submitPurchase()` valida client-side (≥1 funcionário marcado, ≥1 item, cada item com `qtde>=1` e `valor>0`), POSTa `EpiPackageCreateV2`. (e) Em sucesso, limpa form e mostra toast.
- [ ] T014 [US1] Validar Cenário 1 do [quickstart.md](quickstart.md) end-to-end: subir uvicorn, logar, criar compra com 10 funcionários × 3 itens, verificar via `SELECT COUNT(*) FROM epi_purchase_items WHERE package_id = <id>` = 30 e `SUM(valor_total) = 800`. Documentar resultado no commit final desta phase.

**Checkpoint US1**: o usuário consegue criar uma compra do começo ao fim. Sistema funcional para o happy path.

---

## Phase 4 — User Story 2 (P2): Revalidação server-side de funcionários ativos

**Goal**: ao salvar (POST ou PUT), o backend re-consulta a Senior e bloqueia o save com `409 Conflict` se algum `numcad` selecionado deixou de estar ativo entre a abertura do form e o clique em "Salvar".

**Independent test**: executar Cenário 4 do [quickstart.md](quickstart.md) — UPDATE manual em `billing_employees` simula demissão; clicar em "Salvar" no form aberto retorna 409 com lista de afetados; UI exibe modal.

- [x] T015 [US2] Em [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py), antes do `db.commit()` no `POST /api/epi-purchases` (e idem no `PUT`), chamar `active = fetch_active_employees(data.codccu)` e construir `active_numcads = {e["numcad"] for e in active}`. Para cada `e in data.employees`: se `e.numcad not in active_numcads`, montar `inactive = [{numcad, nome, motivo: "Não está ativo no CCU informado"}]`. Se `inactive` não vazio, retornar `JSONResponse(status_code=409, content={status: "stale", message, inactive})`.
- [x] T016 [US2] Em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html) (no JS de `submitPurchase`), tratar resposta com `response.status === 409`: parsear `inactive`, exibir modal listando funcionários afetados com botões "Remover do save e tentar de novo" (que desmarca os `numcad`s afetados no form e re-submete) e "Cancelar" (apenas fecha o modal).
- [ ] T017 [US2] Validar Cenário 4 do [quickstart.md](quickstart.md) end-to-end. Confirmar que: (a) sem revalidação failing, save funciona normal (não regrediu US1). (b) Com UPDATE manual simulando demissão, save retorna 409 e modal aparece.

**Checkpoint US2**: feature completa do ponto de vista de correção.

---

## Phase 5 — User Story 3 (P3): Listagem, abertura, edição e exclusão

**Goal**: na tela `/epis`, ver compras anteriores numa tabela paginada, clicar para abrir uma e editar (recalcula cartesiano) ou excluir (com confirmação). Linhas legadas (`codccu IS NULL` ou `employee_numcad IS NULL`) aparecem com rótulo "(legado)".

**Independent test**: executar Cenários 5 e 6 do [quickstart.md](quickstart.md) — editar uma compra existente recalculando linhas; queries SQL de auditoria por funcionário e por CCU retornam respostas em uma única consulta.

- [x] T018 [US3] Ajustar `GET /api/epi-purchases` em [FATURAMENTO-APP/app/routers/epi_purchases.py:99-143](FATURAMENTO-APP/app/routers/epi_purchases.py#L99-L143) para incluir o bloco `totais` em cada pacote retornado (`funcionarios_distintos`, `itens_distintos`, `total_linhas`, `valor_total_compra`) calculado a partir dos `EpiPurchaseItem`s associados. Manter shape paginada atual.
- [x] T019 [US3] Ajustar `GET /api/epi-purchases/{package_id}` em [FATURAMENTO-APP/app/routers/epi_purchases.py:146-159](FATURAMENTO-APP/app/routers/epi_purchases.py#L146-L159) para chamar o `package_to_dict()` já estendido em T010 (que devolve `linhas_flat`, `agrupado`, `totais`). Garantir que pacotes legados (sem novas colunas) ainda retornem com `agrupado.funcionarios = []` em vez de erro.
- [x] T020 [US3] Atualizar `PUT /api/epi-purchases/{package_id}` em [FATURAMENTO-APP/app/routers/epi_purchases.py:162-200](FATURAMENTO-APP/app/routers/epi_purchases.py#L162-L200) para aceitar `EpiPackageCreateV2` (mesmo shape do POST). Comportamento: deletar TODAS as linhas atuais de `epi_purchase_items` do pacote, recriar cartesiano com a nova combinação. Documentos anexos NÃO são tocados. Aplicar a mesma revalidação de US2.
- [x] T021 [US3] Implementar tabela de listagem em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): JS `loadPackages()` chama `GET /api/epi-purchases?page=1&per_page=20`, renderiza tabela com colunas (Mês, CCU, Funcionários, Itens, Linhas, Valor total, Ações). Botões "Editar" e "Excluir" em cada linha. Linhas legadas com flag visual.
- [x] T022 [US3] Implementar modo de edição em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): ao clicar "Editar", chama `GET /api/epi-purchases/{id}`, popula CCU/funcionários/itens com `agrupado`. Submit usa `PUT`. Distinguir visualmente "Nova compra" de "Editando compra #N".
- [x] T023 [US3] Implementar UI de exclusão em [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html): botão "Excluir" abre confirm dialog com "Tem certeza? Esta ação remove N linhas." Em confirm, chama `DELETE /api/epi-purchases/{id}` (endpoint atual já existe, CASCADE cuida). Atualiza a tabela.
- [ ] T024 [US3] Validar Cenários 5 e 6 do [quickstart.md](quickstart.md). Confirmar via UI e via SQL que: (a) edição recalcula linhas. (b) Query "o que o colaborador X recebeu nos últimos 12 meses?" retorna resultado em 1 query. (c) Query "quantos capacetes no CCU 620039 este mês?" idem.

**Checkpoint US3**: feature 100% implementada. Pronta para uso em produção (após migração ALTER TABLE em prod).

---

## Phase 6 — Polish & Cross-Cutting

- [ ] T025 [P] Confirmar que [FATURAMENTO-APP/app/templates/epis.html](FATURAMENTO-APP/app/templates/epis.html) segue o design system: paleta de cores idêntica a `billing.html`, tipografia JetBrains Mono nos números, mesma estrutura de cards e botões. Sem CSS inline novo que não venha do mesmo `<style>` base.
- [ ] T026 [P] Smoke test de regressão (SC-6): abrir e usar normalmente `/billing`, `/customers`, `/reports`, `/dashboard`. Console do browser limpo. Nenhum 5xx no log do uvicorn. Executar Cenário 7 do [quickstart.md](quickstart.md).
- [x] T027 [P] Adicionar link "EPIs" na navegação principal do sistema (mesmo lugar onde aparecem os links para `/billing`, `/customers`). Procurar em [FATURAMENTO-APP/app/templates/](FATURAMENTO-APP/app/templates/) o `nav` compartilhado entre as telas (provavelmente em um partial ou repetido em cada template); adicionar o item nos lugares apropriados.
- [x] T028 Remover quaisquer `console.log`, `print`, `breakpoint` deixados durante o desenvolvimento. Buscar com `grep -rn "console.log\|print(\|breakpoint" app/templates/epis.html app/routers/epi_purchases.py app/services/senior_connector.py`.
- [x] T029 Atualizar [CLAUDE.md](CLAUDE.md) na seção "Active Spec Feature" indicando o status final ("implementado" + data de merge). Não criar README novo.

---

## Dependencies

```text
Phase 1 (Setup: T001 → T002)
    ↓
Phase 2 (Foundational: T003 [P], T004 [P], T005, T006)
    ↓
Phase 3 (US1: T007 → T008 [P] → T009 → T010 → T011 → T012 → T013 [P] → T014)
    ↓
Phase 4 (US2: T015 → T016 → T017)        ┐
Phase 5 (US3: T018 → T019 → T020 → ... → T024)  ┘  ← US2 e US3 podem rodar em paralelo após US1
    ↓
Phase 6 (Polish: T025 [P], T026 [P], T027 [P], T028, T029)
```

## Parallel Opportunities

- **Phase 2**: T003 (modelo) ∥ T004 (helper); T006 (RUNBOOK) pode rodar em paralelo com qualquer outro.
- **Phase 3 (US1)**: T008 (schemas Pydantic) ∥ T011 (HTML estrutural) — arquivos distintos. T013 (JS) depende de T007 e T012 estarem prontos.
- **Phases 4 e 5**: US2 e US3 são independentes entre si após US1 completa. Pode atacar uma de cada vez ou em paralelo.
- **Phase 6**: T025, T026, T027 são todos paralelos.

## MVP Scope

**US1 sozinha** já entrega valor: o usuário pode criar compras vinculadas a funcionários específicos, o que é o problema principal a resolver. US2 melhora correção (edge case), US3 entrega CRUD completo. Sugerido: shipar US1 + US3 juntos (cria + lista + edita) e US2 como hotfix subsequente se necessário.

## Validação do formato

Total de tarefas: **29**. Distribuição:

- Setup: 2
- Foundational: 4
- US1 (P1): 8
- US2 (P2): 3
- US3 (P3): 7
- Polish: 5

Todas seguem o formato `- [ ] T### [P?] [US#?] descrição com caminho`. Labels `[US#]` aparecem apenas em phases 3-5. Caminhos absolutos/relativos linkados onde aplicável.
