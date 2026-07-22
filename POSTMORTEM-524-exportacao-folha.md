# Post-mortem — Erro 524 na exportação da folha (faturamento)

| Campo | Valor |
|---|---|
| **Incidente** | HTTP **524** ao exportar a folha/faturamento |
| **Serviço** | `faturamento.telos-consultoria.com` (FATURAMENTO-APP) |
| **Endpoint afetado** | `POST /integrations/senior/billing/export-batch` (e demais exportações que buscam a folha na Senior) |
| **Data do diagnóstico** | 2026-07-07 |
| **Severidade** | Alta — impede a geração do faturamento pela interface web |
| **Status** | Causa **confirmada em produção**. Correção **implementada em dev**. **Pendente de deploy em produção.** |

---

## 1. Resumo (TL;DR)

A exportação da folha busca os dados na Senior (SOAP) **um centro de custo por vez, de forma sequencial, dentro de uma única requisição HTTP**. Com muitos centros de custo — ou com a Senior lenta — o processamento passa de **~100 segundos**. Como o app é publicado na internet por um **Cloudflare Tunnel**, e o Cloudflare encerra requisições de origem que passam de ~100s, o usuário recebe **HTTP 524**, mesmo o servidor continuando a processar por trás.

A correção é transformar a exportação em **job em segundo plano** (a requisição volta na hora com um `id`; o front acompanha o progresso e baixa quando fica pronto). Isso já está implementado no código, mas **ainda não foi para produção** — por isso o 524 persiste lá.

---

## 2. Impacto

- Usuários não conseguem baixar o faturamento/folha quando a exportação envolve muitos centros de custo (ex.: "Selecionar Todos").
- O erro aparece como **524 ("A timeout occurred")** no navegador.
- Efeito colateral: o servidor **continua processando** a exportação abortada (desperdício de recurso e chamadas à Senior sem entrega ao usuário).

---

## 3. Sintoma

- Ao clicar em **Exportar Faturamento** (ou Folha Senior) com vários centros de custo, a página fica carregando e termina em **524**.
- Intermitente: exportações pequenas (poucos CCs, Senior rápida) funcionam; grandes falham.

---

## 4. Arquitetura (como o tráfego chega no app)

```
Navegador
   │  https://faturamento.telos-consultoria.com
   ▼
Cloudflare (borda)                      ← timeout de origem ~100s → devolve 524
   │  Cloudflare Tunnel (cloudflared)
   ▼
Servidor interno da empresa  192.168.12.67  (VM "telos-virtual-machine")
   │  originService = http://192.168.12.67:5000
   ▼
Container Docker "app"  (uvicorn app.main:app --port 5000, 1 worker)
   │
   ▼
Postgres 16 (container)   +   Senior ERP (SOAP, externo)
```

**Ponto-chave:** o app **está no servidor da empresa** *e* **tem rota Cloudflare** ao mesmo tempo — o Cloudflare Tunnel é justamente a ferramenta que publica um servidor interno na internet sem abrir portas. As duas coisas não se excluem.

---

## 5. Causa raiz

1. A exportação (`_build_billing_export` → `fetch_payroll`) chama `_call_soap_consulta`, que para **múltiplos centros de custo itera CCU a CCU sequencialmente** (`_run_pass`, em `app/services/senior_connector.py`).
2. Cada chamada SOAP tem **timeout de 120s** (`_post_soap_with_retry`) — **maior que os ~100s do Cloudflare**. Ou seja, **um único CC lento já basta** para estourar o limite.
3. Acima de 10 CCs, há ainda **2s de espera entre chamadas** (`SENIOR_SOAP_DELAY_BETWEEN_CCUS_MS`) e um **passe de retry** ao final.
4. Tudo isso roda **dentro de uma única requisição HTTP síncrona**. O tempo total soma facilmente **> 100s**.
5. O **Cloudflare corta a origem em ~100s e devolve 524** ao navegador; o app segue processando e às vezes até conclui (200 OK no log), mas o cliente já recebeu o 524.

**Fator agravante:** a Senior está lenta. Em teste real, **um único centro de custo levou 67 segundos** para responder; a folha de 64 CCs levou vários minutos.

---

## 6. Como foi comprovado

### 6.1. Análise de código
- `app/services/senior_connector.py`: `_call_soap_consulta` → loop sequencial em `_run_pass` (uma chamada SOAP por CC); `_post_soap_with_retry` com `timeout=120`.
- `app/config.py`: `SENIOR_SOAP_DELAY_BETWEEN_CCUS_MS=2000`, `SENIOR_SOAP_DELAY_THRESHOLD_CCUS=10`, `SENIOR_SOAP_MAX_CONCURRENCY=3` (o loop **não** usa concorrência — é sequencial).
- `app/routers/integrations.py`: a exportação faz `fetch_payroll(...)` dentro do handler HTTP e só responde ao final.

### 6.2. Reprodução real (ambiente de dev conectado à Senior real)
- Exportação de **64 centros de custo** (período 06/2026): concluiu em background, mas **um CC sozinho levou 67,34s** (visível no log `[pass1] CCU 38/64 ... OK 67.34s`). No fluxo síncrono, o tempo total ultrapassa os 100s do Cloudflare → 524.

### 6.3. Confirmação em produção (acesso SSH, somente leitura)
- **Cloudflare Tunnel roteando o faturamento** — logs do `cloudflared` no servidor:
  ```
  dest=https://faturamento.telos-consultoria.com/integrations/senior/billing/export-batch
  originService=http://192.168.12.67:5000
  ip=198.41.200.23 / 198.41.200.73        (IPs de borda do Cloudflare)
  ```
