# Feature Specification: Relatório de Conciliação Contábil

**Feature ID**: 004-relatorio-conciliacao
**Created**: 2026-07-22
**Status**: Draft
**Spec File**: spec.md
**Origem**: Plano de Execução (22/07/2026) — Etapa 3, tarefas 3.1 e 3.2

## Overview

Hoje os totais do sistema (competência inteira, somando todos os cálculos da folha) divergem dos relatórios "mensais" da Senior usados pela contabilidade na conferência — uma diferença legítima de recorte, mas que só o Daniel sabe explicar. A cada fechamento, a confiança no número depende de uma explicação verbal.

Esta feature cria um **relatório de conciliação** que demonstra, com números, a ponte entre os dois recortes: total da competência inteira → (menos) cálculos fora do recorte mensal → total do recorte mensal. O objetivo de negócio é que a conferência mensal feche **de forma autônoma pela contabilidade**, usando apenas o relatório, sem intervenção do Daniel. Acompanha o relatório um **documento de conciliação** (tarefa 3.1) que formaliza a explicação do recorte com exemplos numéricos reais, para aprovação de quem confere.

## Clarifications

### Session 2026-07-22

- Q: Ao gerar a conciliação de uma competência, de onde vêm os números? → A: Consulta à Senior no momento da geração, reutilizando o padrão de tarefa em segundo plano das exportações (números sempre atuais; sem armazenamento novo de folha; resultado não é persistido).
- Q: Até onde desce o detalhamento da conciliação (drill-down do FR-2)? → A: Agregado por evento (valor total + quantidade de lançamentos por evento dentro de cada código de cálculo); sem dados por funcionário na tela ou na planilha.
- Q: A classificação de um código de cálculo vale globalmente ou por competência? → A: Global por codcal — uma classificação única aplicada a todas as competências; alterações auditadas.

## User Scenarios & Testing

### Primary Flow

1. Usuário com perfil gestor (ou superior) acessa a tela "Conciliação" e seleciona a competência (e, opcionalmente, um centro de custo).
2. O sistema monta a conciliação da competência: total da competência inteira, decomposição por código de cálculo (cada um classificado como "entra no recorte mensal" ou "fora do recorte"), total do recorte mensal resultante e a diferença explicada.
3. O usuário compara o "total do recorte mensal" do relatório com o total do relatório mensal da Senior que a contabilidade já recebe — os dois devem bater.
4. O usuário exporta a conciliação (planilha) e envia à contabilidade, que fecha a conferência sem precisar de explicações adicionais.
5. Caso apareça um código de cálculo ainda não classificado, o relatório o destaca como "não classificado" e um gestor o classifica na própria tela; a classificação fica registrada para as próximas competências.

### Acceptance Scenarios

- **Scenario 1**: Dado uma competência processada com todos os códigos de cálculo classificados, quando o gestor gera a conciliação, então o relatório mostra total da competência inteira, lista de cálculos fora do recorte com seus valores, total do recorte mensal, e a identidade "competência inteira − fora do recorte = recorte mensal" fecha em R$ 0,00 de resíduo.
- **Scenario 2**: Dado o relatório mensal da Senior de uma competência real, quando a contabilidade compara o total dele com o "total do recorte mensal" da conciliação, então os valores coincidem (evento a evento na visão detalhada), sem intervenção do Daniel.
- **Scenario 3**: Dado que a Senior processou um código de cálculo novo (ex.: um recálculo extraordinário), quando a conciliação é gerada, então o relatório sinaliza o código como "não classificado", exibe seu valor separadamente e indica que a conciliação está incompleta até a classificação.
- **Scenario 4**: Dado um usuário com perfil operador (sem permissão), quando tenta acessar a tela ou a exportação de conciliação, então o acesso é negado.
- **Scenario 5**: Dado uma competência filtrada por um centro de custo específico, quando o gestor gera a conciliação, então todos os totais e a decomposição refletem apenas aquele centro de custo.

### Edge Cases

- Código de cálculo novo/desconhecido na competência → destacado como "não classificado"; conciliação marcada como incompleta (nunca fecha "no silêncio").
- Competência sem dados (não processada ainda) → mensagem clara, sem relatório vazio enganoso.
- Valores negativos (estornos/descontos) → exibidos com sinal e somados corretamente na ponte.
- Fonte de dados da folha indisponível no momento da geração → erro claro com orientação de tentar novamente; nunca um relatório parcial sem aviso.
- Recálculo da competência depois de uma conciliação exportada → o relatório carrega data/hora de geração para deixar claro a que momento os números se referem.
- Funcionário presente em mais de um centro de custo na competência → valores atribuídos ao centro de custo de cada lançamento, sem duplicação no total geral.

## Functional Requirements

