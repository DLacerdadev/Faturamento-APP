# Feature Specification: Assistente de IA do Faturamento (chat — criar modelo, editar campos, adicionar itens)

**Feature ID**: 010-assistente-ia-faturamento
**Created**: 2026-07-25
**Status**: Draft
**Spec File**: spec.md
**Itens do plano**: 34 (adicionar IA ao faturamento) + parte de IA do item 29
**Onda de execução**: 1 (frota paralela — junto com 008 e 009)
**Agente**: 1 agente dedicado
**Contrato consumido**: [`specs/_contracts/grid-ops-protocol.md`](../_contracts/grid-ops-protocol.md)

## Overview

Adicionar um **assistente de IA em chat** que ajuda a criar novos modelos de planilha, editar
campos/colunas, ajustar fórmulas/parâmetros e adicionar itens — via linguagem natural. Hoje não
há nenhuma integração de IA no projeto. Por decisão do dono, o LLM é **100% self-hosted** (nada sai
da infra) — o serviço fala com um endpoint local compatível com OpenAI (ex.: Ollama). A IA **não
escreve** na planilha diretamente: ela **produz operações `grid-ops`** (contrato compartilhado),
que a tela da planilha (spec 009) aplica via `window.FaturamentoGrid.applyOps`. Isso desacopla
totalmente esta spec da 009.

## User Scenarios & Testing

### Primary Flow

1. Na planilha, o gestor abre o painel de IA (montado em `#ai-assistant-root`).
2. Escreve, por ex., "adicione uma coluna de Uniformes depois de Seguro de Vida e configure o
   evento 1950".
3. A IA responde em texto e devolve `ops` (`add_column` + `set_field_config`); o front pré-visualiza
   e o gestor confirma a aplicação.

### Acceptance Scenarios

- **Scenario 1**: Given um pedido de nova coluna, When envio ao `/ia/chat`, Then a resposta traz
  `ops` válidas conforme o schema, e um texto explicativo.
- **Scenario 2**: Given um pedido ambíguo/impossível, When envio, Then a IA pede esclarecimento e
  NÃO inventa `ops` inválidas.
- **Scenario 3**: Given o LLM local indisponível, When envio, Then o endpoint responde erro claro
  (degradação controlada), sem travar a tela.
- **Scenario 4** (PII): Given o snapshot enviado ao LLM, Then ele contém apenas metadados de
  estrutura (colunas/tipos/fórmulas), nunca CPF/nome/valores individuais reais.

### Edge Cases

- Resposta do LLM fora do schema → validação rejeita e a IA é reprompted (ou erro amigável).
- Pedido que geraria coluna duplicada → op marcada inválida na validação (espelha regras do contrato).
- Chamada longa → usar padrão `export_jobs` (thread + poll) para não estourar timeout.

## Functional Requirements

- **FR-1**: `ai_service.py` — cliente do LLM **local** (endpoint compatível OpenAI, configurável por
  `LLM_BASE_URL`/`LLM_MODEL`), com system prompt do domínio de faturamento.
- **FR-2**: `ai_tools.py` — definição das ferramentas/execução que traduz a intenção em `grid-ops`
  válidas, com validação contra o schema do contrato **antes** de devolver.
- **FR-3**: Endpoint `POST /ia/chat` (gestor+): recebe `{model_id, message, snapshot}`, retorna
  `{reply, ops[]}`. Auditado.
- **FR-4**: `ai_assistant.js` — painel de chat que monta em `#ai-assistant-root`, chama `/ia/chat`,
  pré-visualiza `ops` e as aplica via `window.FaturamentoGrid.applyOps` (contrato).
- **FR-5**: Persistência de histórico de chat (`ai_chat_session`/`ai_chat_message`).
- **FR-6** (PII / política org): o snapshot enviado ao LLM é **sanitizado** (sem PII). Garantido por
  função dedicada + teste.
- **FR-7**: Página standalone de teste/dev (`ai_chat.html`) para exercitar a IA sem depender de 009.

## Success Criteria

- **SC-1**: Para um conjunto de pedidos-exemplo, a IA (LLM mockado no teste) produz `ops` válidas
  segundo o schema do contrato.
- **SC-2**: Snapshot enviado ao LLM comprovadamente sem PII (teste).
- **SC-3**: Falha do LLM degrada com erro claro; nenhuma exceção não-tratada vaza para a tela.

## Key Entities

- **AiChatSession / AiChatMessage** — histórico persistido.
- **GridOp / GridSnapshot** — ver contrato (produzidos aqui, aplicados por 009).

## Assumptions

- LLM local exposto por endpoint OpenAI-compatível (ex.: Ollama) — infra provisionada no deploy.
- Testes rodam **offline** com o cliente LLM **mockado** (nenhuma dependência de modelo real no CI).

## Out of Scope

- Implementar/hospedar o modelo em si (infra/deploy — dependência externa, ver relatório).
- O grid Univer e o `apply-ops` server-side (é a spec 009). Aqui só se **produz** `ops`.
- IA sobre dados reais de funcionário no prompt (proibido por política; snapshot é sem PII).

## Dependencies

- Spec 006 (harness) para testes.
- Contrato `grid-ops-protocol.md` (compartilhado com 009).
- **Infra**: servidor LLM local (Ollama/equivalente) — provisionamento é dependência externa
  (não bloqueia o desenvolvimento, pois o teste mocka o cliente).

## File Ownership / Boundaries

**Cria (exclusivo desta spec)**:
- `app/services/ai_service.py`, `app/services/ai_tools.py`
- `app/models/ai_chat_session.py`
- `app/routers/ai_chat.py`
- `app/static/js/ai_assistant.js` (monta em `#ai-assistant-root` — fornecido por 009)
- `app/templates/ai_chat.html` (página standalone de dev/test)
- `tests/test_ai_service.py`, `tests/test_ai_tools.py`

**Lê (NÃO edita)**: `app/models/billing_model.py`, contrato grid-ops.

**Notas de integração (orquestradora aplica no merge)**: registrar router em `app/main.py`;
`LLM_BASE_URL`/`LLM_MODEL` em `app/config.py` + `.env.example`; deps do cliente LLM em
`requirements.txt`; tabelas de chat em `app/db.py`; deploy do LLM no `RUNBOOK.md`.

**Não toca**: arquivos das specs 007/008/009. Em especial, **não edita**
`faturamento_planilha.html` nem `faturamento_grid.py` (donos: 009).

## Test Plan (definição de "100% concluída")

- Testes com LLM mockado: pedidos-exemplo → `ops` válidas; resposta fora do schema → rejeição;
  snapshot sanitizado (sem PII); falha do LLM → erro tratado.
- Integração E2E real (IA → applyOps → grid) é responsabilidade da **orquestradora** no merge final
  (as duas branches juntas), não desta spec isolada.
- `pytest` verde.
