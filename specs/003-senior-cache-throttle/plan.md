# Implementation Plan: Cache e Throttle das Chamadas Senior

**Feature ID**: 003-senior-cache-throttle
**Status**: Ready for `/speckit-tasks`
**Spec**: [spec.md](spec.md)
**Predecessoras**: [001-epi-purchase-flow](../001-epi-purchase-flow/plan.md), [002-epi-catalog-orders](../002-epi-catalog-orders/plan.md)

## Technical Context

- **Language/Runtime**: Python 3.11+ (dev local 3.13)
- **Backend framework**: FastAPI já em uso
- **Concorrência**: uvicorn em modo single-process com thread pool (sync handlers). Semáforo `threading.Semaphore` é adequado.
- **Cache**: stdlib `time.time()` + `dict` + `threading.Lock`. Sem `cachetools`, Redis ou outra lib.
- **External integrations**: SOAP Senior (`T018CCU`, `consultaRegistros`) — já implementadas; alvo da feature.
- **Test approach**: smoke manual via UI + endpoints admin. Validação via logs (hit/miss).
- **Performance targets**: SC-3 (CCUs em ≤ 300ms cache aquecido), SC-1 (saída de 3 → 1 SOAP por save), SC-7 (hit rate ≥ 50% após 1h)
- **Compatibility constraints**:
  - Contratos REST atuais não mudam (FR-22)
  - DEV_MODE continua operando (FR-21)
  - Features 001 e 002 não podem regredir; FR-13 (revalidação de funcionários ativos) mantida (FR-6)

Sem itens NEEDS CLARIFICATION — Q1/Q2/Q3 resolvidos na spec e retry foi removido antes do plan.

## Constitution Check

| Princípio | Conformidade |
|---|---|
| **P1 — Senior é a fonte da verdade** | ✅ Cache é apenas otimização. Dado canônico continua sendo a Senior. Endpoints admin permitem revalidar sob demanda. TTLs garantem que dados eventualmente refletem mudanças. |
| **P2 — DEV_MODE silencioso, prod barulhento** | ✅ Em DEV_MODE o cache continua operando sobre o SQLite local sem mudança de comportamento. Em prod, falhas SOAP propagam para o usuário (retry removido). |
| **P3 — Stack consistente** | ✅ Stdlib pura (`time`, `threading`, `dict`). Sem libs novas. |
| **P4 — Snapshot de dados externos** | ✅ Cache armazena snapshot em memória — coerente com o princípio (a entrada cacheada é um snapshot temporal). Não persiste entre restarts (aceitável). |
| **P5 — Migrações compatíveis** | ✅ Nenhuma mudança de banco. Sem migração. |
| **P6 — UI mantém o design system** | ✅ UI não muda (FR-22). |

Sem violações. Complexity Tracking vazio.

## Phase 0 — Research

Ver [research.md](research.md). Decisões consolidadas:

- **R1 — Estrutura do cache em memória**: classe `TimedCache` com `dict + threading.Lock + time.time()`. Lazy expiration no `get()`.
- **R2 — Acoplamento com o connector**: novo módulo `app/services/senior_cache.py` exportando 2 instâncias singleton (`ccu_cache`, `employees_cache`). Funções `fetch_cost_centers/fetch_all_cost_centers/fetch_active_employees` consultam o cache antes de chamar SOAP.
- **R3 — Semáforo global de concorrência**: módulo `app/services/senior_cache.py` também expõe um `_SOAP_SEMAPHORE = threading.BoundedSemaphore(SENIOR_SOAP_MAX_CONCURRENCY)`. `_post_soap_with_retry` adquire/libera ao redor do `requests.post`.
- **R4 — Configuração**: 3 novas vars no `.env` lidas em `app/config.py`: `SENIOR_CACHE_CCU_TTL=21600`, `SENIOR_CACHE_EMPLOYEES_TTL=3600`, `SENIOR_SOAP_MAX_CONCURRENCY=3`.
- **R5 — Endpoints admin**: em `app/routers/integrations.py`, dois novos: `POST /integrations/senior/cache/invalidate` e `POST /integrations/senior/cache/refresh`, ambos autenticados via `get_current_user`.
- **R6 — Observabilidade**: log `INFO` no formato `cache=hit key=X ttl_left=Ys` ou `cache=miss key=X` antes/depois de cada lookup. Tempo de espera no semáforo logado quando > 100ms.
- **R7 — Retry**: confirmado removido (já aplicado no código antes do plan). `_post_soap_with_retry` mantém o nome por compat, mas faz uma única tentativa.

## Phase 1 — Design Artifacts

- [data-model.md](data-model.md) — Sem mudanças de banco. Apenas estruturas em memória (caches + semáforo).
- [contracts/rest-endpoints.md](contracts/rest-endpoints.md) — Endpoints novos de administração de cache. Endpoints existentes não mudam contratos.
- [quickstart.md](quickstart.md) — Cenários para validar hits/misses, expiração, invalidação manual, refresh manual e limite de concorrência.

## Phase 2 — Implementation Approach

Ordem sugerida (detalhamento fino em `/speckit-tasks`):

### Setup
1. Adicionar variáveis novas em `.env.example` e `.env`.
2. Ler config em `app/config.py`.

### Foundational
3. Criar `app/services/senior_cache.py` com `TimedCache`, instâncias singleton e semáforo.

### US1 — Cache de CCUs
4. Refatorar `fetch_cost_centers()` e `fetch_all_cost_centers()` em `senior_connector.py` para consultar cache antes do SOAP.
5. Validar via UI: abrir `/epis` duas vezes, conferir log que mostra 1 SOAP + 1 cache hit.

### US2 — Cache de funcionários ativos (cobre Win 2 + Win 3)
6. Refatorar `fetch_active_employees(codccu)` para consultar cache curto.
7. `_revalidate_active` em `epi_purchases.py` chama `fetch_active_employees` — automaticamente já aproveita o cache, sem mudança própria.
8. Validar: criar compra; conferir log que mostra 1 SOAP no load + 1 cache hit no save (em vez de 2 SOAPs).

### US3 — Rate limit (concorrência)
9. Em `_post_soap_with_retry`, envolver o `requests.post` com `acquire()`/`release()` do semáforo global.
10. Log do tempo de espera quando > 100ms.
11. Validar: simular carga (script Python paralelo com `concurrent.futures`).

### US4 — Endpoints admin
12. Criar `POST /integrations/senior/cache/invalidate` e `POST /integrations/senior/cache/refresh` em `integrations.py`.
13. Ambos autenticados, com body `{scope, key?}`.
14. Validar: chamar endpoints, ver hit/miss seguinte mudar conforme esperado.

### Polish
15. Atualizar `RUNBOOK.md` com as novas vars do `.env` e os endpoints admin.
16. Atualizar `CLAUDE.md` indicando feature 003.
17. Smoke test de regressão (folha, faturamento, EPIs feature 002).

## Complexity Tracking

| Princípio | Deviation | Justification |
|---|---|---|
| — | — | — |
