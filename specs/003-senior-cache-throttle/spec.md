# Feature Specification: Cache e Throttle das Chamadas Senior

**Feature ID**: 003-senior-cache-throttle
**Created**: 2026-05-29
**Status**: Ready for `/speckit-plan`
**Spec File**: spec.md
**Predecessoras**: [001-epi-purchase-flow](../001-epi-purchase-flow/spec.md), [002-epi-catalog-orders](../002-epi-catalog-orders/spec.md)

## Overview

Reduzir o volume de chamadas SOAP que o sistema dispara contra a integração Senior (`webp33.seniorcloud.com.br`), sem alterar comportamento funcional visível ao usuário.

Hoje o sistema chama Senior em duas operações apenas (`T018CCU` para centros de custo e `consultaRegistros` para folha/funcionários), mas o faz sem nenhum cache, sem controle de concorrência e com redundâncias claras: a lista de 760 CCUs é re-baixada a cada page load; ao criar uma compra de EPI, o mesmo `consultaRegistros` é disparado duas vezes em segundos (uma na UI para listar funcionários, outra no save para revalidar). Esse padrão contribuiu, em testes anteriores, para o F5 ASM da Senior responder HTTP 503 ("URL was rejected"), derrubando a integração temporariamente.

Esta feature aplica cinco mitigações de baixo esforço e alto impacto: cache em memória para CCUs e funcionários ativos, eliminação da revalidação duplicada, unificação do retry para todas as operações SOAP, e um teto de concorrência interna sobre as chamadas simultâneas. Meta: cortar entre 60% e 70% das chamadas Senior em uso normal, mantendo as mesmas garantias de correção (incluindo o bloqueio FR-13 da feature 001 quando um funcionário deixa de estar ativo entre tela e salvamento).

## User Scenarios & Testing

### Primary Flow (sem mudança visível de comportamento)

O usuário continua usando o sistema exatamente como antes:
1. Abre `/epis`, escolhe CCU, marca funcionários, adiciona itens, salva.
2. Abre `/catalogo-epis`, cadastra/edita EPIs.
3. Acessa qualquer outra tela que liste CCUs ou consulte folha.

A única diferença perceptível ao usuário deve ser:
- **Mais rápido**: telas que listam CCUs abrem em até 200ms quando o cache está aquecido (vs ~3-5s da chamada SOAP).
- **Menos erros 503**: chamadas Senior bem-sucedidas após erro transitório graças ao retry uniformizado.
- **Sob carga simultânea**, requisições enfileiram suavemente em vez de derrubarem o F5.

### Acceptance Scenarios

- **Scenario 1 — Cache de CCUs (Win 1)**: Dado que o usuário A abre `/epis` (primeira visita do processo) e o cache está frio, quando os CCUs carregam, então acontece 1 chamada SOAP `T018CCU` e a lista é cacheada. Dado que o usuário B abre `/epis` ou `/catalogo-epis` no minuto seguinte, quando os CCUs carregam, então a lista vem do cache e **nenhuma** nova chamada Senior é feita.
- **Scenario 2 — Cache expira (Win 1)**: Dado que o cache de CCUs tem 1h de TTL, quando 1h passa sem nenhum acesso, então a próxima requisição re-popula o cache via SOAP (1 chamada).
- **Scenario 3 — Invalidação manual (Win 1)**: Dado que um admin acabou de cadastrar um CCU novo no Senior, quando ele chama o endpoint admin de invalidação de cache, então a próxima requisição vai à Senior, lê o CCU novo, e cacheia.
- **Scenario 4 — Save de compra sem duplicar SOAP (Win 2 + Win 3)**: Dado que o usuário escolheu o CCU `620039` há 30 segundos e a lista de ativos foi cacheada, quando ele clica em "Salvar e Solicitar Compra", então o backend reaproveita o cache para revalidar (em vez de fazer um novo `consultaRegistros`). Total: **1** chamada Senior no fluxo todo (em vez de 2 ou 3).
- **Scenario 5 — Cache expira durante save (Win 2 + Win 3)**: Dado que o cache de funcionários ativos tem 60s de TTL, quando o usuário demora 5 minutos preenchendo a compra e clica em salvar, então o backend revalida via SOAP (a UI ficou desatualizada). FR-13 (bloqueio de funcionário não-ativo) **continua valendo**.
- **Scenario 6 — Retry uniformizado (Win 4)**: Dado que o F5 ASM responde 503 transitório à chamada de CCUs, quando o sistema tenta de novo após o backoff, então a 2ª ou 3ª tentativa passa e o usuário não vê erro. (Hoje, `T018CCU` não tem retry; usuário vê erro imediatamente.)
- **Scenario 7 — Rate limit interno (Win 5)**: Dado que 10 usuários simultâneos clicam em "Salvar" no `/epis`, quando o limite de concorrência é 5, então 5 chamadas Senior rodam em paralelo, as outras 5 enfileiram e esperam liberar slot. Todas concluem com sucesso, nenhum 503 da Senior por excesso de carga local.
- **Scenario 8 — Observabilidade**: Dado que o sistema está em produção, quando admin abre os logs do uvicorn, então cada chamada Senior aparece com tag `hit`/`miss` do cache e o tempo de espera no semáforo, permitindo medir o impacto da otimização.

