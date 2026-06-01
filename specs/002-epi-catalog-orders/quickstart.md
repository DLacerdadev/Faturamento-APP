# Quickstart — Catálogo de EPIs e Pedido de Compra

Roteiro para validar a feature 002 end-to-end. Pré-requisito: app subido na porta 8000.

## Setup

1. Aplicar migração 002 (uma vez):

   ```sql
   CREATE TABLE IF NOT EXISTS epi_catalog (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       nome VARCHAR(200) NOT NULL,
       ativo BOOLEAN NOT NULL DEFAULT 1,
       created_at DATETIME,
       updated_at DATETIME
   );
   CREATE UNIQUE INDEX IF NOT EXISTS ix_epi_catalog_nome_upper
       ON epi_catalog(UPPER(nome)) WHERE ativo = 1;

   CREATE TABLE IF NOT EXISTS epi_catalog_sizes (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       epi_id INTEGER NOT NULL REFERENCES epi_catalog(id) ON DELETE CASCADE,
       tamanho VARCHAR(20) NOT NULL,
       valor FLOAT NOT NULL,
       UNIQUE (epi_id, tamanho)
   );
   CREATE INDEX IF NOT EXISTS ix_epi_catalog_sizes_epi_id ON epi_catalog_sizes(epi_id);

   ALTER TABLE epi_purchase_packages ADD COLUMN solicitante_nome VARCHAR(200);
   ALTER TABLE epi_purchase_packages ADD COLUMN quantidade_total_geral INTEGER;
   ALTER TABLE epi_purchase_packages ADD COLUMN valor_total_compra_geral FLOAT;
   ALTER TABLE epi_purchase_packages ADD COLUMN solicitacao_filename VARCHAR(500);
   ALTER TABLE epi_purchase_packages ADD COLUMN solicitacao_generated_at DATETIME;

   ALTER TABLE epi_purchase_items ADD COLUMN epi_id INTEGER REFERENCES epi_catalog(id);
   ALTER TABLE epi_purchase_items ADD COLUMN tamanho VARCHAR(20);
   ALTER TABLE epi_purchase_items ADD COLUMN quantidade_por_funcionario INTEGER;
   ALTER TABLE epi_purchase_items ADD COLUMN valor_unitario_catalogo FLOAT;
   CREATE INDEX IF NOT EXISTS ix_epi_purchase_items_epi_id ON epi_purchase_items(epi_id);
   ```

2. Adicionar variáveis ao `.env` (opcional para email):

   ```dotenv
   SMTP_HOST=
   SMTP_PORT=587
   SMTP_USER=
   SMTP_PASSWORD=
   SMTP_FROM=noreply@grupoopus.com
   SMTP_USE_TLS=1
   EPI_PURCHASE_EMAIL=compras@grupoopus.com
   ```

3. Reiniciar uvicorn.

## Cenário 1 — Catálogo: cadastrar EPI com múltiplos tamanhos (Acceptance 1)

Objetivo: validar SC-1 (cadastro em ≤ 1 min).

1. Acessar **http://127.0.0.1:8000/catalogo-epis**.
2. Clicar em **Novo EPI**.
3. Nome: "Luva de raspa".
4. Adicionar 3 tamanhos:
   - P → R$ 10,00
   - M → R$ 12,00
   - G → R$ 15,00
5. Salvar.

**Esperado**: EPI aparece imediatamente na listagem com `in_use_count = 0` e `ativo = true`.

## Cenário 2 — Catálogo: tamanho único (Acceptance 2)

1. Em **Novo EPI**, criar "Protetor solar FPS 50" com tamanho único:
   - Único → R$ 25,00
2. Salvar.

**Esperado**: EPI cadastrado com 1 entrada de tamanho.

## Cenário 3 — Catálogo: nome duplicado bloqueado

1. Tentar criar outro EPI com nome "Luva de raspa" (mesma capitalização ou diferente, ex: "LUVA DE RASPA").

**Esperado**: erro 409 "Já existe um EPI ativo com este nome".

## Cenário 4 — Compra: criar com catálogo, gerar solicitação (Acceptance 3, 5)

Objetivo: validar SC-2 (compra + solicitação em ≤ 3 min).

1. Acessar **http://127.0.0.1:8000/epis**.
2. Clicar em **Nova Compra**.
3. CCU: `620039`. Aguardar lista de funcionários carregar.
4. Marcar **5 funcionários**.
5. Em "Itens", adicionar:
   - EPI: "Luva de raspa", tamanho M → quantidade por funcionário: 2 (valor pré-preenchido: R$ 12,00)
   - EPI: "Protetor solar FPS 50", tamanho Único → quantidade: 1 (valor: R$ 25,00)
