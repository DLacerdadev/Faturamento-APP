# Relatório de Execução — "Mudança Forte" do Faturamento (Import/Export + IA)

**Branch base**: `feat/mudanca-forte` (criada a partir de `main` @ `b6291e5`)
**Data**: 2026-07-25
**Orquestradora**: Claude (Opus 4.8)
**Status**: ⏸️ **AGUARDANDO CONFIRMAÇÃO DO DONO PARA EXECUTAR**

Este documento mostra **o que cada agente vai fazer** antes de qualquer execução. Nada foi
implementado ainda — só foram criadas as specs e este plano.

---

## 1. Objetivo

Substituir a forma atual de import/export do faturamento por uma **planilha interativa (Univer)**
com **motor de conversão XLSX**, e adicionar uma **IA (chat)** para criar modelos, editar campos e
adicionar itens — além de resolver itens paralelos do plano (EPI automático via TOTVS e o bug
urgente da Skyrail).

Cobre os itens do projeto Faturamento Telos: **29, 30, 31, 32, 33, 34**.

## 2. Decisões travadas com o dono

1. **Univer self-hosted** — adotado; o princípio P3 da constituição ("vanilla JS, nenhum
   framework") será formalmente relaxado para o grid de planilha (exceção registrada na
   constituição no início da execução).
2. **LLM 100% self-hosted** — a IA fala com um endpoint local (compatível OpenAI, ex.: Ollama);
   nenhum dado sai da infra. Ainda assim, por defesa em profundidade e pela política da
   organização, o snapshot enviado ao modelo é **sem PII** (só estrutura).

## 3. Princípios de orquestração (regras do dono)

1. **1 agente = 1 spec.**
2. **Specs independentes** — nenhuma escreve nos mesmos arquivos de outra na mesma onda.
3. **Cada agente roda testes** provando 100% da sua spec (harness comum da spec 006).
4. **Cada spec em sua própria branch**, commitada e depois integrada em `feat/mudanca-forte`.
5. A **orquestradora** garante a conclusão, resolve os "arquivos de integração" e faz o merge/E2E.

### Arquivos de integração (só a orquestradora edita, no merge)
Para garantir independência real na onda paralela, estes arquivos **não** são editados pelos
agentes da Onda 1 — cada agente entrega as mudanças como **nota/patch** e a orquestradora aplica:
`app/main.py`, `app/config.py`, `app/db.py`, `requirements.txt`, `pyproject.toml`, `.env.example`,
`app/templates/base.html` (menu), `CLAUDE.md`, `RUNBOOK.md`, `.specify/memory/constitution.md`.

## 4. Por que ondas (e não tudo em paralelo)

Dois itens **tocam o núcleo de exportação** (`excel_export.py`, `model_structure.py`): o bug da
**Skyrail (33)** e a reescrita **Univer (29)**. Rodá-los em paralelo geraria conflito garantido.
Como a Skyrail é **URGENTE/Imediato**, ela vai **primeiro**, e a frota paralela parte da base já
corrigida. O harness de testes (006) precisa existir **antes** de qualquer agente "rodar testes".

```
Onda 0 (sequencial):   006 (harness) ─► 007 (Skyrail)  ──┐
                                                          │ merge em feat/mudanca-forte
Onda 1 (paralela):     008 (EPI/TOTVS) ║ 009 (Univer) ║ 010 (IA)  ──► merge + E2E
```

## 5. As specs / agentes

| Spec | Agente | Item(ns) | Onda | Resumo | Testes |
|------|--------|----------|------|--------|--------|
| [006 — Harness de testes](../specs/006-harness-testes/spec.md) | A | (base) | 0 | pytest + conftest + fixtures + banco isolado + smoke | smoke + fixtures |
| [007 — Fix colunas Skyrail](../specs/007-fix-colunas-skyrail/spec.md) | B | 33 | 0 | corrige colunas que somem no faturamento Skyrail | regressão Skyrail + não-regressão FEMSA/Geral |
| [008 — EPI automático TOTVS](../specs/008-epi-auto-totvs/spec.md) | C | 30,31,32 | 1 | cron diário + trava manual + voltar ao automático | sync/trava/reverter/falha-segura |
| [009 — Planilha Univer + XLSX](../specs/009-planilha-univer-xlsx/spec.md) | D | 29 | 1 | grid interativo Univer + motor de conversão XLSX | round-trip + apply-ops |
| [010 — Assistente de IA](../specs/010-assistente-ia-faturamento/spec.md) | E | 34 (+IA do 29) | 1 | chat que produz `grid-ops` via LLM local | ops válidas + sem-PII + falha tratada |