- **FR-1**: O sistema deve oferecer uma tela de conciliação por competência, com filtro opcional por centro de custo, exibindo: (a) total da competência inteira; (b) decomposição por código de cálculo com valor e classificação; (c) total do recorte mensal; (d) resíduo da ponte (deve ser zero quando tudo classificado).
- **FR-2**: A decomposição deve permitir detalhamento (drill-down) de cada código de cálculo por evento da folha, **agregado por evento** (valor total e quantidade de lançamentos), para conferência evento a evento. Nenhum dado individual de funcionário (nome, CPF, valores por pessoa) aparece na tela ou na planilha exportada.
- **FR-3**: O sistema deve manter uma classificação configurável de códigos de cálculo ("entra no recorte mensal" / "fora do recorte"), **global por código** (uma única classificação vale para todas as competências), editável por gestor+ na própria tela, persistida e auditada (quem alterou, quando, de → para). Um gestor+ também deve poder **remover** a classificação de um código, fazendo-o voltar ao estado "não classificado" (ex.: reverter um erro de classificação); a remoção é auditada como as demais alterações.
- **FR-4**: Para códigos de cálculo ainda não classificados, o sistema deve **sugerir** uma classificação por heurística (a partir dos eventos conhecidos do código), que o gestor+ confirma ou ajusta antes de gravar — nada é classificado automaticamente (ver FR-5/SC-3). Cada classificação registra sua **origem** (`heuristica`, quando gravada a partir de uma sugestão aceita; `manual`, quando digitada do zero; `oficial`, reservada à marcação de tipo de cálculo da Senior quando exposta), permitindo a substituição futura sem retrabalho para o usuário.
- **FR-5**: Códigos de cálculo não classificados devem ser destacados visualmente e impedir que a conciliação seja apresentada como "fechada"; o status da conciliação deve ser explícito, com três estados: **fechada** (`fechada`), **incompleta** (`incompleta`, quando há código não classificado) e **com resíduo** (`com_residuo`, quando a ponte não fecha em zero apesar de tudo classificado).
- **FR-6**: O relatório deve ser exportável em planilha com as mesmas informações da tela (resumo + decomposição + detalhamento), incluindo competência, filtro aplicado e data/hora de geração.
- **FR-7**: O acesso à tela e à exportação deve ser restrito a gestor e admin; toda geração/exportação deve ser registrada na trilha de auditoria.
- **FR-8**: Deve ser produzido o **documento de conciliação** (tarefa 3.1): explicação da diferença de recorte com ao menos 2 exemplos numéricos de competências reais (valores reais, sem dados pessoais de funcionários — apenas totais e códigos de cálculo), versionado no repositório e submetido à aprovação de quem confere (contabilidade/cliente).
- **FR-9**: A pendência junto à Senior (exposição da marcação de tipo de cálculo) deve ficar registrada no documento de conciliação com a data do último follow-up (tarefa 3.3 do plano, recorrência mensal).
- **FR-10**: A geração da conciliação consulta a integração da folha no momento do pedido e executa em segundo plano com indicação de progresso (mesmo padrão das exportações), respeitando o limite de tempo do SC-4; o resultado não é persistido — a planilha exportada é o registro da conferência.

## Success Criteria

- **SC-1**: A conferência de um ciclo real fecha usando apenas o relatório, sem intervenção do Daniel (critério de conclusão do plano) — validado com a contabilidade em uma competência real.
- **SC-2**: Para uma competência com classificação completa, o resíduo da ponte é R$ 0,00 e o total do recorte mensal coincide com o relatório mensal da Senior da mesma competência.
- **SC-3**: Um código de cálculo novo nunca passa despercebido: aparece como "não classificado" em 100% dos casos e a conciliação não é apresentada como fechada até ser classificado.
- **SC-4**: A conciliação de uma competência completa é gerada e exibida em menos de 2 minutos, mesmo com todos os centros de custo.
- **SC-5**: O documento de conciliação é aprovado formalmente por quem confere (contabilidade/cliente) — registro da aprovação anexado ao documento.

## Key Entities

- **Classificação de código de cálculo**: código de cálculo da folha, rótulo/descrição, classificação (recorte mensal / fora do recorte / não classificado), origem da classificação (heurística, manual, oficial-Senior), autor e data da última alteração.
- **Conciliação (visão gerada)**: competência, filtro de centro de custo, data/hora de geração, total da competência inteira, total do recorte mensal, resíduo, status (fechada / incompleta / com resíduo), linhas de decomposição por código de cálculo e detalhamento por evento.

## Assumptions

- "Contabilidade" não é um papel próprio no sistema hoje (papéis: operador, gestor, admin). Assumimos que a conferência é feita a partir da **planilha exportada** enviada pela equipe, e que usuários internos com papel gestor+ acessam a tela. Criar um papel/acesso "contabilidade" fica fora deste escopo.
- A ponte é calculada **apenas com os dados que já chegam pela integração da folha** (cobertura já validada: todos os eventos do relatório mensal chegam pela integração). Não há upload do relatório mensal da Senior para comparação automática dentro do sistema — a comparação final é feita pela contabilidade contra o relatório que ela já recebe.
- A classificação por código de cálculo é suficiente para explicar a diferença de recorte (conhecimento já levantado: o recorte mensal corresponde a um subconjunto dos ~10 códigos de cálculo da competência). Se surgirem diferenças dentro de um mesmo código, o detalhamento por evento (FR-2) é o instrumento de investigação.
- O documento de conciliação usa apenas totais agregados e códigos/nomes de cálculo — nenhum dado pessoal de funcionário (conformidade com a política de dados da organização).
- Enquanto a Senior não expõe a marcação oficial de tipo de cálculo, a classificação manual/heurística é a fonte da verdade e é assumida como estável entre competências.

## Out of Scope

- Upload/importação do relatório mensal da Senior para "bater" automaticamente dentro do sistema (possível evolução futura).
- Conciliação com a fatura emitida ao cliente (isso é conferência de faturamento, não de folha).
- Papel de acesso próprio para a contabilidade ou acesso externo ao sistema.
- A eliminação da divergência na origem (depende da Senior expor o tipo de cálculo — tarefa 3.3 é só acompanhamento/registro).

## Dependencies

- Integração da folha (WS Senior) operante para a competência consultada — fonte única dos números.
- Conhecimento levantado sobre o recorte (competência inteira × relatório mensal) para semear a classificação heurística inicial.
- Trilha de auditoria existente (registro de alterações de classificação e exportações).
- Aprovação do documento de conciliação por quem confere (contabilidade/cliente) — dependência externa para o SC-5.
