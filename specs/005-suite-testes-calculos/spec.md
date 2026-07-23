# Feature Specification: Suíte de Testes dos Cálculos Críticos

**Feature ID**: 005-suite-testes-calculos
**Created**: 2026-07-23
**Status**: Draft
**Spec File**: spec.md
**Origem**: Plano de Execução (22/07/2026) — Etapa 4, tarefas 4.1, 4.2 e 4.3

## Overview

O sistema calcula dinheiro — encargos sociais, taxa administrativa, impostos e o gross-up da alíquota, além do valor total do faturamento por colaborador e a soma dos colaboradores. Hoje a única garantia de que uma mudança não quebrou uma conta é a validação manual. Como novos modelos de cliente entram com frequência, cada alteração é um risco silencioso.

Esta feature cria uma **rede de segurança automatizada**: uma suíte de casos de teste tabelados (com valores esperados conferidos), que roda com **um único comando** e fica verde quando os cálculos estão corretos. A partir da sua conclusão, vale a regra de governança do plano: **deploy só com a suíte verde**. A suíte cobre os cálculos puros (unidade), pelo menos um fluxo ponta a ponta por modelo de cliente, e a importação de modelo por planilha (reconhecimento de colunas e preservação de fórmulas). Roda offline, sem depender da Senior, e sem qualquer dado pessoal de funcionário.

## User Scenarios & Testing

### Primary Flow

1. Um desenvolvedor (ou o processo de deploy) executa a suíte com um único comando.
2. A suíte roda todos os casos tabelados de cálculo, os fluxos ponta a ponta por modelo e o caso de importação de planilha — sem acessar a Senior.
3. Se todos os valores calculados batem com os valores esperados conferidos, a suíte termina **verde** (sucesso) e o deploy é liberado.
4. Se algum cálculo diverge, a suíte termina **vermelha**, apontando qual caso falhou, o valor esperado e o valor obtido — e o deploy é bloqueado até a correção.

### Acceptance Scenarios

- **Scenario 1**: Dado um conjunto de casos tabelados de encargos/taxa administrativa/imposto/gross-up com valores esperados conferidos, quando a suíte roda, então cada cálculo produz exatamente o valor esperado (dentro da tolerância de centavos definida) e a suíte fica verde.
- **Scenario 2**: Dado um caso de borda de dissídio retroativo (diferença de salário lançada na competência), quando o faturamento é calculado, então o valor total reflete o dissídio conforme o valor esperado tabelado.
- **Scenario 3**: Dado um caso de borda de admissão no meio do mês (salário proporcional aos dias trabalhados), quando o faturamento é calculado, então o valor por colaborador bate com o esperado.
- **Scenario 4**: Dado um conjunto de dados sintéticos de folha para cada modelo de cliente (FEMSA, Skyrail, Geral Total), quando a exportação é gerada ponta a ponta, então a planilha resultante contém as colunas e os totais esperados daquele modelo.
- **Scenario 5**: Dada uma planilha de referência de modelo de cliente, quando ela é importada e usada para gerar um export, então as colunas são reconhecidas corretamente e as fórmulas do modelo são preservadas no resultado.
- **Scenario 6**: Dado que um cálculo foi alterado de forma que muda um resultado, quando a suíte roda, então ela falha e identifica o caso divergente — a mudança não passa despercebida.
- **Scenario 7**: Dado um ambiente sem acesso à Senior (offline), quando a suíte roda, então ela completa normalmente usando apenas fixtures locais.

### Edge Cases

- Dissídio retroativo (diferença de salário) na competência.
- Admissão no meio do mês (proporcionalidade de dias).
- Afastamento/rescisão no período (verbas que entram ou não na base).
- Alíquota de gross-up igual a zero (não pode dividir por zero nem inflar o valor).
- Percentuais de encargos/taxa/imposto no limite (0% e valores altos) sem estourar.
- Colaborador com remuneração base zero (não deve gerar encargo negativo ou erro).
- Planilha de importação com coluna ausente, renomeada ou fora de ordem.
- Valores negativos (estornos/descontos) somados com o sinal correto.

## Functional Requirements

