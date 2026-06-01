# Feature Specification: Catálogo de EPIs e Pedido de Compra com Solicitação

**Feature ID**: 002-epi-catalog-orders
**Created**: 2026-05-29
**Status**: Ready for `/speckit-plan`
**Spec File**: spec.md
**Predecessora**: [001-epi-purchase-flow](../001-epi-purchase-flow/spec.md)

## Overview

Evoluir o fluxo de EPI implementado em [001-epi-purchase-flow](../001-epi-purchase-flow/) introduzindo três peças que faltam para fechar o ciclo de compra: **catálogo de EPIs**, **cálculos persistidos** e **solicitação de compra como output**.

Hoje o usuário do RH cria um "pacote" digitando manualmente descrição/quantidade/valor de cada item, sem catálogo de referência. Cada pacote é só registro interno — não há documento gerado para enviar ao setor de compras / fornecedor. Os totais existem só em memória na tela, não persistidos.

A nova feature substitui digitação livre por **seleção de EPI cadastrado + tamanho**, onde o cadastro centraliza nome, tamanhos disponíveis e preço por tamanho. O pedido passa a calcular e **persistir** dois totais ao salvar: quantidade total (funcionários × qtde por funcionário) e valor total (qtde total × valor unitário). Por fim, qualquer pedido salvo pode gerar uma **solicitação de compra** — um documento ou email contendo CCU, qtde total e valor total — pronto para ser enviado ao setor de compras.

## User Scenarios & Testing

### Primary Flow

**Pré-requisito (acontece antes, uma única vez):**

1. Usuário acessa "Catálogo de EPIs" (nova tela).
2. Cadastra um EPI: nome ("Capacete classe B"), tamanhos disponíveis ([P, M, G, GG] ou ["Único"]), e o valor unitário de cada tamanho.

**Fluxo principal (criar pedido + gerar solicitação):**

1. Usuário acessa a tela de pedidos de EPI.
2. Escolhe centro de custo (CCU) e data de competência (mês/ano).
3. Sistema carrega funcionários ativos do CCU (mesma regra da feature 001) num multi-select.
4. Usuário marca um ou mais funcionários.
5. Usuário escolhe um EPI do catálogo (autocomplete pelo nome).
6. Sistema carrega os tamanhos cadastrados para esse EPI; usuário escolhe um tamanho.
7. Valor unitário é preenchido automaticamente do catálogo (read-only ou editável — ver Q1 abaixo).
8. Usuário digita quantidade por funcionário (ex: 2 luvas).
9. Sistema exibe em tempo real:
   - Quantidade total = funcionários × qtde por funcionário.
   - Valor total da compra = qtde total × valor unitário.
10. Usuário clica em "Salvar pedido". Pedido é persistido com os dois totais calculados.
11. Na listagem de pedidos, usuário clica em "Gerar solicitação de compra" sobre o pedido salvo.
12. Sistema gera um documento (formato definido em Q2) contendo no mínimo: CCU, quantidade total, valor total. Documento fica disponível para download ou é enviado por email.

### Acceptance Scenarios

