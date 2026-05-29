# Research — Fluxo de Compra de EPIs por Funcionário

Decisões técnicas para apoiar `plan.md`. Cada item segue: **Decisão / Racional / Alternativas**.

---

## R1 — Modelagem do cartesiano (linha plana vs. tabela junction)

**Decisão**: linha plana. Adicionar `employee_numcad` (Integer, nullable, indexado) e `employee_nome` (String 200, nullable) diretamente em `epi_purchase_items`. Cada row passa a representar 1 par (funcionário × item).

**Racional**:
- O usuário pediu literalmente "5 funcionários × 2 itens = 10 linhas". A linha plana materializa essa semântica sem precisar de joins na leitura.
- O esquema atual já tem `epi_purchase_items` como tabela de linhas; estender é menos disruptivo que introduzir junction.
- FR-9 (edição) é "recalcula o cartesiano ao salvar" — delete-all + insert-all do pacote é trivial sem junction.
- Snapshot por linha (P4) é natural: `employee_nome` fica gravado no momento do save e nunca depende do Senior depois.

**Alternativas consideradas**:
- **Junction `epi_purchase_item_employees`**: mais normalizado (item-template + N vínculos). Permitiria "este item foi planejado, depois ampliamos para mais funcionários". Custo: 1 tabela a mais, 1 join em toda consulta, ganho marginal porque a feature não tem "item-template" como conceito de usuário.
- **JSON-array em `epi_purchase_items.employees`**: descartado — quebra queryability ("quais EPIs o funcionário X recebeu este ano?" exigiria varrer JSON).

---

## R2 — Filtro `codccu` na lista de funcionários

**Decisão**: estender o endpoint existente `GET /api/integrations/senior/employees` com dois novos query params opcionais:
- `?codccu=<código>` — filtra funcionários pelo centro de custo
- `?active_only=true` — aplica regra de "ativo" (R3)

