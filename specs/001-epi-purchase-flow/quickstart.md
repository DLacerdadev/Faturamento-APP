# Quickstart — Fluxo de Compra de EPIs por Funcionário

Roteiro para validar a feature end-to-end. Pré-requisito: app subido (ver `RUNBOOK.md`).

## Setup

1. Em produção, executar a migração:

   ```sql
   ALTER TABLE epi_purchase_packages ADD COLUMN codccu VARCHAR(20);
   ALTER TABLE epi_purchase_items   ADD COLUMN employee_numcad INTEGER;
   ALTER TABLE epi_purchase_items   ADD COLUMN employee_nome VARCHAR(200);
   CREATE INDEX IF NOT EXISTS ix_epi_purchase_packages_codccu ON epi_purchase_packages(codccu);
   CREATE INDEX IF NOT EXISTS ix_epi_purchase_items_employee  ON epi_purchase_items(employee_numcad);
   ```

   Em dev, `init_db()` cria as colunas novas se `app.db` não existir; se já existir, rodar o ALTER manualmente.

2. Iniciar o servidor:

   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

3. Logar com `ti@grupoopus.com` / `telos@2026`.

## Cenário 1 — Caminho feliz (Acceptance Scenario 1)

Objetivo: validar SC-1 (≤ 2 min para 30 linhas).

1. Acessar **http://127.0.0.1:8000/epis**.
2. Clicar em **Nova Compra**.
3. Selecionar **Centro de custo** = `620039` (ou qualquer CCU com >10 ativos).
4. Aguardar a lista de funcionários ativos carregar (deve aparecer em ≤ 3s — SC-3).
5. Marcar **10 funcionários** na lista.
6. Adicionar 3 itens:
   - Capacete classe B, qtde=1, valor=R$ 50
   - Luva de raspa par, qtde=2, valor=R$ 15
   - Óculos UV, qtde=1, valor=R$ 25
7. Salvar.

**Esperado**:
- Mensagem de sucesso.
- Listagem mostra 1 pacote com `total_linhas = 30`, `funcionarios_distintos = 10`, `itens_distintos = 3`, `valor_total = R$ 800,00` (10×50 + 10×30 + 10×25).

## Cenário 2 — Validações (Acceptance Scenarios 4 e 5)

1. Em **Nova Compra**, escolher CCU, marcar funcionários, **NÃO adicionar nenhum item**, tentar salvar.

   **Esperado**: erro inline "Adicione ao menos 1 item.". Sem chamada ao backend.

2. Mesma tela, adicionar item mas **desmarcar todos os funcionários**, tentar salvar.

   **Esperado**: erro inline "Selecione ao menos 1 funcionário.".

## Cenário 3 — Filtro por CCU (Acceptance Scenario 2)

1. Trocar o CCU para `640053`.

   **Esperado**: lista de funcionários **atualiza** e mostra somente funcionários ativos do CCU `640053`. Estado anterior (seleções) é resetado com aviso.

## Cenário 4 — Revalidação FR-13 (Acceptance Scenario edge)

Simulação manual:

1. Abrir o form e selecionar funcionário `X` (CCU `620039`).
2. **Sem salvar**, alterar manualmente o `app.db` para simular demissão do `X`:

   ```sql
   UPDATE billing_employees SET data_afastamento = '2020-01-01' WHERE numcad = <X>;
   ```

   (ou em produção: aguardar uma demissão real entre os passos.)

3. Voltar ao form e clicar em **Salvar**.

**Esperado**: HTTP 409 do backend; UI exibe modal listando funcionário `X` como "não mais ativo" e exige decisão (remover do save / cancelar).

## Cenário 5 — Edição (Acceptance Scenario 7)

1. Na listagem, clicar em um pacote criado.
2. Alterar a lista de funcionários (adicionar 1, remover 1) e adicionar 1 item novo.
3. Salvar.

**Esperado**: o número de linhas é recalculado (cartesiano novo). Documentos anexos preservados.

## Cenário 6 — Auditoria (SC-4 e SC-5)

Via banco:

```sql
-- "O que o colaborador 12345 recebeu nos últimos 12 meses?" (SC-4)
SELECT p.mes_ano, i.descricao, i.quantidade, i.valor_total
FROM   epi_purchase_items i
JOIN   epi_purchase_packages p ON p.id = i.package_id
WHERE  i.employee_numcad = 12345
  AND  p.mes_ano >= date('now','-12 months')
ORDER BY p.mes_ano DESC;

-- "Quantos capacetes foram entregues no CCU 620039 este mês?" (SC-5)
SELECT SUM(i.quantidade) AS total_capacetes, SUM(i.valor_total) AS valor
FROM   epi_purchase_items i
JOIN   epi_purchase_packages p ON p.id = i.package_id
WHERE  p.codccu = '620039'
  AND  p.mes_ano = date('now','start of month')
  AND  i.descricao LIKE '%capacete%';
```

Ambas devem responder em uma consulta, sem cruzar planilhas externas.

## Cenário 7 — Regressão (SC-6)

Após o deploy, acessar e usar normalmente:
- `/billing` — folha
- `/customers` — clientes
- `/reports` — relatórios
- `/integrations/senior/cost-centers` — lista de CCUs

Nenhuma das telas deve apresentar erro novo. Console do navegador limpo.