Contrato que costura 009 e 010 (nenhum agente edita):
[`specs/_contracts/grid-ops-protocol.md`](../specs/_contracts/grid-ops-protocol.md).

## 6. O que cada agente faz (detalhe)

### Agente A — 006 Harness (Onda 0)
Instala `pytest`/`httpx`; cria `tests/` + `conftest.py` (fixtures `db_session`, `client`,
`client_as(role)`); banco SQLite temporário isolado; `pytest.ini`; teste-smoke (`/health`);
helpers de folha sintética anônima; documenta o comando no `RUNBOOK.md`.
**Entrega**: comando único de teste que todas as specs seguintes usam.

### Agente B — 007 Skyrail (Onda 0)
Reproduz o bug com `docs/modelos/skyrail_abril.xlsx`; acha a causa raiz (cabeçalho não casa →
coluna vira `constante`/`vazio`, ou `fonte` ausente no DataFrame); corrige casamento de
cabeçalho/sinônimos e/ou colunas conhecidas; elimina o "sumiço silencioso" (aviso/log); teste de
regressão + não-regressão. Toca só `model_structure.py`, `excel_export.py`, `billing_models.py`,
`integrations.py` (caminho Skyrail).

### Agente C — 008 EPI/TOTVS (Onda 1)
Conector `totvs_connector.py` (WSGETDATA REST→SQL) + `price_autosync.py` + `scheduler.py`
(APScheduler, 1x/dia). A rotina puxa o **último `C7_PRECO` por produto × centro de custo** (`SC7010`,
grupo `0003`) e faz **upsert em `CCItemPrice`** — o valor de EPI é **por CCU**, então a trava manual
vive em `CCItemPrice` (`is_manual_price`/`last_auto_sync`) e **mudança num CCU não afeta outro**.
Selo "definido pela TOTVS", "voltar ao automático" por CCU e "sincronizar agora". **Notas de
integração**: startup do scheduler, deps APScheduler+httpx, env TOTVS.

> **Investigação TOTVS já feita (25/07):** conexão validada em produção; campo do valor confirmado
> (`SC7010.C7_PRECO`), grupo de EPI `0003`, envelope `{registros,qtde}`, encoding cp1252. Consulta
> oficial em `specs/008-epi-auto-totvs/contracts/totvs-wsgetdata-epi-precos.sql`.

### Agente D — 009 Univer + XLSX (Onda 1)
Nova tela `/faturamento/planilha` com grid Univer **self-hosted**; botão **"Consultar faturamento"**
que renderiza no grid o **preview fiel do `.xlsx`** a exportar; serviço `grid_serialization.py`
(XLSX ⇄ grid ⇄ `BillingModel`) reutilizando `excel_export.py`/`model_structure.py` **sem editá-los**;
endpoint `apply-ops`; expõe `window.FaturamentoGrid` + `<div id="ai-assistant-root">` do contrato.
**Notas de integração**: registrar router, link no menu.

### Agente E — 010 IA (Onda 1)
`ai_service.py` (cliente LLM local OpenAI-compatível) + `ai_tools.py` (intenção → `grid-ops`
validadas); endpoint `/ia/chat`; `ai_assistant.js` que monta em `#ai-assistant-root` e aplica ops
via `window.FaturamentoGrid`; persistência de chat; **snapshot sem PII** (garantido por teste).
Testa com LLM **mockado** (offline). **Notas de integração**: router, `LLM_BASE_URL`/`LLM_MODEL`,
deploy do modelo no RUNBOOK.

## 7. Matriz de arquivos (prova de não-sobreposição na Onda 1)