### Edge Cases

- Cache em memória é perdido em cada restart do processo: aceitável; primeira request do dia repopula.
- Cache pode divergir do Senior se um CCU/funcionário muda fora do TTL: aceitável dentro da janela (1h para CCU; 60s para funcionários); admin pode invalidar manualmente.
- Cache de funcionários ativos é por (codccu + mês corrente); se o mês vira em produção (00:00 do dia 1), a chave muda automaticamente.
- Concorrência: usuário aguardando no semáforo deve perceber latência adicional, mas não erro. Limite deve ser folgado o suficiente para evitar congelamento perceptível.
- DEV_MODE: cache ainda pode operar; o "valor cacheado" é o que vem do SQLite local. Não há vazamento para Senior.
- Reuso de cache entre processos (uvicorn worker multi): cache é por processo. Em deploy com múltiplos workers, cada um terá seu próprio cache; aceitável (cada um economiza individualmente).

## Functional Requirements

### Win 1 — Cache de centros de custo (CCUs)

- **FR-1**: O sistema deve manter em memória de processo uma cópia da lista completa de CCUs retornada por `T018CCU` (operação Nexti).
- **FR-2**: Cada entrada no cache é indexada por `numEmp` (atualmente fixo em 6, mas o cache deve generalizar) e tem TTL configurável via `.env` (variável `SENIOR_CACHE_CCU_TTL`, default **21600s = 6 horas**).
- **FR-3**: Uma requisição que precisa de CCUs deve consultar primeiro o cache; em caso de hit válido, retorna sem chamada Senior. Em caso de miss ou TTL expirado, a entrada antiga é **descartada**, é feita uma nova chamada SOAP, e o cache é repopulado com o resultado fresco — comportamento "lazy expiration": expira ao ser acessada após o TTL.
- **FR-4**: O sistema deve oferecer dois endpoints administrativos autenticados:
  - `POST /integrations/senior/cache/invalidate` — limpa entradas do cache (não busca novos dados). Aceita corpo com `scope=ccu|employees|all` e opcionalmente `key` específica.
  - `POST /integrations/senior/cache/refresh` — **revalida manualmente** (descarta a entrada, busca dados frescos da Senior agora, popula o cache, retorna os novos dados). Mesmo formato de body. Útil para garantir frescor após admin saber que algo mudou no Senior, sem ter que esperar o próximo acesso de um usuário.

### Win 2 — Eliminar revalidação duplicada de funcionários ativos no save

- **FR-5**: Ao salvar (POST/PUT) `/api/epi-purchases`, o backend deve usar o mesmo cache do Win 3 para obter a lista de funcionários ativos do CCU; se o cache estiver fresco (dentro do TTL), reusa em vez de disparar nova `consultaRegistros`.
- **FR-6**: O comportamento de bloqueio FR-13 da feature 001 (HTTP 409 com lista de afetados quando algum funcionário deixou de estar ativo) **permanece inalterado**: o que muda é apenas a fonte do dado (cache vs SOAP).
- **FR-7**: Quando o cache do CCU está vazio/expirado no momento do save, o backend faz a chamada SOAP normalmente e popula o cache no caminho — eliminando a chamada duplicada na maioria dos casos sem perder segurança.

### Win 3 — Cache curto de funcionários ativos por CCU

