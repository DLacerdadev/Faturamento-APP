# Cache e Throttle das Chamadas Senior — feature 003

**Status**: Implementação em disco completa, pendente validação E2E
**Data**: 2026-05-29
**Spec**: [`specs/003-senior-cache-throttle/`](../specs/003-senior-cache-throttle/)

---

## TL;DR

O sistema fazia chamadas SOAP redundantes à Senior — a mesma lista de 760 centros de custo era re-baixada a cada page load, e a lista de funcionários ativos do mesmo CCU era buscada **duas vezes** em segundos durante o fluxo "abrir compra → salvar". Sem cache, sem controle de concorrência, com retry agressivo que amplificava picos de carga. Resultado: contribuição para erros 503 do F5 ASM da Senior em momentos de pico.

Esta entrega aplica 5 mitigações de baixo esforço:

1. **Cache em memória de CCUs** com TTL de 6h.
2. **Cache em memória de funcionários ativos** com TTL de 1h.
3. **Eliminação automática da revalidação duplicada** no save (reusa o cache de #2).
4. **Retry removido** — falha rápida em vez de loop sob estresse.
5. **Rate limit interno** — máximo de 3 chamadas SOAP simultâneas.

Resultado esperado: corte de **~60–70% das chamadas Senior** em uso normal, com a mesma garantia funcional (FR-13 de revalidação no save permanece).

---

## Por que era um problema

Inventário das chamadas SOAP do sistema (apenas 2 operações):

| Operação | Endpoint Senior | Onde era chamada |
|---|---|---|
| `T018CCU` | `rubi_Synccom_opus_nexti` | Lista de 760 CCUs — chamada em **todo page load** de `/epis` e `/catalogo-epis` |
| `consultaRegistros` | `rubi_Synccom_opus_fopag` | Folha mensal e lista de funcionários ativos — chamada **a cada interação** com CCU |

**Cenário "criar 1 compra de EPI"** disparava 3 chamadas Senior:

```
1. Page load /epis              →  T018CCU             (760 CCUs)
2. Selecionar CCU 620039        →  consultaRegistros    (funcionários ativos do CCU)
3. Clicar "Salvar"              →  consultaRegistros    (revalidação — payload idêntico ao passo 2)
                                                          ↑
                                                          duplicidade clara
```

Se um usuário fazia 5 compras numa hora: **15 chamadas SOAP**. Para `T018CCU`, todas redundantes (a lista raramente muda).

Sob picos com vários usuários simultâneos, o **F5 ASM** da Senior começava a responder HTTP 503 ("URL was rejected") como rate-limiting defensivo. O retry agressivo do sistema (4 tentativas com backoff) **amplificava** o problema em vez de aliviar.

---

## O que mudou

### Inventário de arquivos

| Arquivo | Mudança | Linhas afetadas |
|---|---|---|
| [`app/services/senior_cache.py`](../app/services/senior_cache.py) | **NOVO** — `TimedCache`, instâncias singleton, semáforo, helpers | ~120 |
| [`app/services/senior_connector.py`](../app/services/senior_connector.py) | `fetch_cost_centers`, `fetch_all_cost_centers`, `fetch_active_employees` consultam cache; `_post_soap_with_retry` envolto por semáforo; retry removido | ~50 |
| [`app/routers/integrations.py`](../app/routers/integrations.py) | +3 endpoints admin: `/cache/invalidate`, `/cache/refresh`, `/cache/stats` | ~110 |
| [`app/config.py`](../app/config.py) | +3 vars de config | ~5 |
| [`.env.example`](../.env.example) e `.env` | +bloco documentado | ~10 |
| [`RUNBOOK.md`](../RUNBOOK.md) | +seção "Cache Senior e throttle" | ~60 |

### Diagrama lógico das mudanças

```
ANTES:
┌──────────┐      ┌─────────────────┐      ┌────────┐
│ Frontend │ ───► │ senior_connector│ ───► │ Senior │
└──────────┘      │  (sem cache)    │      │  SOAP  │
                  │  retry 4x       │      └────────┘
                  └─────────────────┘

DEPOIS:
┌──────────┐      ┌──────────────────┐      ┌──────────────┐      ┌────────┐
│ Frontend │ ───► │ senior_connector │ ───► │ senior_cache │ ─┐   │        │
└──────────┘      │  (lê cache antes)│      │ TimedCache   │ │   │ Senior │
                  └──────────────────┘ ◄─── │ +semáforo(3) │ │   │  SOAP  │
                                            └──────────────┘ │   │        │
                                                             ▼   └────────┘
                                                     POST SOAP (1 tentativa)
                                                     somente se cache miss
                                                     E slot semáforo livre
```

---

## Cenário esperado: antes vs depois

### Caso 1 — Usuário cria 1 compra de EPI

**Antes**: 3 chamadas SOAP

```
T018CCU (page load)
consultaRegistros (selecionar CCU)
consultaRegistros (save — revalidação duplicada)
```

**Depois (cache aquecido)**: 1 chamada SOAP

```
cache=hit  ccu                       (page load — usa cache de 6h)
consultaRegistros (selecionar CCU)   (primeiro miss daquele CCU/mês)
cache=hit  employees                 (save — reusa cache de 1h)
```

**Depois (cache frio, primeira request do dia)**: 2 chamadas SOAP

```
cache=miss → T018CCU                 (popula cache, válido por 6h)
cache=miss → consultaRegistros       (popula cache, válido por 1h)
cache=hit  employees                 (save reusa)
```

**Corte**: 33% no caso frio, 66% no caso aquecido.

### Caso 2 — Usuário consulta CCUs em 5 telas distintas em 1 hora

**Antes**: 5 chamadas `T018CCU` (uma por page load)
**Depois**: 1 chamada `T018CCU` + 4 cache hits → **corte 80%**

### Caso 3 — Pico de 10 usuários simultâneos salvando compras

**Antes**: 10 chamadas SOAP em paralelo → F5 ASM detecta como flood → algumas voltam 503 → retry agressivo → mais 503 → cascata
**Depois**: 3 chamadas em paralelo, 7 enfileiram, todas concluem com sucesso. Latência adicional perceptível ≤ 2s no pior caso.

### Caso 4 — Senior fica temporariamente fora (503 ou timeout)

**Antes**: 4 tentativas com backoff total ~15s → cliente espera e ainda pode falhar → cliente clica de novo → outras 4 tentativas → cascata
**Depois**: 1 tentativa → falha imediata com mensagem clara (incluindo support ID do F5) → cliente decide se tenta de novo. O sistema não amplifica o pico.

---

## Detalhamento técnico

### 1. Cache em memória (`TimedCache`)

Classe Python pura, sem dependência externa:

```python
class TimedCache:
    def __init__(self, ttl_seconds: int, name: str): ...
    def get(self, key) -> Optional[Any]:
        # lazy expiration: se TTL excedido, descarta e retorna None
    def set(self, key, value) -> None: ...
    def invalidate(self, key=None) -> int: ...
    def stats(self) -> dict: ...
```

**Onde vive**: [`app/services/senior_cache.py`](../app/services/senior_cache.py)
**Thread-safety**: `threading.Lock` envolvendo `_data` (dict)
**Persistência**: nenhuma — restart limpa tudo (aceitável, dado os TTLs)

#### Instâncias singleton

| Instância | TTL default | Chave | Valor |
|---|---|---|---|
| `ccu_cache` | 6h (`SENIOR_CACHE_CCU_TTL=21600`) | `int numEmp` | Lista `[{codccu, nomccu}]` |
| `employees_cache` | 1h (`SENIOR_CACHE_EMPLOYEES_TTL=3600`) | `tuple (codccu_str, "YYYY-MM")` | Lista `[{numcad, nomfun, ...}]` |

A chave do `employees_cache` inclui o mês corrente automaticamente — se virar o mês em produção (00:00 do dia 1), o cache do mês antigo deixa de ser consultado (suas entradas envelhecem e somem na próxima leitura por TTL).

### 2. Eliminação automática da revalidação duplicada

Não foi necessário tocar em `app/routers/epi_purchases.py`. A função `_revalidate_active(payload.codccu)` já chamava `fetch_active_employees(codccu)`. Como `fetch_active_employees` agora consulta o cache antes da SOAP, a revalidação **automaticamente** reusa o resultado se a UI acabou de carregar o mesmo dado nos últimos 60min.

A garantia FR-13 da feature 001 (bloquear save com 409 se algum funcionário não está mais ativo) **permanece intacta** — a única diferença é a fonte do dado (cache vs SOAP fresca). No pior caso, a revalidação enxerga uma demissão até 1h atrasada, o que é aceitável para o caso de uso.

### 3. Semáforo de concorrência

```python
_SOAP_SEMAPHORE = threading.BoundedSemaphore(SENIOR_SOAP_MAX_CONCURRENCY)  # default 3
```

Envolve `requests.post(...)` em `_post_soap_with_retry`:

```python
_SOAP_SEMAPHORE.acquire()      # bloqueia se já há 3 em voo
try:
    resp = requests.post(...)
finally:
    _SOAP_SEMAPHORE.release()
```

Quando o `acquire()` espera mais de 100ms, loga `wait_ms=N` — permite observar gargalo se houver.

### 4. Retry removido

A função `_post_soap_with_retry` foi mantida com o mesmo nome (compat com call sites), mas agora faz **uma única tentativa**. Em qualquer falha:
- `ConnectionError`/`Timeout` → loga e propaga
- HTTP 503 (F5) → extrai support ID, loga, levanta Exception com mensagem amigável
- Outro HTTP ≠ 200 → loga e propaga

Razão: retries automáticos amplificam carga quando o problema é justamente sobrecarga. Falha rápida + usuário decide quando tentar de novo é mais saudável.

### 5. Endpoints admin

| Endpoint | Verbo | Função |
|---|---|---|
| `/integrations/senior/cache/invalidate` | POST | Limpa entradas do cache (não busca dados novos) |
| `/integrations/senior/cache/refresh` | POST | Limpa **e** busca dados frescos, populando o cache |
| `/integrations/senior/cache/stats` | GET | Snapshot dos caches + estado do semáforo |

Todos autenticados via sessão (`get_current_user`).

Exemplo — invalidar todo o cache de CCUs:

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/invalidate?token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scope":"ccu"}'
```

Exemplo — forçar revalidação de um CCU específico após admitir 5 funcionários novos:

```bash
curl -X POST "http://127.0.0.1:8000/integrations/senior/cache/refresh?token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scope":"employees","key":"620039"}'
```

Exemplo — inspecionar estado:

```bash
curl "http://127.0.0.1:8000/integrations/senior/cache/stats?token=$TOKEN"
# {"status":"ok","ccu":{"name":"ccu","ttl":21600,"entries":1,"keys":[...]},
#  "employees":{...},"soap_concurrency":{"max":3,"in_flight_estimated":0}}
```

---

## Configuração

Todas as 3 variáveis são opcionais e têm default sensato no `app/config.py`:

```dotenv
SENIOR_CACHE_CCU_TTL=21600         # 6 horas
SENIOR_CACHE_EMPLOYEES_TTL=3600    # 1 hora
SENIOR_SOAP_MAX_CONCURRENCY=3      # chamadas SOAP simultâneas
```

No log de startup do uvicorn:

```
INFO  Senior cache config: ccu_ttl=21600s employees_ttl=3600s soap_concurrency=3
```

### Quando ajustar

| Sintoma | Ajuste |
|---|---|
| Usuários reclamando que cadastro de CCU novo demora a aparecer | Reduzir `SENIOR_CACHE_CCU_TTL` ou orientar admin a chamar `/cache/refresh` |
| Demissões/admissões frequentes precisam refletir imediato no save | Reduzir `SENIOR_CACHE_EMPLOYEES_TTL` (ex: 300s) ou aceitar revalidação manual |
| Logs mostram `wait_ms` alto frequente | Aumentar `SENIOR_SOAP_MAX_CONCURRENCY` (com cuidado, pode pressionar F5) |
| F5 da Senior está rejeitando muito | Reduzir `SENIOR_SOAP_MAX_CONCURRENCY` para 2 |

---

## Como observar o impacto

Os logs do uvicorn ganharam novos eventos:

```
INFO cache=miss name=ccu key=6
INFO SOAP Senior T018CCU request: ...
INFO SOAP Senior T018CCU retornou 760 centros de custo
INFO cache=set name=ccu key=6
INFO cache=hit name=ccu key=6 ttl_left=21597.3s
INFO cache=hit name=employees key=('620039', '2026-05') ttl_left=3450.8s
INFO consultaRegistros wait_ms=120 (semáforo SOAP)
```

### Métrica simples de hit rate

```bash
# Para qualquer janela de tempo (ajuste o tail conforme volume):
grep "cache=hit\|cache=miss" uvicorn.log | tail -1000 | sort | uniq -c | sort -rn
```

Esperado em uso normal após 1h de operação: **≥ 50% de hits**.

---

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Cache pode servir dado obsoleto (CCU novo, admissão recente) | TTL conservador + endpoint `/cache/refresh` para forçar atualização |
| Cache perdido em restart do uvicorn (em deploy) | Aceitável: primeira request do dia repopula. Sem persistência por design (P5 da constitution) |
| Cache divergente entre múltiplos workers uvicorn | Hoje deployamos com 1 worker; se escalar para N, cada worker terá seu cache. Tolerável; primeira request por worker repopula |
| Semáforo bloqueando user em horário de pico | Configurável; `wait_ms` logado para identificar gargalo. Default 3 é folgado para volume Telos (~10 usuários ativos) |
| Senior fica fora e usuário vê erro imediato | Aceitável (decisão pré-plan): mensagem clara com support ID do F5; usuário tenta de novo manualmente |

---

## Fora do escopo desta feature

Itens identificados na auditoria que **não** entram nesta entrega:

- **Redis ou cache distribuído** — adicionaria dependência externa; sem necessidade no deploy atual (1 worker)
- **Job assíncrono pré-aquecendo cache no startup** — lazy é suficiente; primeira request "paga" o custo
- **Webhook/eventos da Senior** — Senior não oferece push
- **Batch de múltiplos CCUs num envelope só** — caso de uso atual da feature 002 é só 1 CCU por compra; benefício marginal
- **Métricas exportadas para Prometheus/Grafana** — apenas logs textuais nesta versão

---

## Próximos passos imediatos

1. **Validação E2E** pelo usuário (cenários 1-6 do [quickstart.md](../specs/003-senior-cache-throttle/quickstart.md)).
2. **Commit + push** das mudanças para `origin/main` no GitHub.
3. **Deploy** em produção (se aplicável) — sem migração de banco, sem nova dependência, basta restart do uvicorn.
4. **Monitorar** logs por 24-48h após deploy para confirmar hit rate ≥ 50% e ausência de degradação.

## Documentos relacionados

- Auditoria que originou esta feature: [conversa anterior + spec.md](../specs/003-senior-cache-throttle/spec.md)
- Plan completo: [`specs/003-senior-cache-throttle/plan.md`](../specs/003-senior-cache-throttle/plan.md)
- Cenários de validação detalhados: [`specs/003-senior-cache-throttle/quickstart.md`](../specs/003-senior-cache-throttle/quickstart.md)
- Contratos REST dos endpoints novos: [`specs/003-senior-cache-throttle/contracts/rest-endpoints.md`](../specs/003-senior-cache-throttle/contracts/rest-endpoints.md)
- Operação no dia-a-dia: seção "Cache Senior e throttle" do [`RUNBOOK.md`](../RUNBOOK.md)
