# Plano multi-task — Compras genéricas + Confirmação + Modelos de faturamento

> Objetivo: atacar em **frentes paralelas que não se atrapalham** (cada frente é dona de arquivos
> distintos). Ao final, rodar com multi-agents em 2 ondas.

## Objetivos (o que o usuário pediu)
1. **Tela de "qualquer pedido"** — na aba Solicitar, permitir pedido de **EPI, Uniforme e Equipamento** (não só EPI), por funcionário.
2. **Confirmação simplificada** — **remover** a exigência de subir recibo. Basta um botão **"Confirmar recebimento"** → o pedido passa a **aparecer no faturamento** do mês.
3. **Modelos de faturamento configuráveis** — criar um modelo **GERAL** (cópia do FEMSA **+** os campos novos de itens) que é a **base de todos os contratos**. Ao criar um novo modelo/contrato, escolhe-se **coluna por coluna** quais campos ele terá.

## Decisões assumidas (confirmar antes de rodar)
- **A1 — Sem recibo, sem conciliação de preço:** como não haverá recibo, **não haverá** a validação de valor pago × catálogo (a regra dos R$ 50 sai). O faturamento usa os valores do **pedido** (qtd × preço efetivo por funcionário: preço do CC → senão catálogo).
- **A2 — Colunas novas do GERAL (por funcionário):** `UNIFORMES (Valor)`, `EPIS (Valor)`, `EQUIPAMENTOS (Valor)`, `TREINAMENTOS (Valor)`. (Confirmar nomes/quantidade — 1 coluna de valor por tipo.)
- **A3 — Contrato → modelo:** cada contrato aponta para um modelo; a exportação usa as colunas **daquele modelo**. **FEMSA continua idêntico** (modelo FEMSA = as 79 colunas atuais).
- **A4 — Treinamentos:** entra como **coluna** no GERAL agora, mas o **lançamento de treinamento por funcionário** (quem fez qual) é uma frente à parte (fora deste plano) — confirmar se inclui já.
- **A5 — "Só confirmados entram no faturamento":** pedidos em rascunho/solicitado não entram; só `status = confirmado`.

---

## Frentes (donas de arquivos disjuntos = sem conflito)

### FRENTE 1 — Compras: pedido genérico + confirmação
**Dona dos arquivos:** `app/templates/epis.html`, `app/routers/epi_purchases.py`
- **T1.1** `package_to_dict`: exibir itens de **produto** (hoje item sem `epi_id` é tratado como "legado"); agrupar por `produto_codigo`; incluir `categoria`.
- **T1.2** Aba **Solicitar** genérica: seletor de **categoria** (EPI/Uniforme/Equipamento); combobox de item conforme categoria (EPI = catálogo de EPIs; uniforme/equip = `product_catalog` por categoria via `/api/products`); **colunas condicionais** (C.A só EPI; tamanho EPI/uniforme; equip sem os dois); enviar `categoria` + `linhas_produto` (backend já pronto).
- **T1.3** **Confirmar recebimento**: endpoint `POST /api/epi-purchases/{id}/confirmar` (seta `status='confirmado'`); botão na aba Confirmar + badge de status; (opcional) desfazer confirmação.

### FRENTE 2 — Modelos de faturamento configuráveis (GERAL)
**Dona dos arquivos:** `app/models/billing_model.py` (novo), `app/routers/billing_models.py` (novo), `app/templates/modelos_faturamento.html` (novo), **refactor** `app/services/excel_export.py`, + registro em `app/db.py`/`app/main.py`
- **T2.1** Model `BillingModel` (nome, base/GERAL, ativo) + colunas do modelo (lista ordenada de nomes de coluna + flags). Seed **FEMSA** (79 colunas) e **GERAL** (79 + colunas novas A2). Migração aditiva.
- **T2.2** Refactor `excel_export`: montar a planilha a partir das **colunas do modelo** (não mais `FEMSA_COLUMNS` fixo). FEMSA = mesmo resultado de hoje (regressão zero).
- **T2.3** Tela **Modelos de faturamento**: listar/criar modelo escolhendo colunas **1 a 1** (partindo do GERAL); associar modelo ao **contrato** (`Company.billing_model_id`).

### FRENTE 3 — Ligar itens no faturamento (por funcionário) — DEPENDE de F2 e F1
**Dona dos arquivos:** `app/services/excel_export.py` (após F2), `app/routers/integrations.py`
- **T3.1** Agregar compras **confirmadas** (EPI/uniforme/equip) por **CPF/numcad** e lançar nas colunas do modelo (UNIFORMES/EPIS/EQUIPAMENTOS Valor). Entra no Subtotal → taxa adm → gross-up → impostos.
- **T3.2** Coluna TREINAMENTOS no modelo GERAL (valor por funcionário quando existir a base — placeholder até a frente de treinamentos).

---

## Mapa de conflito (garantia de "um não atrapalha o outro")
| Arquivo | F1 | F2 | F3 |
|---|:--:|:--:|:--:|
| `templates/epis.html` | ✅ | | |
| `routers/epi_purchases.py` | ✅ | | |
| `models/billing_model.py` (novo) | | ✅ | |
| `routers/billing_models.py` (novo) | | ✅ | |
| `templates/modelos_faturamento.html` (novo) | | ✅ | |
| `services/excel_export.py` | | ✅ | ✅ (depois) |
| `routers/integrations.py` | | | ✅ |
| `db.py` / `main.py` | | ✅ | |

- **F1 e F2 são 100% disjuntos → rodam em PARALELO.**
- **F3 toca `excel_export.py` (compartilhado com F2) → roda DEPOIS da F2** (build sobre o refactor).

## Execução multi-agent (2 ondas)
- **Onda 1 (paralela, isolada em worktrees):** F1 e F2 simultâneas.
- **Onda 2 (após F2):** F3 (usa as colunas do modelo + o status confirmado da F1).
- Cada agente: implementa + compila/valida seus arquivos; sem tocar arquivos de outra frente.
- Ao final: integração num só worktree, subir servidor local, teste E2E (pedido uniforme → confirmar → aparece no faturamento com a coluna do modelo).

## Fora deste plano (próximas frentes)
- Lançamento de **treinamentos por funcionário** (fluxo estilo exames).
- **Exames por e-mail** (Fase 2 dos exames).
- Redeploy na VPS quando o conjunto estiver validado.