- **FR-8**: O sistema deve manter em memória uma cópia da lista de funcionários ativos retornada por `fetch_active_employees(codccu)`, indexada por `(codccu, mês_corrente_YYYY-MM)`.
- **FR-9**: TTL configurável via `.env` (variável `SENIOR_CACHE_EMPLOYEES_TTL`, default **3600s = 1 hora**).
- **FR-10**: Requisições à mesma chave dentro do TTL retornam sem chamada Senior. Após o TTL, entrada é **descartada** na próxima leitura, nova chamada SOAP é feita, e o cache é repopulado com o resultado fresco (lazy expiration).
- **FR-11**: Invalidação manual e revalidação manual possíveis via os endpoints admin do FR-4 com `scope=employees`.

### Win 4 — Tratamento uniforme de falhas SOAP (sem retry automático)

> **Decisão atualizada:** o retry foi removido para evitar amplificar carga em momentos de instabilidade da Senior/F5 (decisão tomada antes do plan). A "uniformização" agora se refere a passar todas as operações SOAP pelo mesmo helper de erro, sem políticas diferentes entre `T018CCU` e `consultaRegistros`.

- **FR-12**: Toda chamada SOAP do sistema (atualmente `T018CCU` e `consultaRegistros`) deve passar pelo helper unificado `_post_soap_with_retry` (nome preservado por compatibilidade, mas comportamento é **uma única tentativa**).
- **FR-13**: Em falha (`ConnectionError`, `Timeout`, HTTP 503 do F5 ASM, qualquer outro HTTP ≠ 200), o sistema deve **falhar rapidamente** e propagar erro descritivo ao usuário (com support ID do F5 quando presente na resposta 503). O usuário decide se clica de novo.
- **FR-14**: Toda falha SOAP deve ser logada com nível `ERROR` incluindo a operação, URL, status code, e (quando 503) o support ID extraído do corpo HTML do F5.

### Win 5 — Rate limit interno (concorrência máxima)

- **FR-15**: O sistema deve limitar o número de chamadas SOAP simultâneas a um teto configurável via `.env` (variável `SENIOR_SOAP_MAX_CONCURRENCY`, default **3**).
- **FR-16**: Requisições que excederem o teto devem aguardar a liberação de um slot (semáforo bloqueante). Sem rejeição imediata — o usuário pode sentir leve atraso, mas a requisição completa.
- **FR-17**: O tempo de espera no semáforo deve ser logado para que o operador identifique se o limite está baixo demais.

### Observabilidade

- **FR-18**: Cada operação de cache deve logar `cache=hit|miss` no nível `INFO` ou `DEBUG`, junto com a chave e tempo gasto.
- **FR-19**: Cada chamada SOAP efetiva (após cache miss) deve continuar logando como hoje, com adição da operação SOAP (`T018CCU` ou `consultaRegistros`).
- **FR-20**: Toda configuração relevante (TTLs, concorrência) deve ser lida do `.env` na inicialização; valores efetivos exibidos no log de startup do uvicorn para auditoria.

### Compatibilidade

- **FR-21**: Em DEV_MODE (sem credenciais Senior), o cache continua operando sobre os dados locais (SQLite), sem mudança de comportamento.
- **FR-22**: Contratos de endpoints existentes (`/integrations/senior/cost-centers`, `/integrations/senior/employees`, `/api/epi-purchases` POST/PUT, etc.) **não mudam**. Apenas a fonte interna do dado pode vir do cache.

## Success Criteria

- **SC-1**: Em uso normal (cenário "criar 1 compra" descrito na auditoria), as chamadas SOAP saem de 3 para 1 — corte ≥ 66% por usuário/compra.
- **SC-2**: Em sessão de 30 minutos de um único usuário consultando 5 telas diferentes que envolvem CCUs, o número de chamadas `T018CCU` cai de 5 para 1.
- **SC-3**: Telas que listam CCUs carregam em até 300ms no caminho de cache aquecido (vs ~3-5s do SOAP frio).
- **SC-4**: Zero regressões em segurança: 100% dos casos em que um funcionário deixou de estar ativo entre a tela e o save continuam sendo bloqueados (FR-13 mantido).
- **SC-5**: Sob carga de 10 usuários simultâneos salvando compras, nenhuma requisição falha por excesso de concorrência local; latência adicional perceptível ≤ 2s.
- **SC-6**: 100% das falhas transitórias de Senior (503/timeout/reset) sobreviventes após 3 retries — ou seja, usuário só vê erro real quando a Senior está realmente fora.
- **SC-7**: Logs permitem identificar em ≤ 5 minutos de inspeção qual proporção do tráfego está sendo servida do cache (hit rate ≥ 50% em uso normal após 1h de operação).
- **SC-8**: Zero quebras nas demais telas (folha, faturamento, exames, benefícios, EPIs feature 001 e 002).

