# Tasks: Cache e Throttle das Chamadas Senior

**Feature**: [spec.md](spec.md) · [plan.md](plan.md) · [research.md](research.md) · [data-model.md](data-model.md) · [contracts/rest-endpoints.md](contracts/rest-endpoints.md) · [quickstart.md](quickstart.md)
**Status**: Implementação em disco completa — pendente validação E2E (T008, T012, T015, T020, T022, T023)

## User Stories

| ID | Prioridade | Objetivo | Mapeia para |
|---|---|---|---|
| **US1** | **P1 — MVP** | Cache em memória dos centros de custo (`T018CCU`) com TTL 6h, transparente para callers | Win 1 da auditoria, FR-1 a FR-3 |
| **US2** | **P1 — MVP** | Cache curto (TTL 1h) de funcionários ativos por CCU+mês; elimina chamada duplicada `consultaRegistros` no fluxo abrir→salvar | Win 2 + Win 3, FR-5 a FR-11 |
| **US3** | **P2** | Rate limit interno: máximo 3 chamadas SOAP simultâneas (semáforo bloqueante) | Win 5, FR-15 a FR-17 |
| **US4** | **P2** | Endpoints admin para invalidar, revalidar manualmente e inspecionar caches | FR-4, FR-11 (refresh + invalidate + stats) |

> **Win 4 (retry removido)** já foi aplicado no código antes do plan — não gera tasks.

## Format

`- [ ] T### [P?] [US#?] Descrição com caminho`

---

## Phase 1 — Setup

- [x] T001 [P] Adicionar bloco de variáveis novas em [.env.example](FATURAMENTO-APP/.env.example) com defaults documentados: `SENIOR_CACHE_CCU_TTL=21600`, `SENIOR_CACHE_EMPLOYEES_TTL=3600`, `SENIOR_SOAP_MAX_CONCURRENCY=3`. Replicar o mesmo bloco no [.env](FATURAMENTO-APP/.env) (sem valores customizados).
- [x] T002 [P] Adicionar leitura dessas 3 vars em [FATURAMENTO-APP/app/config.py](FATURAMENTO-APP/app/config.py) com `int(os.getenv(..., default))`, posicionando perto do bloco SMTP da feature 002.

## Phase 2 — Foundational (bloqueia todas as User Stories)

- [x] T003 Criar módulo [FATURAMENTO-APP/app/services/senior_cache.py](FATURAMENTO-APP/app/services/senior_cache.py) com:
  - Classe `TimedCache(ttl_seconds, name)` conforme research.md §R1 (métodos `get`, `set`, `invalidate`, `stats`; thread-safe via `threading.Lock`; lazy expiration no `get`).
  - Instâncias singleton: `ccu_cache = TimedCache(SENIOR_CACHE_CCU_TTL, "ccu")` e `employees_cache = TimedCache(SENIOR_CACHE_EMPLOYEES_TTL, "employees")`.
  - Semáforo global: `_SOAP_SEMAPHORE = threading.BoundedSemaphore(SENIOR_SOAP_MAX_CONCURRENCY)`.
  - Helper opcional `current_month_key()` retornando `date.today().strftime("%Y-%m")` para compor a chave de `employees_cache`.
  - Log `INFO` na importação: `Senior cache config: ccu_ttl=... employees_ttl=... soap_concurrency=...`.
- [x] T004 Validar que [FATURAMENTO-APP/app/services/senior_cache.py](FATURAMENTO-APP/app/services/senior_cache.py) importa sem erro: `python -c "from app.services.senior_cache import ccu_cache, employees_cache, _SOAP_SEMAPHORE; print('OK')"`.

---

## Phase 3 — User Story 1 (P1): Cache de centros de custo

**Goal**: 1ª chamada de CCUs na vida do processo busca da Senior; chamadas seguintes dentro de 6h vêm do cache. Sem SOAP redundante.

**Independent test**: Cenário 1 do [quickstart.md](quickstart.md) — abrir `/epis` 2x consecutivas; 1ª log mostra `cache=miss` + SOAP, 2ª mostra `cache=hit`.

- [x] T005 [US1] Em [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py), refatorar `fetch_cost_centers(numemp: int = 6)` para: (a) consultar `ccu_cache.get(numemp)`; (b) hit → logar `cache=hit name=ccu key=<numemp>` e retornar; (c) miss → logar `cache=miss`, chamar `_call_soap_cost_centers(numemp)`, salvar via `ccu_cache.set(numemp, lista)`, retornar.
- [x] T006 [US1] Aplicar o mesmo padrão em `fetch_all_cost_centers()` (atualmente chama `_call_soap_cost_centers(TELOS_NUMEMP)` direto). Reusar a key `TELOS_NUMEMP` no cache.
- [x] T007 [US1] Adicionar `from app.services.senior_cache import ccu_cache` no topo de [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py) (após os imports existentes, antes das funções).
- [ ] T008 [US1] Validar Cenário 1 do [quickstart.md](quickstart.md) end-to-end: abrir `/epis` duas vezes seguidas e conferir logs do uvicorn mostram 1 SOAP T018CCU + 1 `cache=hit`.

