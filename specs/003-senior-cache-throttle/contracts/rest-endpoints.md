# REST Endpoints — Feature 003

Endpoints novos para administração de cache. **Endpoints existentes não mudam contratos** (FR-22) — apenas seu comportamento interno passa a usar cache quando disponível.

---

## 1. `POST /integrations/senior/cache/invalidate` — NOVO

Limpa entradas do cache sem buscar dados novos. Próximo acesso re-popula via SOAP normalmente.

### Auth
Sessão válida (`get_current_user`). Retorna 401 se token inválido.

### Body

```json
{
  "scope": "ccu" | "employees" | "all",
  "key": null
}
```

- `scope` (obrigatório): qual cache atingir.
  - `"ccu"`: cache de centros de custo (`ccu_cache`).
  - `"employees"`: cache de funcionários ativos (`employees_cache`).
  - `"all"`: ambos.
- `key` (opcional): se informado, limpa só essa chave; senão limpa todas as entradas da cache do scope.
  - Para `ccu_cache`, `key` é o `numEmp` como string ou int (ex: `"6"` ou `6`).
  - Para `employees_cache`, `key` é o `codccu` como string (ex: `"620039"`). O mês corrente é deduzido automaticamente.

### Response — 200 OK

```json
{
  "status": "ok",
  "scope": "all",
  "removed": { "ccu": 1, "employees": 3 }
}
```

`removed.X` é o número de entradas efetivamente removidas em cada cache (0 se nada havia).

### Response — 400 Bad Request
Scope inválido.

### Response — 401 Unauthorized
Token inválido/expirado.

---

## 2. `POST /integrations/senior/cache/refresh` — NOVO

Força revalidação: limpa a entrada **e** busca dados frescos da Senior agora, populando o cache. Útil quando admin sabe que algo mudou (cadastrou um CCU novo, admitiu funcionário) e quer garantir que a próxima leitura do user seja imediata e correta.

### Auth
Igual ao invalidate.

### Body
Mesmo shape do invalidate.

### Comportamento

| Scope | Comportamento |
|---|---|
| `"ccu"` | Limpa `ccu_cache` (todas as keys ou só `key`) → chama `fetch_all_cost_centers()` (ou `fetch_cost_centers(key)` se `key` informado) → popula cache → retorna sumário. |
| `"employees"` | Requer `key` (codccu). Limpa entrada → chama `fetch_active_employees(key)` → popula → retorna sumário. Sem `key` → 400 (revalidar TODOS os CCUs é caro e raramente desejado). |
| `"all"` | Atalho para `ccu` (sem key) + `employees` (NÃO atua sem key específica) — apenas refresh CCU. Mensagem do response explicita. |

### Response — 200 OK

```json
{
  "status": "ok",
  "scope": "ccu",
  "refreshed": {
    "ccu": { "count": 760, "sample": [{ "codccu": "1", "nomccu": "VIBRAC" }] },
    "employees": null
  }
}
```

### Response — 503 Service Unavailable
Falha ao chamar Senior (não retry — usuário decide se tenta de novo).

```json
{ "status": "error", "message": "Senior F5 bloqueando (HTTP 503). Tente novamente em alguns minutos. Support ID: <id>" }
```

### Response — 400 Bad Request
Scope inválido, ou `scope=employees` sem `key`.

---

## 3. `GET /integrations/senior/cache/stats` — NOVO (opcional, recomendado para depuração)

Inspeciona o estado atual dos caches sem alterar.

### Response — 200 OK

```json
{
  "status": "ok",
  "ccu": {
    "name": "ccu", "ttl": 21600, "entries": 1,
    "keys": [{ "key": "6", "age_seconds": 123.4, "ttl_left": 21476.6 }]
  },
  "employees": {
    "name": "employees", "ttl": 3600, "entries": 2,
    "keys": [
      { "key": "('620039', '2026-05')", "age_seconds": 12.3, "ttl_left": 3587.7 },
      { "key": "('620024', '2026-05')", "age_seconds": 45.0, "ttl_left": 3555.0 }
    ]
  },
  "soap_concurrency": { "max": 3, "in_flight_estimated": 0 }
}
```

`in_flight_estimated` é informativo (semáforo não expõe contagem oficial; pode ser aproximado via `_value` interno do `BoundedSemaphore` se necessário).

---

## Endpoints existentes — comportamento

### `GET /integrations/senior/cost-centers`
**Contrato**: igual. **Comportamento**: agora consulta `ccu_cache` primeiro.

### `GET /integrations/senior/cost-centers/all`
**Contrato**: igual. **Comportamento**: agora consulta `ccu_cache` primeiro.

### `GET /integrations/senior/employees?codccu=X&active_only=true`
**Contrato**: igual. **Comportamento**: quando `codccu` informado e `active_only=true`, agora consulta `employees_cache[(codccu, mês_corrente)]` primeiro.

### `POST /api/epi-purchases` e `PUT /api/epi-purchases/{id}`
**Contrato**: igual. **Comportamento**: `_revalidate_active(codccu)` agora reusa `employees_cache`. Se o cache está fresco (dentro de 1h), nenhum SOAP novo é disparado para a revalidação. FR-13 da feature 001 continua funcionando — bloqueio sobre dados do cache (que, no pior caso, são de até 1h atrás).

### Demais endpoints `/integrations/senior/billing/*`, `/integrations/senior/payroll/*`
**Sem mudança**. Não são alvo desta feature (consultas de folha são caso-a-caso, com datas específicas; cache não traz ganho proporcional ao risco de obsolescência).
