# Data Model — Relatório de Conciliação Contábil (004)

## Tabela nova: `codcal_classifications`

Padrão de tabela de configuração do repo (ref.: `benefit_events`). Arquivo: `app/models/codcal_classification.py`, classe `CodcalClassification`.

| Coluna | Tipo | Regras |
|--------|------|--------|
| `id` | Integer PK | autoincrement, index |
| `codcal` | Integer | `unique`, `nullable=False`, index — código do cálculo Senior (ex.: 362) |
| `descricao` | String(255) | rótulo humano editável (ex.: "Folha mensal", "13º adiantamento") — o WS não fornece nome |
| `recorte_mensal` | Boolean, `nullable=False` | `True` = entra no recorte mensal; `False` = fora do recorte |
| `origem` | String(20), default `"manual"` | `manual` (digitada do zero) \| `heuristica` (sugestão aceita pelo gestor) \| `oficial` (marcação TIPCAL da Senior, futuro — FR-4/D4). Valor recebido no upsert; `oficial` só por sincronização interna. |
| `observacao` | String(255), nullable | anotação livre (ex.: "confirmado com contabilidade em 07/2026") |
| `created_at` | DateTime | default `utcnow` |
| `updated_at` | DateTime | default `utcnow`, `onupdate=utcnow` |

**Semântica central**: codcal presente na folha da competência e **sem linha** nesta tabela = "não classificado" → conciliação fica com status `incompleta` (SC-3). Não existe estado "não classificado" persistido.

**Auditoria**: criação/edição via `audit(request, "conciliacao.classificar", entidade="codcal_classification", entidade_id=codcal, detalhe={"antes": {...}, "depois": {...}})`. A tabela não guarda histórico próprio — a trilha é o `audit_logs` (FR-3).

**Migração**: `CREATE TABLE` aditivo, idempotente no `init_db` (mesmo mecanismo das tabelas 001–003); nota no `RUNBOOK.md`. Nenhum ALTER em tabela existente (P5).

## Estrutura em memória: resultado da conciliação (não persistido — FR-10)

Produzido por `app/services/conciliacao.py::montar_conciliacao(payroll_rows, classificacoes)` (função pura), serializado como JSON no conteúdo do `ExportJob`:

```jsonc
{
  "periodo": "2026-06-01",
  "codccu": null,                      // ou "620083" quando filtrado
  "nomccu": null,                      // snapshot do nome quando filtrado (P4)
  "gerado_em": "2026-07-23T14:05:00Z",
  "gerado_por": "usuario@grupoopus.com",
  "status": "fechada",                 // "fechada" | "incompleta" | "com_residuo"
  "totais": {
    "competencia_inteira": 1234567.89,
    "recorte_mensal": 1100000.00,
    "fora_recorte": 134567.89,
    "residuo": 0.0                     // inteira − mensal − fora
  },
  "codcals": [
    {
      "codcal": 362,
      "descricao": "Folha mensal",       // null se não classificado
      "classificacao": "mensal",         // "mensal" | "fora" | "nao_classificado"
      "origem": "manual",                // null se não classificado
      "valor_total": 1100000.00,
      "eventos": [                       // drill-down agregado (FR-2) — sem funcionários
        { "codigo": "200", "descricao": "SALARIO DIA", "valor_total": 800000.00, "lancamentos": 412 },
        { "codigo": "257", "descricao": "HORAS EXTRAS", "valor_total": -1234.56, "lancamentos": 87 }
      ]
    }
  ],
  "nao_classificados": [999]             // atalho p/ destaque na tela (SC-3)
}
```

Regras de montagem (D5):

- Agrega `payroll_rows[*].eventos[*]` por (`codcal`, `codigo_evento`): soma `valor_evento` com sinal, conta lançamentos.
- `competencia_inteira` = soma de todos os codcal; `recorte_mensal`/`fora_recorte` = somas por classificação; codcal não classificado não entra em nenhuma das duas → `residuo` = valor dos não classificados (e o status vira `incompleta`).
- Nenhum campo de funcionário (matrícula/nome/valor individual) atravessa para o resultado.

## Planilha exportada (derivada do JSON retido no job — D2)

- Aba **Resumo**: período, filtro CCU (código + nome), gerado em/por, totais e status.
- Aba **Decomposição**: uma linha por codcal (codcal, descrição, classificação, origem, valor total).
- Aba **Eventos**: uma linha por codcal×evento (codcal, evento, descrição, valor total, lançamentos).

## Relacionamentos

- `codcal_classifications` não referencia outras tabelas (codcal é código externo do Senior — snapshot por natureza, P1/P4).
- `ExportJob` (memória) e `audit_logs` já existem — sem mudanças de esquema.