**Racional**:
- Endpoint já existe em [`app/routers/integrations.py:589-599`](FATURAMENTO-APP/app/routers/integrations.py#L589-L599) e devolve a lista completa de funcionários TELOS. Adicionar query params é retro-compatível: clientes existentes sem params continuam recebendo tudo.
- Evita proliferação de endpoints (`/active-by-ccu`, `/by-ccu`, etc.).

**Alternativas consideradas**:
- Endpoint novo `/senior/active-employees?codccu=`: descartado por duplicar lógica.
- Filtragem client-side (frontend baixa todos e filtra): descartado pelo volume (centenas/milhares de funcionários) e pela revalidação server-side que precisa do mesmo predicado de "ativo" (DRY).

---

## R3 — Definição programática de "funcionário ativo"

**Decisão**: criar função utilitária em `senior_connector.py`:

```python
def is_employee_active(emp: dict, today: date | None = None) -> bool:
    """Ativo = sem data_afastamento, sentinel 31/12/1900, ou data futura."""
    today = today or date.today()
    datafa = emp.get("datafa")  # 'YYYY-MM-DD' ou 'DD/MM/YYYY' ou None
    if not datafa:
        return True
    # sentinel Senior
    if str(datafa).startswith("1900") or datafa == "31/12/1900":
        return True
    parsed = _parse_senior_date(datafa)  # helper já existente ou novo
    if parsed is None:
        return True  # data ilegível → tolerância: considera ativo
    return parsed > today
```

E uma função pública `fetch_active_employees(codccu: str) -> List[dict]` que envelopa `fetch_employees_telos()` + filtro por `codccu` + filtro por `is_employee_active`.

**Racional**:
- Critério já escolhido em Q1 da spec.
- Centralizar o predicado em uma função evita divergência entre listagem (front) e revalidação (save).
- Sentinel `31/12/1900` é padrão Senior conhecido ([app/services/excel_export.py:611-620](FATURAMENTO-APP/app/services/excel_export.py#L611-L620) já trata).

**Alternativas consideradas**:
- Usar `sitafa == 1` do Senior: descartado em Q1 — exclui afastados temporários (acidente, licença) que ainda precisam de EPI.
- Reusar a função de cutoff de `excel_export.py`: descartado — aquela usa cutoff de 2 meses contra mês de referência, semântica diferente.

---

## R4 — Componente multi-select

**Decisão**: implementar inline em vanilla JS dentro de `epis.html`, com:
- Campo de busca textual (filtra por nome e matrícula)
- Lista de checkboxes virtualizada (mostrar máx. 50 visíveis por vez para perf com 500+ funcionários)
- Contador de selecionados no topo ("3 selecionados")
- Botões "Selecionar todos visíveis" / "Limpar"

**Racional**:
- P3 proíbe libs JS novas. Vanilla JS é viável: padrão simples de checkbox + filter.
- A virtualização cuida do edge case de CCUs grandes.

**Alternativas consideradas**:
- Lib externa (Select2, Choices.js): viola P3.
- `<select multiple>` nativo: UX ruim (sem busca, sem contador, mantém só tecla Ctrl).

---

## R5 — Revalidação FR-13

**Decisão**: no `POST /api/epi-purchases` (e `PUT`):
1. Receber `codccu` e `employees: [{numcad, nome}]`.
2. Chamar `fetch_active_employees(codccu)`.
3. Cruzar `numcad`s recebidos contra a lista resultante.
4. Se qualquer `numcad` recebido não estiver na lista de ativos atuais, retornar `409 Conflict` com payload `{ "status": "stale", "inactive": [{numcad, nome, motivo}] }`.
5. Caso contrário, persistir o cartesiano.

**Racional**:
- Q3 escolheu "bloquear + listar afetados".
- Server-side é a única opção segura (o front pode ter dados estagnados).
- 409 é o código HTTP mais correto (conflict de estado).

**Alternativas consideradas**:
- Validação only no front (consultar Senior antes do submit): descartada — corrida possível, e front malicioso pula a validação.
- Persistir e marcar com flag (Q3 opção B): rejeitada pelo usuário.

---

## R6 — Estratégia de migração compatível

**Decisão**: novas colunas todas `nullable=True`. `init_db()` (SQLAlchemy `create_all`) cuida do SQLite dev (cria tabelas faltantes, mas **não altera** tabelas existentes). Para o `app.db` já populado, será necessário executar `ALTER TABLE` uma vez:

```sql
ALTER TABLE epi_purchase_packages ADD COLUMN codccu VARCHAR(20);
ALTER TABLE epi_purchase_items   ADD COLUMN employee_numcad INTEGER;
ALTER TABLE epi_purchase_items   ADD COLUMN employee_nome   VARCHAR(200);
```

Documentar no `RUNBOOK.md`. Em produção (PostgreSQL via docker-compose), o mesmo SQL roda. Linhas legadas ficam com `NULL` — UI nova exibe rótulo "(legado, sem funcionário)" ao listar.

**Racional**: cumpre P5. Zero perda de dado. Front consegue distinguir legacy via NULL.

**Alternativas consideradas**:
- Migração destrutiva (apagar legacy): rejeitada — dados em prod.
- Backfill com `numcad=0`: cria valor sentinel sujo no banco, descartado.

---

## Resumo do impacto no código

| Arquivo | Mudança |
|---|---|
| `app/models/epi_purchase.py` | + 1 col em Package, + 2 cols em Item |
| `app/services/senior_connector.py` | + `is_employee_active`, + `fetch_active_employees` |
| `app/routers/integrations.py` | endpoint `/senior/employees` ganha 2 query params |
| `app/routers/epi_purchases.py` | POST/PUT aceitam `codccu` + `employees[]`; expansão cartesiana; revalidação 409 |
| `app/templates/epis.html` | **novo** |
| `app/routers/views.py` (ou similar) | + rota GET `/epis` que serve o template |
| `RUNBOOK.md` | + seção "Migração 001 — EPI por funcionário" |
