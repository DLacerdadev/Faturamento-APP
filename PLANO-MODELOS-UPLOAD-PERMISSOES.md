# PLANO — Modelos de exportação por upload de Excel + Permissões (cargos) + Regras por modelo

> Documento de coordenação para execução multi-agente. Cada frente é DONA exclusiva
> dos arquivos listados — NÃO edite arquivos de outra frente. Os CONTRATOS abaixo
> são fixos: siga-os à risca para as frentes se integrarem sem conflito.

## Objetivo (pedido do usuário)

1. **Criar novos modelos de exportação de faturamento por upload de Excel.**
   Fluxo: botão "Adicionar modelo" → upload da planilha → sistema mapeia fórmulas,
   ordem dos campos e onde cada informação entra → salva o modelo → exportações
   passam a poder usar esse modelo.
2. **Cargos/permissões de usuário.** Primeiro caso: apenas **gestor ou acima** pode
   criar/editar regras de encargos sociais, taxa administrativa e imposto/alíquota.
3. **Vincular modelo de exportação + regras administrativas.** Ex.: salvar
   encargos 10% + taxa adm 5% + alíquota 3% como padrão do modelo GERAL → sempre
   que o modelo GERAL for selecionado na exportação, esses campos vêm preenchidos.

## Modelo de referência analisado (Skyrail)

Arquivo copiado em `docs/modelos/skyrail_abril.xlsx` (NÃO imprimir linhas de dados —
contêm dados pessoais; usar apenas cabeçalhos/fórmulas/estrutura).

Estrutura real (aba `FATURAMENTO`):
- Linhas 1–2: bloco de título (empresa, "RELATÓRIO DE FATURAMENTO", `=TODAY()`).
- Linhas 3–4: cabeçalho em 2 níveis com grupos mesclados:
  `COLABORADOR | ADMISSÃO | FUNÇÃO | [SALÁRIO, ENC. SOCIAIS, SUBTOTAL (1)] |`
  `BENEFÍCIOS[VT, CAFÉ, LANCHE, ALMOÇO, VT+REF SABADOS, SEG. DE VIDA, SAÚDE DO TRAB., SUBTOTAL (2)] |`
  `UNIFORMES, MATERIAIS E OUTROS[P?, NRs, UNIFORMES, EPIS, SUBTOTAL (3)] |`
  `TAXA ADM.[U4=0.07 (a TAXA mora na célula do cabeçalho!), SUBTOTAL (4)] |`
  `DESCONTOS[REFEIÇÃO, TRANSPORTE, FALTAS, ATRASOS, DSR, SUBTOTAL (5)] | TRIBUTOS | TOTAL MENSAL Unitário`
- Dados a partir da linha 5. Fórmulas POR LINHA (r = linha):
  `G=E{r}+F{r}` · `O=G{r}+H{r}+I{r}+J{r}+K{r}+L{r}+M{r}+N{r}` · `T=O{r}+P{r}+R{r}+S{r}+Q{r}`
  `U=T{r}*$U$4` · `V=T{r}+U{r}` · `AB=V{r}-W{r}-X{r}-Y{r}-Z{r}-AA{r}`
  `AC=AD{r}*16.25%` · `AD=AB{r}/0.8375`  ← gross-up de imposto (mesmo padrão FEMSA)

## CONTRATOS (fixos — todas as frentes seguem)

### C1. JSON `estrutura` (persistido em `billing_models.estrutura`)

```json
{
  "origem": "upload",
  "arquivo_nome": "skyrail_abril.xlsx",
  "aba": "FATURAMENTO",
  "header_rows": [3, 4],
  "data_row": 5,
  "titulo": "RELATÓRIO DE FATURAMENTO",
  "constantes": {"U4": 0.07},
  "colunas": [
    {"ordem": 1, "letra": "B", "titulo": "COLABORADOR", "grupo": null,
     "tipo": "campo", "campo": "Nome", "confianca": 0.95},
    {"ordem": 6, "letra": "G", "titulo": "SUBTOTAL (1)", "grupo": null,
     "tipo": "formula", "formula": "=E{r}+F{r}"},
    {"ordem": 15, "letra": "S", "titulo": "EPIS", "grupo": "UNIFORMES, MATERIAIS E OUTROS",
     "tipo": "campo", "campo": "EPIS (Valor)", "confianca": 0.9},
    {"ordem": 20, "letra": "X", "titulo": "TRANSPORTE", "grupo": "DESCONTOS",
     "tipo": "vazio"}
  ]
}
```

Regras:
- `tipo`: `"campo"` (mapeado a um nome canônico de `GERAL_COLUMNS`),
  `"formula"` (fórmula por linha; refs de linha de dados normalizadas para `{r}`;
  refs absolutas `$U$4` ficam literais), `"vazio"` (não mapeado → célula em branco).
- `campo` DEVE ser um valor EXATO de `GERAL_COLUMNS` (app/services/excel_export.py) —
  são as chaves do dict de linha que o motor de export já calcula.
- `constantes`: células fora da área de dados referenciadas por fórmulas
  (ex.: `$U$4` → gravar `{"U4": 0.07}` para o renderizador reescrever).