- **Scenario 1 — Cadastro de EPI com múltiplos tamanhos**: Dado o catálogo vazio, quando o usuário cadastra "Luva de raspa" com tamanhos [P, M, G] e valores [10.00, 12.00, 15.00], então o EPI é salvo e aparece imediatamente disponível na tela de pedido. Selecionar tamanho M no pedido preenche valor R$ 12,00 automaticamente.
- **Scenario 2 — Cadastro com tamanho único**: Dado um EPI sem variação de tamanho (ex: "Protetor solar"), quando o usuário cadastra com tamanho "Único" e valor único, então o pedido oferece esse tamanho como única opção pré-selecionada.
- **Scenario 3 — Cálculo de totais ao criar pedido**: Dado 8 funcionários selecionados, EPI "Capacete classe B" tamanho "Único" valor R$ 50, quantidade=1 por funcionário, então a tela mostra "Qtde total: 8 / Valor total: R$ 400,00" antes do clique em Salvar. Após salvar, esses valores estão persistidos no pedido.
- **Scenario 4 — Totais reativos**: Dado um pedido em edição com 5 funcionários e qtde=2 (total = 10 unidades, R$ 100), quando o usuário marca mais 3 funcionários, então o total atualiza instantaneamente para 16 unidades / R$ 160 — sem necessidade de re-clicar em "calcular".
- **Scenario 5 — Solicitação de compra gerada**: Dado um pedido salvo com CCU "620039", quantidade total 30 e valor total R$ 450,00, quando o usuário clica em "Gerar solicitação", então o sistema produz um documento contendo esses três campos, pronto para download/envio.
- **Scenario 6 — EPI cadastrado e múltiplos pedidos**: Dado o EPI "Luva tamanho G" cadastrado com valor R$ 15, quando dois pedidos diferentes usam esse EPI, então ambos lêem o valor atual do catálogo. Se o catálogo for atualizado depois, pedidos NOVOS lêem o valor novo; pedidos JÁ SALVOS mantêm o snapshot de valor da época do save (rastreabilidade).
- **Scenario 7 — EPI sem catálogo** (legado): Dada uma compra antiga da feature 001 sem `epi_id`, quando o usuário acessa a listagem unificada de pedidos, então essa compra aparece marcada como "legado" e pode ser visualizada mas a geração de solicitação de compra é bloqueada ou avisa que falta o vínculo com catálogo.

### Edge Cases

- Catálogo vazio quando o usuário entra na tela de pedido: exibir aviso + atalho para cadastrar primeiro.
- EPI cadastrado com 0 tamanhos cadastrados: bloquear no salvamento do catálogo (precisa ≥1 tamanho com valor > 0).
- Excluir um EPI do catálogo que tem pedidos vinculados: bloquear com mensagem "Existem N pedidos usando este EPI; não pode excluir, apenas desativar".
- Editar valor de um tamanho no catálogo: avisar que pedidos novos usarão o valor novo, pedidos existentes mantêm snapshot.
- Pedido sem nenhum funcionário ou sem item válido: bloquear no salvamento (já coberto na feature 001).
- Quantidade por funcionário = 0 ou negativa: bloquear.

## Functional Requirements

### Catálogo de EPIs

- **FR-1**: O sistema deve oferecer uma tela de catálogo de EPIs (separada da tela de pedidos) com CRUD: listar, criar, editar, desativar (soft-delete).
- **FR-2**: Cada EPI cadastrado contém: nome (texto livre, único — não permite dois EPIs com mesmo nome ativo), lista de tamanhos disponíveis, e valor unitário por tamanho.
- **FR-3**: O cadastro deve aceitar tanto EPIs com tamanhos variados (P, M, G, GG, …) quanto EPIs sem variação de tamanho (tamanho "Único"). Mínimo de 1 entrada de tamanho por EPI.
- **FR-4**: Não é possível excluir um EPI que tem pedidos associados; o usuário pode apenas "desativar" o EPI, que o esconde da listagem de seleção em novos pedidos mas mantém visível em pedidos antigos.
- **FR-5**: A listagem do catálogo permite busca por nome.

### Pedido de Compra (entidade evoluída)

- **FR-6**: Um pedido de compra tem os campos: data de competência (mês/ano), centro de custo, lista de funcionários, EPI selecionado do catálogo, tamanho selecionado, quantidade por funcionário, valor unitário (snapshot do catálogo no momento do save).
- **FR-7**: A escolha do EPI deve ser feita por seleção de item do catálogo (não por digitação livre). A escolha do tamanho é restrita aos tamanhos cadastrados para aquele EPI.
- **FR-8**: O valor unitário é preenchido automaticamente a partir do tamanho selecionado no catálogo, **mas pode ser editado pelo usuário no pedido**. Se o valor digitado diferir do catálogo, o sistema exibe um aviso visível ("Valor difere do catálogo: catálogo = R$ X, digitado = R$ Y") e persiste o valor digitado no pedido como override. O valor do catálogo NÃO é alterado nesse caso.
- **FR-9**: Ao salvar, o sistema deve calcular e persistir dois novos totais junto com o pedido:
  - `quantidade_total = count(funcionarios) × quantidade_por_funcionario`
  - `valor_total_compra = quantidade_total × valor_unitario`
