# Documento de Critérios de Conciliação Contábil

**Origem**: Plano de Execução (22/07/2026) — Etapa 3, tarefas 3.1 e 3.3.
**Feature**: [specs/004-relatorio-conciliacao](../specs/004-relatorio-conciliacao/spec.md)
**Status**: rascunho — pendente de preenchimento dos exemplos reais e aprovação.

## 1. Por que os totais divergem (a diferença de recorte)

O sistema soma a **competência inteira**: todos os códigos de cálculo (CODCAL) que a Senior processa no mês — folha mensal, adiantamentos, rescisões, férias, 13º, recálculos etc. O relatório **"mensal"** que a contabilidade recebe da Senior é um **recorte**: contém apenas parte desses cálculos.

Logo, é **esperado** que o total do sistema seja maior que o total do relatório mensal. A diferença não é erro — é o conjunto de cálculos que ficam **fora do recorte mensal**. A conciliação torna essa diferença explícita e auditável:

```
competência inteira  =  recorte mensal  +  fora do recorte
```

A ferramenta em `/conciliacao` classifica cada CODCAL como "entra no recorte mensal" ou "fora do recorte" e mostra a ponte fechando (resíduo R$ 0,00) quando todos os códigos estão classificados.

## 2. Como conferir (contabilidade)

1. Gere a conciliação da competência em `/conciliacao` (todos os CCUs, ou filtrando um).
2. Confira o **"recorte mensal"** contra o total do relatório mensal da Senior da mesma competência — devem coincidir.
3. Use a aba **Eventos** da planilha exportada para conferir evento a evento, se necessário.
4. O **"fora do recorte"** é a diferença legítima explicada pelos cálculos não-mensais.

> A conciliação usa apenas **totais e códigos de cálculo/evento** — nenhum dado pessoal de funcionário.

## 3. Classificação dos códigos de cálculo (CODCAL)

Preencher com os CODCAL reais da operação (o WS não fornece o nome — a descrição é cadastrada na tela):

| CODCAL | Descrição | Classificação | Observação |
|--------|-----------|---------------|------------|
| 362 | _(ex.: Folha mensal)_ | Recorte mensal | _preencher_ |
| _..._ | _..._ | _mensal / fora_ | _..._ |

## 4. Exemplos numéricos reais

> Preencher com **2 competências reais** já conferidas (apenas totais e CODCAL — sem nomes/CPF).

### Exemplo A — competência AAAA-MM

| Item | Valor |
|------|-------|
| Competência inteira | R$ _..._ |
| Recorte mensal | R$ _..._ |
| Fora do recorte | R$ _..._ |
| Resíduo | R$ 0,00 |

Decomposição do "fora do recorte" (por CODCAL): _..._

### Exemplo B — competência AAAA-MM

_(idem)_

## 5. Aprovação

| Nome | Função | Data | Assinatura/registro |
|------|--------|------|---------------------|
| _..._ | Contabilidade/Cliente | _..._ | _..._ |

## 6. Pendência junto à Senior — marcação de tipo de cálculo (TIPCAL)

A Senior ainda não expõe a marcação oficial de tipo de cálculo que eliminaria a divergência na origem. Enquanto isso, a classificação por CODCAL (cadastrada na tela) é a fonte da verdade. Quando o TIPCAL for exposto, a classificação passa a ser sincronizada com `origem = "oficial"` sem retrabalho.

**Follow-up (recorrência mensal):**

| Data | Situação | Responsável |
|------|----------|-------------|
| _..._ | _registrado/aguardando_ | _..._ |