**Checkpoint US1**: 80% das chamadas T018CCU eliminadas em uso normal.

---

## Phase 4 — User Story 2 (P1): Cache de funcionários ativos + eliminação da revalidação duplicada

**Goal**: 1ª seleção de CCU na tela busca funcionários da Senior; saves seguintes ao mesmo CCU dentro de 1h reutilizam o cache (zero SOAP redundante para revalidação). FR-13 da feature 001 continua bloqueando demissões.

**Independent test**: Cenário 2 do [quickstart.md](quickstart.md) — selecionar CCU, salvar; log mostra 1 SOAP `consultaRegistros` (load) + 1 `cache=hit` (save).

- [x] T009 [US2] Em [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py), refatorar `fetch_active_employees(codccu: str)` para: (a) compor `key = (codccu_normalized, current_month_key())`; (b) consultar `employees_cache.get(key)`; (c) hit → log + return; (d) miss → chamar `fetch_payroll(periodo, codccu)`, processar (dedup + filtro `is_employee_active`, igual hoje), `employees_cache.set(key, resultado)`, return.
- [x] T010 [US2] Adicionar `from app.services.senior_cache import employees_cache, current_month_key` no topo de [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py).
- [x] T011 [US2] **Sem mudança** em `_revalidate_active` de [FATURAMENTO-APP/app/routers/epi_purchases.py](FATURAMENTO-APP/app/routers/epi_purchases.py): a função já chama `fetch_active_employees(codccu)`, então o cache cobre Win 2 automaticamente. Confirmar via leitura do código que não há outro caminho que dispara SOAP direto.
- [ ] T012 [US2] Validar Cenário 2 do [quickstart.md](quickstart.md) end-to-end: abrir `/epis`, selecionar CCU, salvar compra simples. Conferir logs do uvicorn mostram **1** chamada `consultaRegistros` no load + **`cache=hit`** no save (em vez de 2 SOAPs).

**Checkpoint US2**: corte de ~33% das chamadas `consultaRegistros` no fluxo de criação de compra. Combinado com US1, atinge meta de ~60-70% (SC-1, SC-2 da spec).

---

## Phase 5 — User Story 3 (P2): Rate limit interno (concorrência)

**Goal**: máximo 3 chamadas SOAP simultâneas; pedidos além disso enfileiram bloqueando até liberar slot.

**Independent test**: Cenário 5 do [quickstart.md](quickstart.md) — script paralelo com 10 requests; 3 rodam em paralelo, 7 enfileiram; todas concluem com 200.

- [x] T013 [US3] Em [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py), modificar `_post_soap_with_retry` para envolver o `requests.post(...)` com `acquire()`/`release()` do `_SOAP_SEMAPHORE` (try/finally). Adicionar log `INFO` com `wait_ms=N` quando o acquire esperou > 100ms.
- [x] T014 [US3] Adicionar `from app.services.senior_cache import _SOAP_SEMAPHORE` no topo de [FATURAMENTO-APP/app/services/senior_connector.py](FATURAMENTO-APP/app/services/senior_connector.py).
- [ ] T015 [US3] Validar Cenário 5 do [quickstart.md](quickstart.md) com o script Python paralelo. Conferir nos logs: nenhum erro 503; ao menos algumas requisições mostram `wait_ms>0`.

**Checkpoint US3**: SC-5 — zero falhas por overload local sob carga concorrente.

---

## Phase 6 — User Story 4 (P2): Endpoints admin (invalidate, refresh, stats)

**Goal**: admin pode limpar caches, forçar revalidação proativa e inspecionar estado atual via endpoints autenticados.

**Independent test**: Cenários 3, 4 e 6 do [quickstart.md](quickstart.md) — invalidate força miss no próximo acesso; refresh deixa cache populado direto; stats mostra entradas e idades.

