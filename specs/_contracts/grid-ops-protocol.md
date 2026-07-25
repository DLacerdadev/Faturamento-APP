# Contrato Compartilhado — Protocolo `grid-ops` (Univer ↔ IA)

**Status**: Draft (autoria da orquestradora — READ-ONLY para os agentes)
**Consumido por**: Spec 009 (Planilha Univer) e Spec 010 (Assistente de IA)
**Objetivo**: desacoplar a construção do grid interativo (009) da camada de IA (010) para que
possam ser desenvolvidas em **paralelo, em branches distintas, sem editar os mesmos arquivos**.

> Este documento é a "costura" entre as duas specs. Nenhum agente edita este arquivo.
> Alterações no contrato são responsabilidade exclusiva da orquestradora.

---

## 1. Papéis

- **Spec 009 (produtor do grid / consumidor de ops)**: implementa a planilha Univer e um
  endpoint que **aplica** operações `grid-ops` sobre um modelo de faturamento / estrutura C1.
- **Spec 010 (produtor de ops)**: implementa a IA que, a partir de um pedido em linguagem
  natural, **produz** uma lista de operações `grid-ops` válidas. A IA nunca escreve na planilha
  diretamente — ela devolve `ops`, que o front (009) aplica.

## 2. Ponto de montagem no DOM (contrato de UI)

A página da planilha (009) DEVE expor:

- Um elemento `<div id="ai-assistant-root"></div>` posicionado no painel lateral direito.
- A inclusão do script `/static/js/ai_assistant.js` (entregue por 010).
- Um objeto global `window.FaturamentoGrid` com a API JS abaixo. 010 chama essa API; não
  manipula o Univer diretamente.

```js
window.FaturamentoGrid = {
  getSnapshot(): GridSnapshot,          // estado atual (colunas + amostra, SEM PII — ver §5)
  applyOps(ops: GridOp[]): ApplyResult, // aplica e retorna resultado/erros
  highlight(target): void,              // destaca coluna/célula referida pela IA
}
```

010 entrega `ai_assistant.js`; 009 entrega a página, o `<div>` e `window.FaturamentoGrid`.
Nenhum dos dois edita o arquivo do outro.

## 3. Endpoints (contrato REST)

| Método | Rota | Dono | Descrição |
|--------|------|------|-----------|
| POST | `/api/faturamento/grid/apply-ops` | 009 | Recebe `{model_id, ops[]}`, valida e aplica sobre o `BillingModel`/estrutura C1. Retorna `ApplyResult`. |
| POST | `/ia/chat` | 010 | Recebe `{model_id, message, snapshot}`, chama o LLM local, retorna `{reply, ops[]}`. |

Fluxo E2E: front → `/ia/chat` (010 gera `ops`) → front chama `window.FaturamentoGrid.applyOps`
→ (se persistir) front → `/api/faturamento/grid/apply-ops` (009 grava).

## 4. Schema das operações (`GridOp`)

Toda `op` é um objeto JSON com `op` (discriminador) + campos. Conjunto **fechado** v1:

```jsonc
// adicionar coluna ao modelo
{ "op": "add_column", "header": "UNIFORMES (Valor)", "tipo": "campo|formula|constante|vazio",
  "fonte": "UNIFORMES (Valor)", "template": "=C{row}+D{row}", "valor": 0, "after": "SEGURO DE VIDA" }

// remover coluna
{ "op": "remove_column", "header": "OBS" }

// renomear / reordenar
{ "op": "rename_column", "header": "OBS", "novo_header": "OBSERVAÇÃO" }
{ "op": "move_column", "header": "OBS", "before": "Total Geral" }

// editar configuração de campo (grade Fórmulas — campos_config do BillingModel)
{ "op": "set_field_config", "campo": "HORAS EXTRAS (Valor)", "codigo": "257,259", "formula": "valor" }

// parâmetros do modelo
{ "op": "set_model_param", "param": "encargos_pct|taxa_adm_pct|imposto_pct|salario_formula", "valor": 57.91 }

// adicionar item (linha de dado / lançamento) — ver §5 sobre PII
{ "op": "add_item", "target": "catalogo|linha", "payload": { /* específico do target */ } }
```

Regras de validação (aplicadas por 009 no `apply-ops`, e espelhadas por 010 antes de devolver):
- `header` de `add_column` não pode colidir com coluna existente.
- `tipo="formula"` exige `template` com `{row}`; `tipo="constante"` exige `valor`;
  `tipo="campo"` exige `fonte` reconhecível (ou marca `warning` de fonte desconhecida).
- `set_model_param` valida faixa (percentuais 0–100; `salario_formula` passa por
  `formula_salario.py` — avaliador seguro já existente).
- Ops desconhecidas → rejeitadas com erro; nunca aplicadas parcialmente sem relatar.

## 5. Regra de PII (política da organização — NÃO-NEGOCIÁVEL)

- O `GridSnapshot` enviado ao LLM (mesmo local) contém **apenas metadados de estrutura**:
  nomes de colunas, tipos, fórmulas, parâmetros e, no máximo, **linhas de exemplo sintéticas**.
  **Nunca** CPF, nome de funcionário, matrícula real ou valores individuais reais.
- `add_item` com `target="linha"` opera sobre dados sintéticos/estrutura, não injeta PII no prompt.
- Como o LLM é self-hosted (nada sai da infra), dados reais podem transitar no backend, mas
  o **snapshot do prompt** permanece sem PII por padrão, por defesa em profundidade.

## 6. `ApplyResult`

```jsonc
{ "ok": true, "applied": 3, "warnings": ["fonte 'CAFÉ' não reconhecida — tratada como constante"],
  "errors": [], "model": { /* BillingModel.to_dict() atualizado */ } }
```

## 7. Versionamento

`grid-ops` v1 é o escopo desta rodada. Extensões (novas ops) exigem bump de versão e
atualização deste contrato pela orquestradora, com aviso a 009 e 010.