- **FR-1**: A suíte deve rodar com **um único comando** e reportar um resultado binário claro (verde/vermelho), listando os casos que falharam com valor esperado × obtido.
- **FR-2**: Deve existir um conjunto de **casos tabelados** dos cálculos financeiros — encargos sociais, taxa administrativa, imposto e gross-up, e o valor total por colaborador e somado — cada um com valor esperado conferido e documentado.
- **FR-3**: Os casos tabelados devem incluir os **casos de borda** identificados (no mínimo dissídio retroativo e admissão no meio do mês), com seus valores esperados.
- **FR-4**: A suíte deve conter **testes de unidade** dos cálculos puros (funções de cálculo isoladas), independentes de banco ou rede.
- **FR-5**: A suíte deve conter **ao menos um fluxo ponta a ponta por modelo de cliente** (FEMSA, Skyrail, Geral Total), da folha sintética até a planilha/estrutura exportada, validando colunas e totais.
- **FR-6**: A suíte deve cobrir a **importação de modelo por planilha**: importar uma planilha de referência, validar o reconhecimento de colunas e a preservação de fórmulas, e validar o export gerado a partir dela.
- **FR-7**: A suíte deve rodar **offline**, sem depender do WS Senior, usando dados locais/fixtures (modo de desenvolvimento ou equivalente).
- **FR-8**: Os dados usados nos testes devem ser **sintéticos/anonimizados** — nenhum nome, CPF ou dado pessoal real de funcionário nos fixtures.
- **FR-9**: A suíte deve ser **executável de forma automatizada** (adequada a um passo de deploy/CI futuro), retornando código de sucesso/falha que permita bloquear o deploy.
- **FR-10**: Cada caso de teste deve ser **determinístico** e **isolado** — o resultado não depende da ordem de execução nem de estado deixado por outro caso.
- **FR-11**: A suíte deve poder incluir, como caso, a verificação da **ponte de conciliação** (feature 004) sobre dados sintéticos (competência inteira = recorte mensal + fora do recorte; resíduo zero quando tudo classificado).

## Success Criteria

- **SC-1**: A suíte roda com um único comando e fica verde num ambiente limpo, offline, em menos de 2 minutos.
- **SC-2**: Todo modelo de cliente ativo (FEMSA, Skyrail, Geral Total) tem ao menos um caso ponta a ponta coberto.
- **SC-3**: Os cálculos financeiros (encargos, taxa administrativa, imposto, gross-up, total) têm casos tabelados com valores conferidos, incluindo os casos de borda de dissídio retroativo e admissão no meio do mês.
- **SC-4**: Uma alteração que muda qualquer resultado financeiro coberto faz a suíte falhar e identifica o caso — verificado introduzindo uma mudança proposital.
- **SC-5**: A partir da conclusão desta feature, nenhum deploy sobe sem a suíte verde (regra de governança registrada e adotada).
- **SC-6**: A suíte não contém nenhum dado pessoal real de funcionário (verificável por inspeção dos fixtures).

## Key Entities

- **Caso de teste tabelado**: identificação, entradas (dados sintéticos de folha/percentuais/alíquota), valor(es) esperado(s) conferido(s), tolerância aceitável, e a que cálculo/modelo pertence.
- **Fixture de folha sintética**: conjunto de lançamentos de folha anonimizados (colaboradores fictícios, eventos, valores) por competência/modelo, usado pelos fluxos ponta a ponta.
- **Planilha de referência de modelo**: arquivo de exemplo (sem dados pessoais) usado para exercitar a importação de modelo e a preservação de fórmulas.

## Assumptions

- "Rodar com um único comando" será um comando padrão de execução de testes do ecossistema Python (a decisão de framework é técnica; o negócio só exige comando único, verde/vermelho e bloqueio de deploy).
- Os fluxos ponta a ponta usam o modo de desenvolvimento/fixtures já existente para não depender da Senior (o sistema já tem fallback local para a folha).
- A tolerância de comparação de valores monetários é de centavos (arredondamento a 2 casas), coerente com o resto do sistema.
- A integração com um servidor de CI não faz parte desta entrega — a suíte apenas precisa ser automatizável e retornar sucesso/falha; ligar num CI é passo posterior.
- Os modelos de cliente cobertos são os ativos hoje (FEMSA, Skyrail, Geral Total); novos modelos entram com seu próprio caso quando criados.

## Out of Scope

- Montar/operar um servidor de integração contínua (CI) — a suíte só precisa ser plugável.
- Testes de carga/desempenho e testes de interface (telas) — o foco é a correção dos cálculos e da exportação.
- Cobertura de 100% do código — o alvo é a correção dos cálculos financeiros e dos fluxos de exportação/importação, não uma métrica de cobertura total.
- Refatorar os cálculos existentes — a suíte documenta e protege o comportamento atual; correções de bugs eventualmente descobertos são tratadas à parte.

## Dependencies

- Cálculos existentes em produção (encargos, taxa administrativa, imposto, gross-up, total) como comportamento de referência.
- Modo de desenvolvimento/fixtures local para rodar a folha sem a Senior.
- Modelos de cliente e o mecanismo de importação de modelo por planilha (features anteriores) como alvo dos fluxos ponta a ponta.
- Valores esperados conferidos com quem valida hoje manualmente (para tabelar os casos com confiança).