- **Assinatura do 524 nos logs do túnel (06 e 07/07/2026):**
  ```
  ERR Request failed error="Incoming request ended abruptly: context canceled"
      dest=.../integrations/senior/billing/export-batch
  ```
  (a borda do Cloudflare abortando a requisição de exportação, enquanto a origem seguia processando)
- **Logs do app**: `POST /integrations/senior/billing/export-batch → 200 OK`, **sem erros** — o app conclui, mas tarde demais para o Cloudflare. Assinatura clássica de 524 (o problema é duração, não falha do app).
- **Código em produção é o antigo**: `app/services/export_jobs.py` **não existe** no container; só há os endpoints síncronos (`export-batch`, `export-femsa`, `export-skyrail`). Imagem criada em **2026-06-09**; container no ar há ~7 dias.
- **1 worker** confirmado (`uvicorn app.main:app --host 0.0.0.0 --port 5000`, sem `--workers`).

---

## 7. Correção

### 7.1. Solução adotada — exportação em segundo plano (job)
A requisição de exportação passa a **retornar imediatamente com um `job_id`**; o trabalho pesado roda em uma **thread** no servidor; o front **acompanha o progresso** e **baixa quando pronto**. Como cada requisição HTTP (iniciar / status / baixar) é curta, **nunca encosta no limite de 100s do Cloudflare**.

Fluxo:
```
POST /integrations/senior/billing/export-async   → { job_id }        (volta na hora)
GET  /integrations/senior/billing/export-status/{id} → progresso      (poll rápido, 2,5s)
GET  /integrations/senior/billing/export-download/{id} → arquivo .xlsx (quando status = done)
```

### 7.2. Componentes implementados (em dev)
- `app/services/export_jobs.py` (novo): registro de jobs **em memória**, thread-safe, com limpeza automática.
- `app/routers/integrations.py`: builder único `_build_billing_export` (modelos femsa/senior/payroll), runner `_run_billing_export_job` (thread + sessão de banco própria) e os 3 endpoints acima.
- `app/services/senior_connector.py`: `progress_cb` opcional propagado até `_run_pass` para reportar "x/y centros de custo".
- `app/templates/billing.html`: helper `_exportViaJob` (iniciar → poll → baixar, com progresso), usado pelos 3 botões (Faturamento FEMSA, Folha, Folha Senior).

### 7.3. Validação em dev (E2E)
- POST de início respondeu em **~19 ms**.
- Folha real de **64 centros de custo**: rodou inteira em background e **baixou o `.xlsx`** (`Folha_Senior_Junho.2026_64_ccus.xlsx`).
- Modelos **femsa**, **senior** e **payroll** validados (downloads íntegros).

### 7.4. Compatibilidade com produção
- Produção roda **1 worker** → o registro de jobs em memória **funciona corretamente**. (Se um dia a prod passar a rodar múltiplos workers, o registro precisará migrar para um store compartilhado, ex.: Postgres.)

---

## 8. Plano de resolução (deploy em produção)

> Ação ainda **não executada** — aguardando confirmação do método de deploy.

1. **Confirmar o método de deploy da VM.** A produção não tem o repositório git no caminho padrão (imagem `fastapisqlitejinja-3zip-2zip`; há um `telos_v1.zip` na home) — indício de deploy por **upload de zip + `docker compose build`**.
2. Levar o código atualizado (com o job assíncrono) para a VM.
3. Rebuild + restart: `docker compose up --build -d`.
4. **Checagens pós-deploy:**
   - `export_jobs.py` presente no container;
   - `GET /integrations/senior/billing/export-status/<id_qualquer>` responde (404 controlado, não 404 de rota inexistente);
   - exportação real de muitos CCs conclui e baixa **sem 524**.
5. Comunicar aos usuários que a exportação agora mostra progresso e baixa ao final (comportamento novo).

### Melhorias adicionais (defesa em profundidade, opcionais)
- Reduzir o `timeout` por chamada SOAP (hoje 120s) para algo < 100s, para falhar rápido em CC travado.
- Cachear a folha por (período, CC) — reexportações ficam quase instantâneas.
- Observação: aumentar o timeout do Cloudflare **não** é opção nos planos Free/Pro (fixo em 100s). A solução correta é o job assíncrono.

---

## 9. Ações de segurança (itens expostos durante a investigação)

> Nenhum segredo foi gravado neste documento. Recomendações:

- **Senha de SSH** foi digitada no chat durante o atendimento → **rotacionar**.
- **Token do Cloudflare Tunnel** apareceu na listagem de processos do servidor → **rotacionar** no painel Cloudflare Zero Trust.
- Foi cadastrada uma **chave SSH temporária** (`claude-vps-temp-...`) no `authorized_keys` do usuário `telos` para o diagnóstico → **remover** quando não for mais necessária.

---

## 10. Lições aprendidas / prevenção

- **Operações longas não devem rodar dentro do ciclo de request** quando há um proxy com timeout fixo na frente (Cloudflare = ~100s). Usar job assíncrono/fila.
- **Documentar a topologia** (Cloudflare Tunnel → `192.168.12.67:5000`) no `RUNBOOK.md`/`SISTEMA.md` — a rota Cloudflare não era evidente.
- **Deploy reproduzível**: migrar de "zip + build" para um fluxo versionado (git) com checagem pós-deploy da rota nova.
- **Monitorar duração** das requisições de exportação e alertar quando passar de ~60s (antecede o 524).
- Manter o app em **1 worker** enquanto o registro de jobs for em memória, ou migrar o store para banco antes de escalar workers.
