# INTEGRATION-NOTES — spec 009 (Planilha Univer + motor XLSX)

O agente 009 respeitou as fronteiras e NÃO editou os arquivos de integração
(`app/main.py`, `app/config.py`, `app/db.py`, `requirements.txt`, `base.html`,
nem os módulos read-only). A orquestradora aplica os passos abaixo no merge.

## 1. Registrar o router no `app/main.py`

Junto aos demais `include_router` (após `conciliacao_router`):

```python
from app.routers.faturamento_grid import router as faturamento_grid_router
...
app.include_router(faturamento_grid_router)
```

Sem isso as rotas `/faturamento/planilha` e `/api/faturamento/grid/*` não sobem.
(O teste `tests/test_faturamento_grid_router.py` registra o router localmente,
de forma idempotente, só para exercitar o contrato REST — não substitui o
registro real no main.)

## 2. Link no menu (`app/templates/base.html`)

Adicionar no bloco `gestor+` da `.sidebar-nav` (ao lado de "Conciliação"):

```html
{% if user and (user.role or 'operador')|lower in ['gestor', 'admin'] %}
<a href="/faturamento/planilha" class="{% if request.url.path == '/faturamento/planilha' %}active{% endif %}">
    <span class="material-symbols-outlined">grid_on</span><span>Planilha</span>
</a>
{% endif %}
```

A página já herda `base.html` e usa `{% block extra_css %}` / `{% block extra_js %}`;
os `<script src>` externos (Univer, planilha, ai_assistant) ficam no fim do
`{% block content %}` (scripts em `<body>` são válidos).

## 3. Univer — VENDORIZADO (self-hosted, sem CDN → respeita CSP)

Bundle baixado e commitado em `app/static/vendor/univer/`:

- `univer.full.umd.js` (~20 MB) — build UMD `@univerjs/umd@0.4.2` (jsDelivr).
  Expõe os globais `window.UniverCore`, `window.UniverSheets`, `window.UniverFacade`, etc.
- `univer.css` (~400 KB) — estilos do Univer.

O template referencia esses arquivos locais (nada de CDN). Como o arquivo JS é
grande (~20 MB), considere Git LFS se o repositório for sensível a tamanho; o
conteúdo é estático e imutável (pinado em 0.4.2).

Se preferir re-baixar/atualizar a versão:

```bash
curl -sSL "https://cdn.jsdelivr.net/npm/@univerjs/umd@0.4.2/lib/univer.full.umd.js" \
  -o app/static/vendor/univer/univer.full.umd.js
curl -sSL "https://cdn.jsdelivr.net/npm/@univerjs/umd@0.4.2/lib/univer.css" \
  -o app/static/vendor/univer/univer.css
```

### Estado do bootstrap do grid

`app/static/js/faturamento_planilha.js` faz um bootstrap **best-effort** do
Univer via a Facade API (`createUniver`/`newAPI`) e converte o grid neutro em
`cellData`. Se o bundle não carregar ou o bootstrap falhar (a montagem exata do
Univer varia entre versões), o JS cai para um **fallback de tabela HTML** estável
e mantém `window.FaturamentoGrid` (getSnapshot/applyOps/highlight) 100% funcional.
Isso segue a orientação da spec: priorizar o BACKEND (serialização + apply-ops),
que é o núcleo testável.

Ação recomendada no merge: validar visualmente o bootstrap do Univer no browser
com a versão vendorizada; se necessário ajustar a chamada da Facade em
`renderUniver()` para a assinatura exata do 0.4.2. O contrato de UI e o
fallback já funcionam independentemente disso.

## 4. Dependências

Nenhuma dependência Python nova. O motor de serialização reusa `openpyxl`
(já no projeto) e importa `excel_export.py` / `model_structure.py` sem editá-los.

## 5. O que NÃO foi tocado (fronteiras)

- `ai_assistant.js` NÃO foi criado (dono: spec 010). A página só o referencia
  com `onerror` tolerante (não quebra se ainda não existir).
- `excel_export.py`, `model_structure.py`, `billing_model.py`: só leitura/import.
- `conftest.py` / `pytest.ini`: intocados. A suíte foi de 36 → 81 testes
  (45 novos), tudo verde, sem regressão.

## 6. Garantias verificadas por teste

- **preview == export** (SC-0): o preview do grid é `xlsx_to_grid(bytes)` sobre
  os MESMOS bytes que o export baixa (`_build_billing_export`). Testes:
  `test_preview_igual_export`, `test_preview_estrutura_igual_export`.
- **snapshot sem PII** (§5): `get_snapshot` só devolve estrutura + linha de
  exemplo sintética (valores None). Testes: `test_snapshot_sem_pii`,
  `test_snapshot_sem_pii_pela_rota`.
- **round-trip** (SC-1) e **todas as ops v1** (SC-2): cobertos em
  `tests/test_grid_serialization.py`.