- `ordem` é 1-based e contígua; `letra` é a coluna original (informativo).

### C2. API do analisador (`app/services/model_analyzer.py`)

```python
def analyze_excel_model(content: bytes, filename: str = "") -> dict:
    """Retorna {"ok": bool, "estrutura": {...C1...}, "nao_mapeadas": [titulos],
    "avisos": [str], "erro": str|None}. NUNCA inclui valores de linhas de dados
    (dados pessoais) — apenas cabeçalhos, fórmulas, tipos e posições."""
```
- Heurísticas: detectar banda de cabeçalho (linhas com muitas strings), suportar
  cabeçalho de 2 linhas com grupos mesclados (compor `grupo`+`titulo`), detectar
  primeira linha de dados, extrair fórmulas normalizando a linha para `{r}`.
- Mapeamento título→campo: normalização (upper, sem acentos/pontuação) + dicionário
  de sinônimos (ex.: COLABORADOR/FUNCIONARIO/NOME→`Nome`; FUNÇÃO/CARGO→`Função`;
  ADMISSÃO→`Dt Admissão`; SALÁRIO→`Salário`; ENC. SOCIAIS/ENCARGOS→`Encargos Sociais`;
  EPIS→`EPIS (Valor)`; UNIFORMES→`UNIFORMES (Valor)`; VT/TRANSPORTE (em grupo
  BENEFÍCIOS)→`PAGTO. VALE-TRANSPORTE (Valor)`; SEG. DE VIDA→`SEGURO DE VIDA`; etc.)
  com `confianca` 0..1. Sem match → `tipo:"vazio"` e título vai em `nao_mapeadas`.

### C3. Banco (migrações idempotentes no padrão existente de `app/db.py` `_migrations`)

- `billing_models`: `estrutura` (JSON/JSONB nullable), `arquivo_origem` VARCHAR(500),
  `encargos_pct` DOUBLE PRECISION, `taxa_adm_pct` DOUBLE PRECISION,
  `imposto_pct` DOUBLE PRECISION — todos NULLable.
- `users`: `role` VARCHAR(20) NOT NULL DEFAULT 'operador'.
  Seed: usuário `ti@grupoopus.com` → role `'admin'` (idempotente, no init_db).
- Papéis e hierarquia: `operador` < `gestor` < `admin`.

### C4. Permissões (`app/services/permissions.py`)

```python
ROLE_ORDER = {"operador": 0, "gestor": 1, "admin": 2}
def get_request_user(request, db) -> User | None        # via token (auth existente)
def require_role(request, db, minimo: str) -> User      # HTTPException 401/403
```

### C5. Endpoints

- `POST /api/billing-models/analyze` — multipart `file` → resultado de C2 (NÃO salva).
- `POST /api/billing-models/from-upload` — JSON `{nome, descricao, estrutura}` →
  cria BillingModel (`is_base=False`, `ativo=True`, `colunas=[titulos na ordem]`
  para compat, `estrutura` completo, `arquivo_origem=estrutura.arquivo_nome`).
  Login obrigatório (qualquer papel).
- `PUT /api/billing-models/{id}/rules` — `{encargos_pct, taxa_adm_pct, imposto_pct}`
  (parciais permitidos; null limpa) — **require_role gestor**.
- `GET /api/billing-models` — passa a incluir `encargos_pct, taxa_adm_pct,
  imposto_pct, tem_estrutura` (bool) em cada item.
- `GET /api/me` — `{email, full_name, role}` (em `app/routers/auth.py`).
- `GET /api/users` (admin) e `PUT /api/users/{id}/role` body `{role}` (admin) —
  novo router `app/routers/users_admin.py` + página `/usuarios` (admin only).
- `PUT /api/contract-params` — passa a exigir **gestor** (o GET continua livre).

### C6. Exportação com modelo de upload (renderizador)

- Em `_build_billing_export` (app/routers/integrations.py):
  - Fallback dos percentuais: payload explícito → **pcts do modelo escolhido** →
    contrato (comportamento atual). 
  - Se o BillingModel escolhido tem `estrutura` → chamar
    `billing_to_femsa_excel(..., colunas=GERAL_COLUMNS, estrutura=estrutura, ...)`.
- Em `billing_to_femsa_excel` (app/services/excel_export.py): novo param
  `estrutura=None`. Quando presente: calcular o df normalmente com TODAS as colunas
  (GERAL_COLUMNS = superconjunto; `calcular_faturamento` roda igual) e renderizar
  com um novo helper `_write_custom_layout(writer, df, estrutura, periodo)`:
  - Linha de título (estrutura.titulo) + banda de cabeçalho de 2 linhas com merges
    de grupo (recriar `grupo` mesclado sobre as colunas do grupo).
  - `constantes` gravadas nas células indicadas (ex.: U4=0.07 → sobrescrever com a
    taxa_adm efetiva/100 quando o campo correspondente for a taxa adm; caso não dê
    para inferir, gravar o valor original).
  - Por linha de dados: `campo` → `df[campo]`; `formula` → substituir `{r}` pelo nº
    da linha Excel; `vazio` → em branco.
  - Larguras/formatos básicos; sem regressão nenhuma quando `estrutura is None`.