- **FR-10**: A tela de criação/edição de pedido deve exibir **em tempo real** (sem requerer botão de "recalcular") os dois totais à medida que o usuário marca funcionários, escolhe EPI/tamanho, ou altera a quantidade.
- **FR-11**: Cada **compra** (entidade-pedido) agrupa **múltiplos itens**, sendo cada item uma combinação distinta de (EPI cadastrado, tamanho, quantidade por funcionário, valor unitário). A compra tem 1 CCU, 1 mês de competência e N itens. Cada item gera uma linha por funcionário selecionado (cartesiano preservado da feature 001). Os totais (`quantidade_total`, `valor_total`) são calculados por item E há um total geral por compra somando todos os itens.
- **FR-11.1**: O ato de "Salvar Compra" também é o ato de "Solicitar Compra" — não há fluxo separado. Cada clique em Salvar/Atualizar persiste a compra **e** gera (regenera) uma solicitação de compra associada.
- **FR-12**: O critério de "funcionário ativo no CCU" continua o mesmo definido na feature 001 (FR-3): sem afastamento ou afastamento futuro na data de hoje.
- **FR-13**: Revalidação server-side ao salvar (FR-13 da feature 001): se algum funcionário deixou de ser ativo, retorna conflito e exige decisão do usuário.

### Solicitação de Compra (output)

- **FR-14**: A solicitação de compra é gerada automaticamente sempre que uma compra é salva ou atualizada (mesma ação, ver FR-11.1). O usuário não precisa clicar em "gerar solicitação" separadamente.
- **FR-15**: A solicitação contém:
  - **Cabeçalho**: nome da empresa solicitante (FEMSA), centro de custo (código + nome), data de competência, data/hora de geração, **nome do solicitante** (usuário logado que clicou em Salvar).
  - **Lista de itens** (uma linha por item da compra): nome do EPI, tamanho, quantidade total, valor unitário, valor total do item.
  - **Total geral** da compra: soma das quantidades totais e soma dos valores totais de todos os itens.
  - **Anexo opcional**: lista de funcionários atendidos (matrícula + nome), exibida abaixo do bloco principal para referência interna.
- **FR-16**: A solicitação é gerada em **Excel (download)** automaticamente ao salvar — o arquivo fica disponível para download imediato e também acessível pela listagem de compras (link "Baixar solicitação"). Adicionalmente, se SMTP estiver configurado no sistema (`SMTP_HOST` + credenciais no `.env`), o sistema oferece um botão **"Enviar por email"** com destinatário configurável (default: campo `EPI_PURCHASE_EMAIL` em `.env`, sobrescritível na hora pelo usuário). Sem SMTP configurado, apenas o download fica disponível e a opção de email aparece esmaecida com tooltip explicativo.
- **FR-17**: Pedidos legados (da feature 001 sem EPI catalogado) aparecem na listagem com um badge "Legado" visível. O botão "Baixar solicitação" / "Enviar por email" fica **desabilitado** nesses pedidos, com tooltip explicativo: "Compra criada antes do catálogo de EPIs — complete os itens com EPI/tamanho catalogados em uma nova compra para gerar solicitação". Não há fluxo de migração automática nesta versão.

### Integração com Feature 001

- **FR-18**: As regras de CCU, multi-select de funcionários ativos e revalidação server-side da feature 001 são reutilizadas integralmente. Não há mudança no SOAP Senior.
- **FR-19**: Pedidos criados pela feature 002 e pacotes da feature 001 podem coexistir; a listagem unificada distingue claramente legado (sem EPI catalogado) de novo (com EPI + totais persistidos).