- [x] T016 [US4] [P] Adicionar Pydantic schema `CacheActionInput { scope: Literal["ccu","employees","all"] = "all", key: Optional[str] = None }` em [FATURAMENTO-APP/app/routers/integrations.py](FATURAMENTO-APP/app/routers/integrations.py).
- [x] T017 [US4] Implementar `POST /senior/cache/invalidate` em [FATURAMENTO-APP/app/routers/integrations.py](FATURAMENTO-APP/app/routers/integrations.py) (prefix `/integrations` já dá a URL final): autenticação via `get_current_user`; chamar `ccu_cache.invalidate(key)` e/ou `employees_cache.invalidate(key)` conforme scope; retornar `{status, scope, removed: {ccu, employees}}`. Para `employees_cache` quando `key` é informado, montar a tupla `(codccu, current_month_key())`.
- [x] T018 [US4] Implementar `POST /senior/cache/refresh` em [FATURAMENTO-APP/app/routers/integrations.py](FATURAMENTO-APP/app/routers/integrations.py): autenticação igual; comportamento conforme [contracts/rest-endpoints.md](contracts/rest-endpoints.md) §2 — invalida + busca + popula. Retornar sumário com `count` e `sample` para cada scope tocado. 503 com mensagem clara se Senior falhar (sem retry, conforme decisão pré-plan). 400 se `scope=employees` sem `key`.
- [x] T019 [US4] [P] Implementar `GET /senior/cache/stats` em [FATURAMENTO-APP/app/routers/integrations.py](FATURAMENTO-APP/app/routers/integrations.py): autenticação igual; chamar `ccu_cache.stats()` e `employees_cache.stats()`; incluir `soap_concurrency.max` e `in_flight_estimated` (de `_SOAP_SEMAPHORE._value` se acessível). Retornar shape de [contracts/rest-endpoints.md](contracts/rest-endpoints.md) §3.
- [ ] T020 [US4] Validar Cenários 3, 4 e 6 do [quickstart.md](quickstart.md) via curl ou Invoke-RestMethod.

**Checkpoint US4**: feature completa.

---

## Phase 7 — Polish & Cross-Cutting

- [x] T021 [P] Atualizar [FATURAMENTO-APP/RUNBOOK.md](FATURAMENTO-APP/RUNBOOK.md) adicionando seção "Cache Senior (feature 003)" com: explicação dos 3 caches/semáforo, vars do `.env` e seus defaults, e exemplos curl dos endpoints admin.
- [ ] T022 [P] Smoke test de regressão (SC-8): abrir e usar normalmente `/billing`, `/customers`, `/reports`, `/dashboard`, `/epis` (criar compra completa da feature 002 + baixar Excel), `/catalogo-epis`. Nenhum 5xx no log; console do browser limpo; comportamento idêntico ao de antes.
- [ ] T023 [P] Cenário 8 do [quickstart.md](quickstart.md) (opcional, valida TTL real): temporariamente baixar `SENIOR_CACHE_EMPLOYEES_TTL=10` no `.env`, reiniciar, acessar mesmo CCU 2x com >10s de intervalo, conferir `cache=miss` na 2ª. Restaurar `3600` ao final.
- [x] T024 Atualizar [CLAUDE.md](CLAUDE.md) na seção "Active Spec Feature" indicando que feature 003 está implementada em disco (status + data de conclusão).

---

## Dependencies

```text
Phase 1 (Setup: T001 [P], T002 [P])
    ↓
Phase 2 (Foundational: T003 → T004)
    ↓
Phase 3 (US1: T005 → T006 → T007 → T008)
    ↓
Phase 4 (US2: T009 → T010 → T011 → T012)
    ↓
Phase 5 (US3: T013 → T014 → T015)         ┐
Phase 6 (US4: T016 [P] → T017 → T018 → T019 [P] → T020)  ┘  ← US3 e US4 podem rodar em paralelo após US2
    ↓
Phase 7 (Polish: T021 [P], T022 [P], T023 [P], T024)
```

## Parallel Opportunities

- **Phase 1**: T001 ∥ T002 — arquivos distintos.
- **Phase 3 (US1)**: T005, T006, T007 são edições no mesmo arquivo — sequencial.
- **Phase 6 (US4)**: T016 (schema) ∥ T019 (stats endpoint — função simples sem dependência dos outros); T017 e T018 são na mesma região do arquivo, sequencial.
- **Phases 5 e 6**: independentes entre si após US2; podem rodar em paralelo.
- **Phase 7**: T021, T022, T023 são todos paralelos.

## MVP Scope

**US1 + US2** entregam ~60-70% do benefício (corte de chamadas em uso normal). US3 (rate limit) é proteção sob pico; só fica crítico se a Telos tiver pico real de usuários simultâneos. US4 (endpoints admin) é operacional: só fica essencial se admin precisar invalidar manualmente, o que é raro com TTLs longos.

Recomendado shipar **US1 + US2 + US3** juntos (todo o backend de otimização) e US4 logo em seguida como hotfix de tooling.

## Validação do formato

Total de tarefas: **24**. Distribuição:

- Setup: 2
- Foundational: 2
- US1 (P1): 4
- US2 (P1): 4
- US3 (P2): 3
- US4 (P2): 5
- Polish: 4

Todas seguem o formato `- [ ] T### [P?] [US#?] descrição com caminho`. Labels `[US#]` aparecem só em phases 3–6.