- Nome do arquivo: `Faturamento_{NOME_MODELO_SLUG}_{Mes}.{Ano}_TELOS_{ccu}.xlsx`
  quando o modelo tem estrutura; senão mantém o padrão atual.

### C7. Frontend

- `modelos_faturamento.html`: botão **"Adicionar modelo (upload Excel)"** → input
  file → POST analyze → modal/painel de revisão mostrando: colunas na ordem, o que
  foi mapeado (campo + confiança), fórmulas detectadas e não-mapeadas → campo nome/
  descrição → POST from-upload → recarrega lista. Modelos com `tem_estrutura`
  ganham badge "Upload".
- `billing.html` (tela Exportar Faturamento):
  - `loadExportModels()` guarda a lista completa; ao trocar `#export-modelo`,
    preencher `param-encargos/param-taxa-adm/param-imposto` com os pcts do modelo
    (quando não-nulos; senão manter contrato/valores atuais).
  - **Salvar padrão** → `PUT /api/billing-models/{id}/rules` do modelo selecionado
    (não mais contract-params; manter contract-params como fallback se modelo não
    encontrado). Botão desabilitado (com tooltip) para papel < gestor via `/api/me`.
- `usuarios.html` (nova, admin): tabela de usuários com dropdown de papel
  (operador/gestor/admin) e salvar. Herda base.html. Link no nav do base.html
  (visível sempre; a página valida admin server-side).

### C8. Regras gerais

- PII: NUNCA imprimir/copiar linhas de dados de planilhas (org policy). Estrutura
  (cabeçalhos/fórmulas) é ok.
- NÃO chamar a Senior em testes: usar `FORCE_DEV_MODE=1` em processos de teste.
- Migrações idempotentes (padrão `_migrations` + `create_all` já existente).
- Tema: qualquer página nova herda `base.html` (design institucional dark).
- Compat: modelos SEM estrutura continuam funcionando exatamente como hoje
  (colunas-driven). FEMSA sem regressão.

## FRENTES (donos exclusivos de arquivos)

### F1 — Analisador de modelos Excel
**Arquivos:** `app/services/model_analyzer.py` (novo), `docs/ANALISE-MODELO-SKYRAIL.md` (novo).
Implementar C2 completo + testar contra `docs/modelos/skyrail_abril.xlsx` (via
script inline python -c, sem framework). O doc de análise descreve o padrão de
análise (como o sistema mapeia qualquer novo modelo) + o resultado do Skyrail
(29 colunas, 8 fórmulas, constante U4). Sem PII no doc.

### F2 — Backend: banco, permissões, endpoints
**Arquivos:** `app/models/billing_model.py`, `app/models/user.py`, `app/db.py`,
`app/routers/billing_models.py`, `app/routers/auth.py` (/api/me),
`app/routers/users_admin.py` (novo), `app/routers/contract_params.py`,
`app/services/permissions.py` (novo), `app/main.py` (include router + rota /usuarios).
Implementar C3, C4, C5. O endpoint analyze importa `analyze_excel_model` de F1
(assinatura fixa em C2 — não precisa esperar F1).

### F3 — Frontend
**Arquivos:** `app/templates/modelos_faturamento.html`, `app/templates/billing.html`,
`app/templates/usuarios.html` (novo), `app/templates/base.html` (só o nav).
Implementar C7 contra os endpoints de C5.

### F4 — Renderizador de exportação
**Arquivos:** `app/services/excel_export.py`, `app/routers/integrations.py`.
Implementar C6. Cuidado: esses arquivos têm lógica crítica em produção — mudanças
aditivas, zero regressão com `estrutura is None` (rodar um export femsa/geral de
sanidade via função direta em DEV_MODE).

### F5 — Verificação E2E (roda DEPOIS de F1–F4)
Sem dono de arquivo (pode corrigir pequenos bugs de integração em qualquer um).
Roteiro (tudo com `FORCE_DEV_MODE=1`, processo isolado, sem servidor, sem Senior):
1. `analyze_excel_model(docs/modelos/skyrail_abril.xlsx)` → ok, ~29 colunas,
   8 fórmulas normalizadas com `{r}`, constante U4 capturada.
2. Criar modelo "SKYRAIL TERCEIRIZADOS" via from-upload (TestClient) → aparece no
   GET /api/billing-models com `tem_estrutura=true`.
3. `PUT .../rules` como admin (ti@grupoopus.com) → 200; como usuário operador
   (criar um usuário de teste) → 403.
4. `_build_billing_export(db, "skyrail terceirizados", "2026-07-01", ["10101"])` →
   xlsx abre; ordem/títulos das colunas = estrutura; células de fórmula contêm
   fórmulas Excel; campos mapeados preenchidos (funcionários sintéticos do seed).
5. Regressão: export "femsa" → 79 colunas sem extras; "geral" → 83.
6. `/api/me` retorna role; GET /api/users como admin lista.
Relatar resultados estruturados (pass/fail por item + arquivos corrigidos).
