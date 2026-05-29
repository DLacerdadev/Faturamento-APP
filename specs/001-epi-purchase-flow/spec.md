# Feature Specification: Fluxo de Compra de EPIs por Funcionário

**Feature ID**: 001-epi-purchase-flow
**Created**: 2026-05-28
**Status**: Ready for `/speckit-plan`
**Spec File**: spec.md

## Overview

Refatorar o cadastro de compras de EPIs (Equipamentos de Proteção Individual) para que cada compra seja registrada **vinculada a funcionários específicos de um centro de custo**, e não apenas como um pacote agregado por empresa/mês.

Hoje a compra de EPI é um pacote agregado por empresa e mês de referência, com itens soltos (descrição/qtde/valor) sem rastreabilidade de quem recebeu o quê. O modelo atual impede responder perguntas operacionais básicas — quem recebeu o capacete X? quais EPIs o funcionário Y recebeu este ano? — e dificulta repasse para o cliente final, conferência de NF e atendimento a fiscalização trabalhista (CA, ficha de EPI).

A nova proposta substitui o cadastro atual por um fluxo guiado: o usuário escolhe um centro de custo, seleciona os funcionários ativos daquele centro que receberão EPIs e adiciona os itens da compra. Ao salvar, o sistema gera o **produto cartesiano** entre funcionários selecionados e itens — uma linha por par (funcionário × item) — preservando rastreabilidade individual sem exigir lançamento manual repetitivo.

## User Scenarios & Testing

### Primary Flow

1. O usuário acessa a tela de "Nova Compra de EPI".
2. O usuário seleciona um centro de custo (seleção única).
3. O sistema busca e exibe a lista de funcionários ativos daquele centro de custo num componente multi-select.
4. O usuário marca um ou mais funcionários (mínimo 1).
5. O usuário adiciona um ou mais itens à compra (mínimo 1), cada item com descrição, quantidade, valor unitário e total (campos do modelo atual).
6. O usuário, opcionalmente, anexa documentos (NF, comprovante) ao pacote.
7. O usuário clica em "Salvar".
8. O sistema persiste a compra criando uma linha por par funcionário × item; cada linha mantém os atributos do item original (descrição, quantidade, valor) e o vínculo com o funcionário.
9. O sistema confirma o sucesso e exibe o resumo: total de linhas geradas, funcionários envolvidos, valor total da compra.

### Acceptance Scenarios

- **Scenario 1 — Cartesiano básico**: Dado que o usuário selecionou 5 funcionários e adicionou 2 itens, quando salva a compra, então 10 linhas são persistidas (5 × 2), cada uma vinculada a um par funcionário × item.
- **Scenario 2 — Lista de funcionários filtrada por CCU**: Dado que o usuário escolhe o centro de custo `620039`, quando a lista de funcionários carrega, então apenas funcionários ativos com `codccu = 620039` aparecem.
- **Scenario 3 — Funcionário único, múltiplos itens**: Dado 1 funcionário selecionado e 3 itens, quando salva, então 3 linhas são geradas, todas vinculadas ao mesmo funcionário.
- **Scenario 4 — Funcionários sem itens**: Dado funcionários selecionados mas nenhum item adicionado, quando o usuário tenta salvar, então o sistema bloqueia o salvamento com mensagem clara.
- **Scenario 5 — Itens sem funcionários**: Dado itens adicionados mas nenhum funcionário selecionado, quando o usuário tenta salvar, então o sistema bloqueia o salvamento com mensagem clara.
- **Scenario 6 — Listagem após salvar**: Dado que uma compra foi salva, quando o usuário acessa a listagem de compras de EPI, então o pacote aparece com totalizadores corretos (funcionários, itens, linhas, valor) e permite drill-down até as linhas individuais funcionário × item.
- **Scenario 7 — Edição de compra existente**: Dado um pacote salvo, quando o usuário abre a compra para edição, então o sistema exibe os funcionários e itens originais e permite alterar/remover; ao salvar a edição, o cartesiano é recalculado.

### Edge Cases

- Centro de custo sem funcionários ativos no momento da consulta: o multi-select deve exibir um aviso "Nenhum funcionário ativo neste centro de custo".
- Funcionário foi demitido entre a abertura da tela e o "Salvar": o sistema revalida em tempo real no backend; se algum selecionado já não está ativo, **bloqueia o salvamento** e devolve a lista dos afetados para o usuário decidir (ver FR-13).
- Mesmo funcionário recebendo o mesmo item duas vezes (compras em datas diferentes): permitido, são linhas independentes.
- Item com quantidade=0 ou valor=0: bloquear no front com validação.
- Integração Senior indisponível ao carregar a lista de funcionários: exibir mensagem de erro clara, manter o que já foi preenchido na tela.