6. Conferir sumário em tempo real:
   - Item 1: qtde total 10, valor total R$ 120,00.
   - Item 2: qtde total 5, valor total R$ 125,00.
   - Total geral: 15 unidades, R$ 245,00.
7. Clicar em **Salvar e Solicitar Compra**.

**Esperado**:
- Toast "Compra criada com 10 linhas, R$ 245,00".
- Link **Baixar solicitação** aparece imediatamente.
- Botão **Enviar por email** aparece se SMTP configurado (esmaecido se não).

8. Clicar em **Baixar solicitação**.

**Esperado**: download de `solicitacao_epi_<id>_<timestamp>.xlsx` com:
- Cabeçalho: empresa FEMSA, CCU `620039`, competência `2026-05`, solicitante = você, data/hora atual.
- Tabela: 2 linhas de itens com nome, tamanho, qtde por funcionário (2 e 1), funcionários atendidos (5), qtde total (10 e 5), valor unit. e total.
- Linha de TOTAL GERAL: 15 / R$ 245,00.
- Bloco "Funcionários atendidos": 5 linhas (matrícula + nome).

## Cenário 5 — Override de valor (Acceptance correlato à FR-8)

1. Em uma nova compra, escolher um EPI/tamanho com valor de catálogo R$ 50,00.
2. Mudar o valor unitário para R$ 45,00 no campo.

**Esperado**:
- Aviso amarelo "Valor difere do catálogo (catálogo: R$ 50,00)".
- Sumário atualiza para R$ 45,00.
- Ao salvar, persiste R$ 45,00 em `valor_unitario` e R$ 50,00 em `valor_unitario_catalogo`.
- Excel mostra o valor R$ 45,00 em "Valor unit.", e opcionalmente uma marca "(*)" indicando override.

## Cenário 6 — Cálculos reativos (Acceptance 4)

1. Em compra em rascunho com 5 funcionários e 1 item (qtde 2, valor 12), confirmar sumário = 10 unidades / R$ 120,00.
2. Marcar mais 3 funcionários.

**Esperado** (≤ 200ms, SC-3): sumário atualiza para 16 unidades / R$ 192,00 sem clicar em recalcular.

## Cenário 7 — Email da solicitação (se SMTP configurado)

1. Em uma compra salva, clicar em **Enviar por email**.
2. Confirmar destinatário pré-preenchido (vindo de `EPI_PURCHASE_EMAIL`).
3. Opcionalmente editar para outro email.
4. Confirmar envio.

**Esperado**: toast "Email enviado para X". Conferir caixa de destino que o anexo Excel chegou.

Sem SMTP configurado:
- Botão fica esmaecido com tooltip "SMTP não configurado no servidor".

## Cenário 8 — Pedidos legados da feature 001 (Acceptance 7)

1. Acessar `/epis`, localizar um pacote criado antes da migração 002 (`epi_id IS NULL`).
2. Confirmar visualmente:
   - Badge "Legado" ao lado do ID.
   - Coluna "Solicitação": "—" ou botão desabilitado com tooltip "Compra criada antes do catálogo de EPIs — complete os itens com EPI/tamanho catalogados em uma nova compra para gerar solicitação".
3. Tentar excluir: deve funcionar (DELETE não bloqueia legados).

## Cenário 9 — Desativar EPI com pedidos vinculados (Acceptance correlato a FR-4)

1. No `/catalogo-epis`, achar "Luva de raspa" (usado na compra do Cenário 4).
2. Clicar em **Desativar**.

**Esperado**:
- Confirmação com aviso: "Este EPI tem 1 pedido vinculado; ele continuará visível neles mas não aparece para novos pedidos."
- Após confirmar, EPI fica `ativo=false`.
- No `/epis`, ao tentar criar nova compra, "Luva de raspa" não aparece no dropdown.
- A compra existente continua mostrando o EPI normalmente.

## Cenário 10 — Auditoria por catálogo (SC-5)

Via SQL:

```sql
-- "Quantas compras usam o EPI X?"
SELECT epi.nome, COUNT(DISTINCT i.package_id) AS compras
FROM   epi_purchase_items i
JOIN   epi_catalog epi ON epi.id = i.epi_id
GROUP  BY epi.nome
ORDER  BY compras DESC;
```

Deve responder em 1 query, sem joins externos.

## Cenário 11 — Regressão (SC-6)

Após o deploy, acessar e usar normalmente:
- `/billing`, `/customers`, `/reports`, `/dashboard`, `/epis` (com novos campos), `/catalogo-epis` (novo).

Nenhum 5xx no log. Console do browser limpo. Compras criadas pela feature 001 continuam carregando.
