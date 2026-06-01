# REST Endpoints — Feature 002

Contratos novos e estendidos. Schemas em Pydantic-like shorthand.

---

## CRUD do Catálogo de EPIs (router novo)

Router: `app/routers/epi_catalog.py`. Prefix: `/api/epi-catalog`.

### 1. `GET /api/epi-catalog`

Query params:
| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `q` | string | — | filtro por nome (case-insensitive, LIKE %q%) |
| `include_inactive` | bool | false | inclui EPIs desativados |
| `page` | int | 1 | |
| `per_page` | int | 50 | |

Response (200):

```json
{
  "status": "ok",
  "data": [
    {
      "id": 1,
      "nome": "Capacete classe B",
      "ativo": true,
      "sizes": [
        { "id": 10, "tamanho": "Único", "valor": 50.00 }
      ],
      "in_use_count": 3,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 7,
  "page": 1,
  "per_page": 50
}
```

`in_use_count` = número de `epi_purchase_items` com `epi_id = X` (informativo para UI mostrar antes de desativar).

### 2. `GET /api/epi-catalog/{id}`

Response (200):

```json
{ "status": "ok", "data": { /* mesma shape de um item da lista */ } }
```

404 se não existir.

### 3. `POST /api/epi-catalog`

Body:

```json
{
  "nome": "Luva de raspa",
  "sizes": [
    { "tamanho": "P", "valor": 10.00 },
    { "tamanho": "M", "valor": 12.00 },
    { "tamanho": "G", "valor": 15.00 }
  ]
}
```

Validações Pydantic:
- `nome` min_length=1, max_length=200
- `sizes` min_length=1
- Cada `tamanho` min_length=1, max_length=20
- Cada `valor > 0`

Validações de negócio:
- 409 se já existe EPI ativo com `UPPER(nome)` igual.
- 400 se `sizes` tem tamanhos duplicados.

Response (201):

```json
{ "status": "success", "data": { /* item criado completo */ } }
```

### 4. `PUT /api/epi-catalog/{id}`

Body: idêntico ao POST. Comportamento: atualiza nome e regenera `sizes` (delete all + insert all).

Mesmas validações. 404 se id não existe.

### 5. `DELETE /api/epi-catalog/{id}`

Soft-delete: marca `ativo=False`. **Não** apaga.

- Se `in_use_count > 0`: ainda funciona (soft-delete), mas a response avisa `"warning": "Este EPI tem N pedidos vinculados; ele continuará visível neles mas não aparece para novos pedidos."`
- 404 se id não existe.

Response (200):

```json
{ "status": "success", "message": "EPI desativado", "warning": null }
```

### 6. `POST /api/epi-catalog/{id}/reactivate`

Reativa um EPI desativado (`ativo=True`).

- 409 se já existe outro EPI ativo com mesmo `UPPER(nome)`.

Response (200): `{ "status": "success", "data": { /* item reativado */ } }`

---

## Compras (router existente, endpoints estendidos)

### 7. `POST /api/epi-purchases` — EXTENDIDO

Body novo (estende o da feature 001):

```json
{
  "empresa": "FEMSA",
  "mes_ano": "2026-05",
  "codccu": "620039",
  "observacao": "Compra trimestral",
  "employees": [
    { "numcad": 12345, "nome": "JOÃO DA SILVA" }
  ],
  "items": [
    {
      "epi_id": 1,
      "tamanho": "Único",
      "quantidade_por_funcionario": 1,
      "valor_unitario": 50.00
    },
    {
      "epi_id": 2,
      "tamanho": "G",
      "quantidade_por_funcionario": 2,
      "valor_unitario": 15.00
    }
  ]
}
```

Validações novas:
- Cada `item.epi_id` deve existir em `epi_catalog` e estar ativo (`ativo=True`) — exceção: edição de pacote já existente pode usar EPI desativado se já estava vinculado.
- Cada par `(epi_id, tamanho)` deve existir em `epi_catalog_sizes`.
- `valor_unitario` pode diferir de `epi_catalog_sizes.valor` (override permitido, FR-8) — no save, registra o valor do catálogo em `valor_unitario_catalogo` para futura comparação.

Comportamento (do ponto de vista da feature 002):
1. Pydantic valida shape.
2. Revalidação de funcionários ativos (FR-13, mantido da 001).
3. Para cada item, carrega `valor_unitario_catalogo` da tabela `epi_catalog_sizes`.
4. Cria pacote com `solicitante_nome = session.user.full_name or email`, `codccu`, datas.
5. Cartesiano: cada `(emp, item)` gera 1 `EpiPurchaseItem` com:
   - `descricao = EpiCatalog.nome` (snapshot)
   - `quantidade = quantidade_por_funcionario`
   - `valor_unitario = item.valor_unitario` (do payload, podendo ser override)
   - `valor_unitario_catalogo` = do catálogo no momento do save
   - `valor_total = quantidade × valor_unitario`
   - `epi_id`, `tamanho` preenchidos
   - `employee_numcad`, `employee_nome` (snapshot)