## Functional Requirements

- **FR-1**: O sistema deve oferecer uma tela única para criar uma compra de EPI seguindo a ordem: centro de custo → funcionários → itens → documentos (opcional) → salvar.
- **FR-2**: A lista de centros de custo deve vir da integração com o ERP de RH (Senior), usando o mesmo endpoint já utilizado pelas demais telas do sistema.
- **FR-3**: Ao escolher um centro de custo, o sistema deve buscar e exibir a lista de funcionários **ativos na data de hoje** daquele centro. Considera-se ativo: funcionário sem `data_afastamento` (sentinel `31/12/1900` tratado como sem afastamento) **ou** com `data_afastamento` futura (posterior à data corrente). Funcionários com `data_afastamento` no passado ou igual à data de hoje são excluídos.
- **FR-4**: A seleção de funcionários deve ser múltipla, com busca textual por nome ou matrícula dentro do componente.
- **FR-5**: A adição de itens deve permitir múltiplos itens, cada um com descrição (texto livre), quantidade (inteiro positivo) e valor unitário (decimal positivo). O valor total do item é calculado automaticamente.
- **FR-6**: Ao salvar, o sistema deve gerar **uma linha persistida por par (funcionário selecionado × item adicionado)**, isto é, |funcionários| × |itens| linhas.
- **FR-7**: Cada linha persistida deve conter: referência ao pacote da compra, referência ao funcionário (com snapshot de matrícula + nome), referência ao item original (ou cópia inline dos atributos do item — decisão de implementação), valor unitário e quantidade **replicados conforme o item** (sem divisão entre funcionários). Exemplo: item "Capacete, qtde=2, valor=R$ 50" com 5 funcionários selecionados gera 5 linhas, cada uma com `qtde=2`, `valor_unitario=R$ 50`, `valor_total=R$ 100`; valor total da compra = R$ 500.
- **FR-8**: O sistema deve permitir anexar e baixar documentos (NF, comprovantes) ao pacote da compra, mantendo a mesma capacidade do fluxo atual.
- **FR-9**: O sistema deve permitir listar, abrir, editar e excluir compras de EPI já criadas.
- **FR-10**: A tela deve seguir o mesmo design system das demais telas do sistema (visual, componentes, tipografia, paleta).
- **FR-11**: O sistema deve validar antes de salvar: pelo menos 1 funcionário selecionado, pelo menos 1 item adicionado, e cada item com quantidade ≥ 1 e valor unitário > 0.
- **FR-12**: Em caso de falha de comunicação com o Senior ao carregar centros de custo ou funcionários, o sistema deve exibir uma mensagem de erro acionável (com possibilidade de retry) e não silenciar o erro com lista vazia.
- **FR-13**: Ao salvar a compra, o backend deve revalidar a situação dos funcionários selecionados consultando o Senior em tempo real. Se algum funcionário selecionado já não atende ao critério de "ativo na data de hoje" (FR-3), o sistema deve **abortar o salvamento**, retornar a lista dos funcionários afetados ao usuário, e exigir uma decisão explícita (remover/manter) antes de tentar novamente.

## Success Criteria

- **SC-1**: Um usuário consegue criar uma compra com 10 funcionários e 3 itens (30 linhas) em menos de 2 minutos a partir do clique em "Nova compra".
- **SC-2**: 100% das compras criadas pelo novo fluxo têm rastreabilidade funcionário × item registrada no banco (zero linhas órfãs sem `employee_id`).
- **SC-3**: A listagem de funcionários ativos de um centro de custo exibe resultado em até 3 segundos no caminho feliz.
- **SC-4**: Auditoria por funcionário ("o que o colaborador X recebeu nos últimos 12 meses?") retorna resposta em uma única consulta, sem necessidade de cruzar planilhas externas.
- **SC-5**: Auditoria por centro de custo / item ("quantos capacetes foram entregues no CCU 620039 este mês?") retorna resposta em uma única consulta.
- **SC-6**: Zero quebras nas demais telas do sistema (folha, faturamento, exames, benefícios) após o deploy da nova feature.

