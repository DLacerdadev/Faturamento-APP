# REST Endpoints — Fluxo de Compra de EPIs por Funcionário

Contratos de cada endpoint (request, response, status codes). Schemas em Pydantic-like shorthand.

---

## 1. `GET /api/integrations/senior/employees` — **EXTENDIDO**

### Query parameters (todos opcionais; default = comportamento atual)

| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `codccu` | string | — | Filtra funcionários por centro de custo |
| `active_only` | bool | `false` | Se `true`, aplica predicado `is_employee_active()` |

### Response — `200 OK`

```json
{
  "status": "ok",
  "count": 12,
  "data": [
    {
      "numcad": 12345,
      "nomfun": "JOÃO DA SILVA",
      "datadm": "2020-05-12",
      "codccu": "620039",
      "nomccu": "FILIAL SP - OPERACAO",
      "datafa": null,
      "valsal": 3500.00,
      "sitafa": 1,
      "dessit": "Trabalhando",
      "cargo": "OPERADOR"
    }
  ]
}
```

### Response — `200 OK` com `status: "error"` (não regredir o contrato atual)

Erros internos continuam retornando `{ status: "error", message: "..." }` com HTTP 200 (manter retro-compat com o resto do sistema).

### Backwards compatibility

Chamadas sem `codccu` e sem `active_only` continuam devolvendo a lista completa (igual ao hoje).

---

## 2. `POST /api/epi-purchases` — **EXTENDIDO**

### Request body

```json
{
  "empresa": "FEMSA",
  "mes_ano": "2026-05",
  "codccu": "620039",
  "observacao": "Compra trimestral capacetes",
  "employees": [
    { "numcad": 12345, "nome": "JOÃO DA SILVA" },
    { "numcad": 12346, "nome": "MARIA SANTOS" }
  ],
  "items": [
    { "descricao": "Capacete classe B", "quantidade": 1, "valor_unitario": 50.00 },
    { "descricao": "Luva de raspa par", "quantidade": 2, "valor_unitario": 15.00 }
  ]
}
```

### Comportamento

1. Valida payload (Pydantic). Erros de schema → `422`.
2. Valida obrigatórios da feature: `codccu`, `employees` (≥1), `items` (≥1), cada item com `quantidade ≥ 1` e `valor_unitario > 0`.
3. Revalida `employees` via `fetch_active_employees(codccu)`:
   - Se algum `numcad` enviado não está mais ativo → `409 Conflict` (formato abaixo).
4. Persiste:
   - 1 `EpiPurchasePackage` com `codccu` preenchido.
   - `|employees| × |items|` `EpiPurchaseItem` (cartesiano). Cada item replica `quantidade`, `valor_unitario`, `valor_total = quantidade × valor_unitario` e grava `employee_numcad` + `employee_nome` snapshot.
5. Retorna o pacote criado (formato igual ao `GET /{id}`).

### Response — `200 OK` (success)

```json
{
  "status": "success",
  "data": {
    "id": 42,
    "empresa": "FEMSA",
    "mes_ano": "2026-05-01",
    "codccu": "620039",
    "observacao": "...",
    "linhas_flat": [
      { "id": 100, "employee_numcad": 12345, "employee_nome": "JOÃO DA SILVA",
        "descricao": "Capacete classe B", "quantidade": 1, "valor_unitario": 50.00, "valor_total": 50.00 },
      { "id": 101, "employee_numcad": 12346, "employee_nome": "MARIA SANTOS",
        "descricao": "Capacete classe B", "quantidade": 1, "valor_unitario": 50.00, "valor_total": 50.00 },
      { "id": 102, "employee_numcad": 12345, "employee_nome": "JOÃO DA SILVA",
        "descricao": "Luva de raspa par", "quantidade": 2, "valor_unitario": 15.00, "valor_total": 30.00 },
      { "id": 103, "employee_numcad": 12346, "employee_nome": "MARIA SANTOS",
        "descricao": "Luva de raspa par", "quantidade": 2, "valor_unitario": 15.00, "valor_total": 30.00 }
    ],
    "agrupado": {
      "funcionarios": [
        { "numcad": 12345, "nome": "JOÃO DA SILVA" },
        { "numcad": 12346, "nome": "MARIA SANTOS" }
      ],
      "itens": [
        { "descricao": "Capacete classe B", "quantidade": 1, "valor_unitario": 50.00 },
        { "descricao": "Luva de raspa par", "quantidade": 2, "valor_unitario": 15.00 }
      ]
    },
    "totais": {
      "funcionarios_distintos": 2,
      "itens_distintos": 2,
      "total_linhas": 4,
      "valor_total_compra": 160.00
    },
    "documents": []
  }
}
```

### Response — `409 Conflict` (revalidação falhou)

```json
{
  "status": "stale",
  "message": "Alguns funcionários selecionados já não estão ativos.",
  "inactive": [
    { "numcad": 12346, "nome": "MARIA SANTOS", "motivo": "Demitida em 2026-05-20" }
  ]
}
```

### Response — `400 Bad Request`

Validação de feature (ex: `employees` vazio): `{ "status": "error", "message": "Selecione ao menos 1 funcionário e 1 item." }`.

---

## 3. `PUT /api/epi-purchases/{package_id}` — **EXTENDIDO**

Mesma shape de request que `POST`. Comportamento: deleta linhas antigas do pacote, recria cartesiano com a nova combinação. Documentos anexos preservados.

Respostas idênticas ao `POST`.

---

## 4. `GET /api/epi-purchases` — **AJUSTADO**

### Query params (preservados)

| Param | Tipo | Default |
|---|---|---|
| `page` | int | 1 |
| `per_page` | int | 20 |

### Response

Mesma shape atual, mas com `totais` agregado por pacote:

```json
{
  "status": "ok",
  "data": [
    {
      "id": 42,
      "empresa": "FEMSA",
      "mes_ano": "2026-05-01",
      "codccu": "620039",
      "observacao": "...",
      "totais": {
        "funcionarios_distintos": 2,
        "itens_distintos": 2,
        "total_linhas": 4,
        "valor_total_compra": 160.00
      },
      "documents_count": 0,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "page": 1,
  "per_page": 20,
  "total": 1
}
```

Itens legados (sem `codccu`, sem `employee_numcad`) continuam retornando — front exibe rótulo "legado" e oferece edição que força preenchimento do novo formato.

---

## 5. `GET /api/epi-purchases/{package_id}` — **AJUSTADO**

Mesma shape do response de `POST`/`PUT` (com `linhas_flat`, `agrupado`, `totais`).

---

## 6. Endpoints inalterados

- `DELETE /api/epi-purchases/{id}` — sem mudança (CASCADE cuida das linhas).
- `POST /api/epi-purchases/{id}/documents` — sem mudança.
- `GET /api/epi-purchases/{id}/documents/{doc_id}/download` — sem mudança.
- `DELETE /api/epi-purchases/{id}/documents/{doc_id}` — sem mudança.

---

## 7. Tela (HTML)

### `GET /epis` — **NOVO**

Serve `app/templates/epis.html` com:
- Listagem de compras (tabela paginada)
- Botão "Nova Compra" → abre form modal/inline
- Form: dropdown CCU → multi-select funcionários → lista de itens → upload docs → salvar

Sem contract JSON — é template HTML autenticado via sessão (igual `/billing`, `/customers`).