6. Calcula `quantidade_total_geral = sum(linha.quantidade)`, `valor_total_compra_geral = sum(linha.valor_total)`. Persiste no pacote.
7. **Gera Excel** chamando `generate_solicitacao_xlsx(pkg)` → salva em `app/generated_reports/solicitacao_epi_<id>_<timestamp>.xlsx`.
8. Atualiza pacote com `solicitacao_filename` e `solicitacao_generated_at`. Commit.
9. Retorna o pacote com todas as keys.

Response (200):

```json
{
  "status": "success",
  "data": {
    "id": 42,
    "empresa": "FEMSA",
    "mes_ano": "2026-05-01",
    "codccu": "620039",
    "solicitante_nome": "Daniel Lacerda",
    "is_legacy": false,
    "totais": {
      "quantidade_total_geral": 30,
      "valor_total_compra_geral": 1500.00
    },
    "agrupado_v2": {
      "funcionarios": [{ "numcad": ..., "nome": ... }, ...],
      "itens": [
        {
          "epi_id": 1,
          "epi_nome": "Capacete classe B",
          "tamanho": "Único",
          "quantidade_por_funcionario": 1,
          "valor_unitario": 50.00,
          "valor_unitario_catalogo": 50.00,
          "valor_unitario_difere_do_catalogo": false,
          "quantidade_total_item": 10,
          "valor_total_item": 500.00
        }
      ]
    },
    "solicitacao": {
      "filename": "solicitacao_epi_42_20260529-143022.xlsx",
      "generated_at": "2026-05-29T14:30:22",
      "available_for_download": true,
      "download_url": "/api/epi-purchases/42/solicitacao"
    },
    "linhas_flat": [ /* … */ ],
    "documents": []
  }
}
```

Response (409, FR-13 revalidação):

```json
{ "status": "stale", "message": "...", "inactive": [{ "numcad": ..., "nome": ..., "motivo": "..." }] }
```

Response (400, validação):

```json
{ "status": "error", "message": "EPI id=5 não está ativo no catálogo." }
```

### 8. `PUT /api/epi-purchases/{id}` — EXTENDIDO

Body: idêntico ao POST. Comportamento: deleta linhas atuais, recria cartesiano com novo input, regenera Excel (substitui arquivo anterior).

Mesmas validações e response.

### 9. `GET /api/epi-purchases` — AJUSTADO

Response (cada item da lista ganha):

```json
{
  "id": 42,
  "...": "...",
  "solicitante_nome": "Daniel Lacerda",
  "is_legacy": false,
  "totais": { "quantidade_total_geral": 30, "valor_total_compra_geral": 1500.00 },
  "solicitacao": { "filename": "...", "generated_at": "...", "available_for_download": true }
}
```

`is_legacy = true` quando o pacote tem ao menos uma linha com `epi_id IS NULL` (geralmente todas as linhas legadas). UI desabilita "Baixar solicitação" e "Enviar email" nesses casos.

### 10. `GET /api/epi-purchases/{id}/solicitacao` — NOVO

Streams o arquivo Excel gravado em `app/generated_reports/<solicitacao_filename>`.

- 200 → `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` + `Content-Disposition: attachment`.
- 404 se pacote inexistente ou se `solicitacao_filename IS NULL` (pacote legado).
- 410 (Gone) se o arquivo no disco sumiu — sugere regenerar via PUT.

### 11. `POST /api/epi-purchases/{id}/solicitacao/email` — NOVO

Body (todos opcionais):

```json
{ "to": "compras@grupoopus.com", "cc": "joao@grupoopus.com", "subject": "Solicitação de compra #42" }
```

Defaults:
- `to` = `EPI_PURCHASE_EMAIL` do `.env`
- `subject` = `Solicitação de compra de EPI #{id} — {empresa} — {mes_ano}`

Comportamento:
- 503 se SMTP não está configurado (`SMTP_HOST` vazio) — `{ "status": "error", "message": "SMTP não configurado." }`
- 400 se `to` inválido (regex básico).
- 404 se pacote inexistente.
- 404 / 410 se a solicitação não existe (pacote legado).
- 200 com `{ "status": "success", "message": "Email enviado para X destinatários." }` em caso de sucesso.

### 12. `GET /api/epi-purchases/smtp-status` — NOVO

Endpoint utilitário pro frontend saber se mostra ou esmaece o botão de email.

Response (200):

```json
{ "smtp_configured": true, "default_recipient": "compras@grupoopus.com" }
```

`default_recipient` é mascarado (ex: `c******@grupoopus.com`) se houver preocupação de privacidade — no início pode vir cru.

---

## Tela HTML (novas rotas em `main.py`)

### 13. `GET /catalogo-epis` — NOVO

Renderiza `app/templates/catalogo_epis.html`. Mesma estrutura de autenticação que `/billing` e `/epis` (valida token, redireciona pra `/login` se inválido).

### 14. `GET /epis` — EXISTENTE (sem mudança de contrato)

Já existe. Template `epis.html` recebe atualizações in-place para o novo fluxo de itens.

---

## Endpoints inalterados

- `DELETE /api/epi-purchases/{id}` — CASCADE cuida das linhas; deleta arquivo Excel da solicitação se existir.
- `POST/GET/DELETE` de documentos — sem mudança.
- Endpoints da feature 001 (CCUs, funcionários ativos) — sem mudança.
