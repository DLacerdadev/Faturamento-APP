# Quickstart — Feature 003

Roteiro de validação. Pré-requisito: app subido em 8000 com o `.env` real (Senior credentials).

## Setup

1. Adicionar/conferir variáveis no `.env`:

```dotenv
SENIOR_CACHE_CCU_TTL=21600
SENIOR_CACHE_EMPLOYEES_TTL=3600
SENIOR_SOAP_MAX_CONCURRENCY=3
```

2. Reiniciar uvicorn. No log de startup deve aparecer:

```
INFO  Senior cache config: ccu_ttl=21600s employees_ttl=3600s soap_concurrency=3
```

## Cenário 1 — Cache de CCU (SC-1, SC-2, SC-3)

1. Acessar **/epis** pela primeira vez após o restart.
2. Conferir no log:
   ```
   INFO cache=miss name=ccu key=6
   INFO SOAP Senior T018CCU request: ...
   INFO SOAP Senior T018CCU retornou 760 centros de custo
   INFO cache=set name=ccu key=6 entries=760
   ```
3. Recarregar a página (Ctrl+F5).
4. Conferir no log:
   ```
   INFO cache=hit name=ccu key=6 ttl_left=21597s
   ```
   **Sem** nova chamada SOAP.

5. Abrir **/catalogo-epis** (que não chama CCUs do Senior diretamente, mas se chamasse, bateria no mesmo cache). Confirma que o cache atende qualquer ponto do sistema.

## Cenário 2 — Cache de funcionários ativos (Win 2 + Win 3)

1. Em `/epis`, selecionar o CCU `620039`.
2. Log esperado:
   ```
   INFO cache=miss name=employees key=('620039', '2026-05')
   INFO SOAP Senior request: ... codCcu=['620039']
   INFO cache=set name=employees key=('620039', '2026-05') entries=17
   ```
3. Salvar a compra **imediatamente** (1 funcionário, 1 item simples).
4. Log esperado no save:
   ```
   INFO cache=hit name=employees key=('620039', '2026-05') ttl_left=3590s
   ```
   **Sem** segunda chamada `consultaRegistros`. Total no fluxo "abrir → salvar": **1 chamada SOAP** (em vez de 2).

## Cenário 3 — Invalidação manual

1. Via curl (com token):

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/invalidate?token=$TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"scope":"ccu"}'
```

Resposta:
```json
{ "status": "ok", "scope": "ccu", "removed": { "ccu": 1, "employees": 0 } }
```

2. Recarregar /epis → deve ver `cache=miss` + nova SOAP.

## Cenário 4 — Revalidação manual (refresh)

1. Cenário: admin acabou de cadastrar um CCU novo no Senior.

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/refresh?token=$TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"scope":"ccu"}'
```

Resposta:
```json
{ "status": "ok", "scope": "ccu", "refreshed": { "ccu": { "count": 761, "sample": [...] }, "employees": null } }
```

O cache fica **já populado** com a lista nova; próximo user que abrir `/epis` cai em `cache=hit` direto.

2. Para funcionários ativos:

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/refresh?token=$TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"scope":"employees","key":"620039"}'
```

## Cenário 5 — Limite de concorrência

Script Python paralelo simulando 10 saves simultâneos:

```python
import concurrent.futures, requests, time

def call(i):
    t0 = time.time()
    r = requests.get(f"http://127.0.0.1:8000/integrations/senior/employees?codccu=62003{i % 5}&active_only=true&token=TOKEN")
    return time.time() - t0, r.status_code

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(call, range(10)))

for i, (dt, code) in enumerate(results):
    print(f"req {i}: {dt:.2f}s status={code}")
```

**Esperado**:
- 3 requests rodam em paralelo.
- 7 ficam aguardando o semáforo.
- Log mostra `wait_ms=...` para os enfileirados.
- **Zero** 503 da Senior (a F5 não vê o pico).
- Todas concluem com 200.

## Cenário 6 — Inspeção (stats)

```bash
curl "http://127.0.0.1:8000/integrations/senior/cache/stats?token=$TOKEN"
```

Mostra entradas atuais, idades e TTL restante de cada chave. Útil para depurar.

## Cenário 7 — Regressão (SC-8)

Verificar que nenhuma tela existente quebrou:
- `/billing`, `/customers`, `/reports`, `/dashboard`
- `/epis` (feature 001+002): criar compra, conferir geração de Excel (feature 002) continua funcionando
- `/catalogo-epis`: cadastrar EPI

Console do browser limpo. Sem 5xx no log.

## Cenário 8 — Frescor após TTL

Difícil de testar manualmente sem esperar 1h/6h. Alternativa: temporariamente baixar `SENIOR_CACHE_EMPLOYEES_TTL=10` no `.env`, reiniciar, e validar que após 10s a próxima leitura é `cache=miss`. Restaurar config ao final.