| Arquivo | 008 | 009 | 010 |
|---------|:---:|:---:|:---:|
| `app/models/product_catalog.py` | ✍️ | | |
| `app/models/epi_purchase.py` | ✍️ | | |
| `app/services/product_import.py` | ✍️ | | |
| `app/services/price_autosync.py`, `scheduler.py` | ✍️(novo) | | |
| `app/routers/product_catalog.py`, `epi_catalog.py` | ✍️ | | |
| `app/templates/catalogo_produtos.html` | ✍️ | | |
| `app/services/grid_serialization.py` | | ✍️(novo) | |
| `app/routers/faturamento_grid.py` | | ✍️(novo) | |
| `app/templates/faturamento_planilha.html` | | ✍️(novo) | |
| `app/static/vendor/univer/*`, `static/js/faturamento_planilha.js` | | ✍️(novo) | |
| `app/services/excel_export.py`, `model_structure.py` | | 👁️ leitura | |
| `app/services/ai_service.py`, `ai_tools.py` | | | ✍️(novo) |
| `app/routers/ai_chat.py` | | | ✍️(novo) |
| `app/models/ai_chat_session.py` | | | ✍️(novo) |
| `app/static/js/ai_assistant.js`, `templates/ai_chat.html` | | | ✍️(novo) |
| `tests/test_*` (arquivos distintos por spec) | ✍️ | ✍️ | ✍️ |

✍️ = escreve · 👁️ = só lê · (vazio) = não toca. **Nenhuma célula tem duas specs escrevendo no
mesmo arquivo.** Os únicos pontos comuns são os *arquivos de integração* (§3), reservados à
orquestradora.

## 8. Fluxo git por agente

1. Orquestradora cria a branch da spec a partir do ponto certo:
   - Onda 0: `spec/006-harness` e `spec/007-skyrail` a partir de `feat/mudanca-forte`.
   - Onda 1: `spec/008-epi`, `spec/009-univer`, `spec/010-ia` a partir de `feat/mudanca-forte`
     **após** o merge da Onda 0.
2. Agente implementa **na sua branch**, roda `pytest` até 100% verde, commita.
3. Orquestradora revisa, aplica as **notas de integração**, resolve `main.py`/config/etc., mergeia
   em `feat/mudanca-forte`, roda a suíte completa.
4. Ao final da Onda 1: teste **E2E** IA→applyOps→grid (costura 009+010) pela orquestradora.

## 9. Riscos e dependências externas (para sua ciência)

- **Infra do LLM local** (010): precisa de um servidor de modelo (Ollama/equivalente) na VPS de
  prod. O **desenvolvimento não bloqueia** (testes mockam o LLM), mas o **uso real** depende desse
  provisionamento. Recomendo definir modelo/host cedo.
- **Peso do Univer** (009): bundle self-hosted aumenta o tamanho do front; exige build/empacotamento.
- **Fonte TOTVS da rotina** (008): ✅ **RESOLVIDO E VALIDADO EM PRODUÇÃO** — WSGETDATA (REST→SQL);
  valor de EPI = `SC7010.C7_PRECO` por centro de custo, grupo `0003`; consulta oficial pronta e
  testada. Credenciais no `.env` local (gitignored). *Restam só confirmações de negócio com o time
  TOTVS (não bloqueiam):* outros grupos de EPI além de `0003` e desempate de múltiplos fornecedores
  no mesmo dia. ⚠️ **Recomendo rotacionar a senha** (foi compartilhada em texto puro) e criar um
  usuário Protheus dedicado com permissão mínima de leitura.
- **Migrações** (008/010): colunas e tabelas novas — `NULL`able/compatíveis (P5), documentadas no
  RUNBOOK.
- **PII + política da organização**: reforçada no contrato e na spec 010; snapshot ao LLM sem PII.

## 10. Ordem de aprovação sugerida

1. Aprovar este plano (ondas + partição).
2. Confirmar as 3 dependências externas do §9 (LLM host, fonte TOTVS, ok ao peso do Univer).
3. Autorizar execução → começo pela Onda 0 (006 → 007), depois disparo a frota da Onda 1.

> **Nada será executado até sua confirmação explícita.**