## Key Entities

- **Pacote de Compra de EPI**: representa uma compra única feita para um centro de custo. Atributos: centro de custo, data/mês de referência, observação livre, documentos anexos. Hoje tem campos `empresa`, `mes_ano`, `observacao`; a nova proposta adiciona `codccu` ao pacote para preservar o contexto da compra.
- **Item de Compra**: descrição do EPI comprado (capacete, luva, etc.), quantidade entregue por funcionário, valor unitário, valor total. Atributos atuais do modelo se preservam; o vínculo passa a ser por par (item × funcionário) em vez de item solto no pacote.
- **Funcionário Vinculado**: cada linha persistida representa "funcionário F recebeu N unidades do item I dentro do pacote P". Atributos: pacote, funcionário (matrícula + nome para snapshot), item (descrição + quantidade + valor unitário), valor total da linha.
- **Documento Anexo**: NF, comprovante de entrega, foto da ficha de EPI assinada. Atributos preservados do modelo atual: pacote, nome original, nome armazenado, data de upload.

## Assumptions

- A1: O conceito de "centro de custo" continua sendo a mesma definição usada pelas demais telas — fonte da verdade é o ERP Senior. O conceito de "funcionário ativo" para esta feature, contudo, é mais simples que o usado pela folha: ativo = sem afastamento ou com afastamento futuro, na data corrente (ver FR-3). Não usa o cutoff de 2 meses da folha.
- A2: A quantidade e o valor unitário lançados no item representam o que **cada funcionário individualmente** recebeu daquele item; o cartesiano replica os mesmos valores para cada par funcionário × item. Exemplo: "Capacete, qtde=1, valor=R$ 50" com 5 funcionários selecionados gera 5 linhas, cada uma com `qtde=1` e `valor_unitario=R$ 50`. Valor total da compra = R$ 250.
- A3: O snapshot do funcionário (matrícula + nome) é persistido na linha no momento do salvamento, para que a compra continue consultável mesmo se o funcionário for desligado depois.
- A4: A edição de uma compra recalcula o cartesiano se a lista de funcionários ou itens muda; histórico antigo é substituído (não há versionamento de compras nesta versão).
- A5: Não há controle de estoque, devolução, troca ou validade de CA — escopo é apenas o registro do que foi comprado e para quem.
- A6: A tela atual de EPIs é apenas API REST (sem template HTML existente, conforme exploração do código). A nova interface será criada do zero, seguindo o design system das demais telas (`billing.html`, `customers.html`).

## Out of Scope

- Controle de estoque de EPIs (entrada/saída/saldo).
- Workflow de aprovação de compra (compradores, aprovadores, status).
- Integração com fornecedores ou OCR de nota fiscal.
- Geração automática de ficha de EPI individual (formulário PDF assinável).
- Alerta de validade do CA (Certificado de Aprovação) por item.
- Importação em massa via planilha.
- Repasse automático para faturamento do cliente final (pode ser feito numa próxima feature).

## Dependencies

- **Senior ERP — SOAP `T018CCU`**: lista de centros de custo (já integrado em `app/services/senior_connector.py`).
- **Senior ERP — SOAP `consultaRegistros`**: lista de funcionários ativos por centro de custo (já integrado; pode precisar de um wrapper REST adicional para filtrar por `codccu` e devolver no formato esperado pelo front).
- **Regra de "ativo" da Folha Senior**: definida em `app/services/excel_export.py` (cutoff de 2 meses sobre `data_afastamento`). Esta regra é referenciada por A1 e FR-3 e precisa ser explicitamente confirmada (ver Q1).
- **Migração de esquema**: a tabela `epi_purchase_items` atual não tem vínculo com funcionário (confirmado na exploração). A nova feature requer um campo ou tabela adicional para o vínculo funcionário × item. Detalhe de implementação a ser tratado em `/speckit-plan`.

## Clarifications Resolved

- **Q1 — Critério exato de "funcionário ativo"**: ativo na data de hoje (sem afastamento ou afastamento futuro). Incorporado em FR-3 e A1.
- **Q2 — Semântica da quantidade do item no cartesiano**: quantidade por funcionário (replica). Incorporado em FR-7 e A2.
- **Q3 — Tratamento de funcionário demitido entre tela e salvamento**: revalidar no backend e bloquear se algum não estiver mais ativo. Incorporado em FR-13 e nas edge cases.