## Success Criteria

- **SC-1**: Um usuário cadastra um novo EPI (com 4 tamanhos) em menos de 1 minuto a partir do clique em "Novo EPI".
- **SC-2**: Um usuário cria um pedido completo (CCU + 10 funcionários + 1 EPI/tamanho + qtde) e gera a solicitação de compra em menos de 3 minutos.
- **SC-3**: Os totais (qtde total / valor total) atualizam em menos de 200ms ao mudar qualquer entrada do form, sem requerer botão de recalcular.
- **SC-4**: A solicitação de compra gerada contém os 3 campos obrigatórios (CCU, qtde total, valor total) em 100% dos casos onde o pedido tem vínculo de catálogo válido.
- **SC-5**: Auditoria por catálogo ("quantos pedidos usam o EPI X?") retorna resposta em uma única consulta.
- **SC-6**: Zero quebras nas demais telas após o deploy: folha, faturamento, exames, benefícios e tela atual de EPIs (feature 001) continuam funcionando.
- **SC-7**: 100% dos pedidos novos têm `quantidade_total` e `valor_total_compra` persistidos no banco (nunca NULL para pedidos não-legados).

## Key Entities

- **EPI Cadastrado** (novo): catálogo de produtos. Atributos: nome (único entre ativos), tamanhos (lista de strings; ex: ["P","M","G"] ou ["Único"]), valor por tamanho (map tamanho → valor), status (ativo/desativado), datas de criação/atualização.
- **Pedido de Compra** (evolução do "pacote" da feature 001): unidade de requisição de compra. Atributos novos em relação à 001: vínculo com EPI cadastrado (FK), tamanho selecionado, quantidade total persistida, valor total persistido. Atributos preservados: CCU, mês de competência, observação, funcionários (cartesiano se modelo multi-item da 001), documentos anexos.
- **Funcionário Vinculado** (preservado da feature 001): cada linha persistida vincula 1 funcionário a 1 EPI/tamanho dentro de um pedido. Snapshot de matrícula + nome.
- **Solicitação de Compra** (output, sem persistência própria obrigatória): documento gerado on-demand a partir de um pedido. Pode opcionalmente ter registro de "última geração" no pedido (histórico de envios).

## Assumptions

- A1: O catálogo de EPI é um cadastro **local** do sistema, sem integração com fornecedor / ERP externo nesta versão. Manutenção manual.
- A2: O valor unitário no pedido é um **snapshot** do valor do catálogo no momento do save (rastreabilidade contábil). Edição futura do catálogo não afeta pedidos já salvos.
- A3: A unicidade do nome do EPI considera apenas EPIs ativos. Pode existir um EPI desativado com mesmo nome de um novo EPI ativo (caso de "ressuscitar" um produto).
- A4: A semântica de quantidade no pedido segue Q2 da feature 001: replica por funcionário (cada um dos N funcionários recebe `quantidade_por_funcionario` unidades). Cada item da compra calcula seu próprio `quantidade_total` (funcionários × qtde_por_funcionario) e `valor_total` (qtde_total × valor_unitario).
- A5: A solicitação de compra é apenas um output; não dispara workflow de aprovação interna nesta versão (sem status "pendente/aprovado/rejeitado"). Cada save regenera a solicitação — não há histórico de versões anteriores.
- A6: O catálogo tem CRUD em tela separada (não inline na criação de pedido). Justificativa: cadastro é tarefa esporádica e separá-lo evita poluir o form principal. Atalho "+ Cadastrar EPI novo" pode existir como ponte da tela de pedido (abre o cadastro em modal).
- A7: EPIs cadastrados não têm associação com fornecedor, NCM, CA (Certificado de Aprovação), validade, ou foto nesta versão. Apenas nome, tamanhos e preços.
- A8: O "solicitante" registrado na solicitação é o usuário logado no momento do save (`session.user.full_name or email`). O campo é persistido no pedido como snapshot (não é FK para a tabela de users), permitindo a solicitação manter a referência mesmo se o user for desativado/renomeado depois.
- A9: O Excel da solicitação segue um layout único e padronizado (sem template personalizável nesta versão). Layout: cabeçalho com brasão/empresa, bloco de cabeçalho (CCU, competência, solicitante, data), tabela de itens, totais em destaque, e bloco opcional de funcionários atendidos.