## Key Entities

- **Entrada de cache de CCU**: representa a lista completa de CCUs para um `numEmp`. Atributos: chave (`numEmp`), payload (lista de objetos {codccu, nomccu}), timestamp de população, TTL aplicado, contador de hits desde a última população (informativo).
- **Entrada de cache de Funcionários Ativos**: representa a lista de ativos para um par (codccu, mês_corrente). Atributos: chave composta, payload (lista de funcionários no formato esperado pelo front), timestamp de população, TTL, contador de hits.
- **Semáforo SOAP global**: contador atômico do número de chamadas SOAP em voo, com limite superior configurado. Sem persistência.

## Assumptions

- A1: Cache em memória de processo é suficiente para a primeira versão. Em deploys com múltiplos uvicorn workers (não é o caso atual em prod com docker-compose), cada worker terá seu cache; tolerável.
- A2: A lista de CCUs no Senior muda raramente (semanas/meses). TTL de 1h é confortável; risco de obsolescência negligenciável diante do ganho.
- A3: A lista de funcionários ativos muda dia-a-dia (admissões/demissões), mas raramente em janelas de minutos. TTL de 60s é folgado para uso interativo, suficiente para cortar a maioria das duplicações no fluxo de "abrir form → salvar".
- A4: A revalidação FR-13 continua server-side — o cache não relaxa segurança, apenas troca a fonte do dado. Se o cache estiver desatualizado, o pior caso é deixar passar uma demissão de até 60s atrás; aceitável.
- A5: O limite de concorrência (default 5) é folgado para o volume atual de usuários simultâneos da Telos (estimado ≤ 10 ativos no horário comercial). Ajustável via `.env` sem deploy de código.
- A6: A inicialização do cache é **lazy**: nada é populado no startup; a primeira request de cada chave é miss. Aceitável dado o TTL longo dos CCUs.
- A7: Sem dependência nova (Redis, MQ, cachetools, p-limit). Stdlib: `time.time()`, `dict`, `threading.Lock`/`Semaphore`.

## Out of Scope

- Cache distribuído (Redis) ou compartilhado entre múltiplos workers.
- Job assíncrono / cron pré-aquecendo o cache no startup.
- Tradução do cache para tabelas SQLite persistentes (sobrevivem a restart).
- Webhooks ou push do Senior (não disponíveis na integração atual).
- Métricas exportadas para Prometheus/Grafana — apenas logs textuais nesta versão.
- Mudanças na UI exceto, opcionalmente, um indicador discreto de "Cache hit" se for trivial.
- Alterações em outras integrações (MSSQL, REST domain) — escopo é só SOAP.

## Dependencies

- Sem novas dependências externas (stdlib).
- Continua dependendo da integração SOAP Senior estar acessível com credenciais válidas no `.env` (mesmo da feature 001).
- Helper `_post_soap_with_retry` já existe em `app/services/senior_connector.py` — vai ser estendido para cobrir `T018CCU`.

## Clarifications Resolved

- **Q1 — TTL CCUs**: **6 horas** (21600s). Mais agressivo (mais economia). Cadastros novos refletidos via revalidação manual (FR-4).
- **Q2 — TTL funcionários ativos**: **1 hora** (3600s). Aceitável dado que o save mantém revalidação contra o cache (FR-5/FR-6) e o admin pode forçar refresh (FR-4) após admissão/demissão importante.
- **Q3 — Limite de concorrência SOAP simultânea**: **3**. Mais conservador, minimiza risco de F5 503 sob pico.
- **Decisão adicional (registrada antes do plan)**: retry automático **removido** — uma única tentativa por chamada SOAP, falha rápida com support ID no log. Win 4 reescrito (FR-12 a FR-14).