## Out of Scope

- Workflow de aprovação de pedido (status pendente/aprovado/rejeitado por gestor).
- Histórico de versões do catálogo (rastrear mudanças de preço com timestamp).
- Integração com fornecedor / cotação automática.
- Importação em massa de catálogo via planilha.
- Múltiplos preços para o mesmo tamanho (ex: variação por fornecedor).
- Validade do CA, ficha de EPI individual, controle de estoque.
- Repasse automático para faturamento do cliente (continua fora do escopo, como na feature 001).

## Technical Decisions (já fechadas pré-plano)

Para evitar perguntas durante `/speckit-plan`, as escolhas técnicas abaixo já estão definidas. O plan vai detalhar como implementar cada uma.

| # | Decisão | Escolha |
|---|---|---|
| TD-1 | Schema do catálogo | **2 tabelas normalizadas**: `epi_catalog` (id, nome, ativo, created_at) + `epi_catalog_sizes` (id, epi_id FK CASCADE, tamanho, valor) |
| TD-2 | Tela do catálogo | **Rota separada** `/catalogo-epis` + novo link "Catálogo de EPIs" na nav global |
| TD-3 | Pedidos legados (001) | **Marcar como legado**: badge na listagem, geração de solicitação desabilitada com tooltip. Sem fluxo de migração inline |
| TD-4 | Destinatário do email | Variável `EPI_PURCHASE_EMAIL` no `.env` como default; campo editável no momento do envio |
| TD-5 | Snapshot do solicitante | `session.user.full_name` (ou `email` se nome vazio) capturado no save, persistido como string no pedido. Não é FK |
| TD-6 | Módulo Excel | Novo arquivo `app/services/epi_solicitation_excel.py` (separado de `excel_export.py`). Usa `openpyxl` já instalado |
| TD-7 | Estratégia de migração | Mesma da 001: colunas `NULL`able, `ALTER TABLE` documentado no `RUNBOOK.md` em "Migração 002" |
| TD-8 | Lib SMTP | Stdlib `smtplib` + `email.mime`. Sem dependência nova. Detecção de SMTP via presença de `SMTP_HOST` no `.env` |
| TD-9 | Unicidade do nome do EPI | Case-insensitive entre ativos (`UPPER(nome)` único). Soft-deletes podem ter colisão |

## Dependencies

- Feature 001 implementada e funcional (tela `/epis`, rotas `/api/epi-purchases`, helpers de Senior, migração 001 aplicada).
- Integração Senior SOAP — sem mudanças.
- Decisão sobre formato de solicitação (Q2) impacta dependências externas: PDF requer geração de PDF (lib local), Excel já tem geração no sistema (`excel_export.py`), Email requer SMTP configurado (verificar se já existe).

## Clarifications Resolved

- **Q1 — Valor unitário no pedido**: editável com aviso visível quando difere do catálogo (FR-8 atualizado). Catálogo não é mutado pelo override.
- **Q2 — Formato e canal da solicitação**: Excel para download (sempre) + envio por email se SMTP configurado (FR-16). Layout padronizado (A9).
- **Q3 — Relação pedido × itens**: modelo híbrido — uma compra com múltiplos itens (preserva 001), mas "Salvar Compra" e "Solicitar Compra" são a mesma ação: cada save gera/regenera a solicitação (FR-11, FR-11.1, FR-14). Solicitação tem cabeçalho (CCU, competência, solicitante) + linhas de itens (nome, tamanho, qtde total, valor total) + total geral.
