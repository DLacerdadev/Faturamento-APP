# SQL - Busca de Folha de Pagamento (Senior)

## Query Principal

```sql
SELECT
    R034FUN.NUMCAD,
    R034FUN.NOMFUN,
    R034FUN.NUMCPF,
    R034FUN.DATADM,
    R034FUN.CODCCU,
    R018CCU.NOMCCU,
    R034FUN.DATAFA,
    R034FUN.VALSAL,
    R034FUN.SITAFA,
    R010SIT.DESSIT,
    R024CAR.TITRED,
    R044CAL.PERREF,
    R046VER.CODCAL,
    R046VER.CODEVE,
    R008EVC.DESEVE,
    R008EVC.NATEVE,
    R008EVC.TIPEVE,
    R046VER.REFEVE,
    R046VER.VALEVE
FROM
    [{db}].dbo.R034FUN
JOIN
    [{db}].dbo.R046VER ON
        R034FUN.NUMEMP = R046VER.NUMEMP AND
        R034FUN.TIPCOL = R046VER.TIPCOL AND
        R034FUN.NUMCAD = R046VER.NUMCAD
JOIN
    [{db}].dbo.R024CAR ON
        R034FUN.ESTCAR = R024CAR.ESTCAR AND
        R034FUN.CODCAR = R024CAR.CODCAR
JOIN
    [{db}].dbo.R010SIT ON
        R034FUN.SITAFA = R010SIT.CODSIT
JOIN
    [{db}].dbo.R008EVC ON
        R046VER.TABEVE = R008EVC.CODTAB AND
        R046VER.CODEVE = R008EVC.CODEVE
JOIN
    [{db}].dbo.R044CAL ON
        R046VER.CODCAL = R044CAL.CODCAL
LEFT JOIN
    [{db}].dbo.R018CCU ON
        R034FUN.CODCCU = R018CCU.CODCCU
WHERE
    '{periodo}' BETWEEN R044CAL.INICMP AND R044CAL.FIMCMP
    AND R034FUN.NUMEMP = {numemp}
    AND R034FUN.CODCCU IN ('{codccu1}', '{codccu2}', ...)  -- (opcional, usa = para um único CCU ou IN para múltiplos)
ORDER BY
    R034FUN.NOMFUN, R046VER.REFEVE
```

---

## Tabelas Envolvidas

| Tabela | Descrição | Papel na Query |
|--------|-----------|----------------|
| **R034FUN** | Cadastro de Funcionários | Tabela principal - dados do funcionário (matrícula, nome, CPF, salário, situação, centro de custo, data admissão/afastamento) |
| **R046VER** | Verbas/Eventos da Folha | Lançamentos da folha - código do evento, valor (`VALEVE`), referência (`REFEVE`), código do cálculo |
| **R024CAR** | Cargos | Título reduzido do cargo (`TITRED`) |
| **R010SIT** | Situações de Afastamento | Descrição da situação (`DESSIT`) - ex: Ativo, Afastado, Demitido |
| **R008EVC** | Eventos de Cálculo | Descrição do evento (`DESEVE`), natureza (`NATEVE`), tipo (`TIPEVE`) |
| **R044CAL** | Cálculos / Competências | Período de referência (`PERREF`), início (`INICMP`) e fim (`FIMCMP`) da competência |
| **R018CCU** | Centros de Custo | Nome do centro de custo (`NOMCCU`) - LEFT JOIN pois nem todo funcionário tem CCU |

---

## JOINs

| JOIN | Condição | Observação |
|------|----------|------------|
| R034FUN → R046VER | `NUMEMP`, `TIPCOL`, `NUMCAD` | Vincula funcionário aos seus eventos/verbas |
| R034FUN → R024CAR | `ESTCAR`, `CODCAR` | Busca o cargo do funcionário |
| R034FUN → R010SIT | `SITAFA = CODSIT` | Busca descrição da situação de afastamento |
| R046VER → R008EVC | `TABEVE = CODTAB`, `CODEVE` | Busca descrição e tipo do evento |
| R046VER → R044CAL | `CODCAL` | Vincula verba ao cálculo/competência |
| R034FUN → R018CCU | `CODCCU` (LEFT JOIN) | Busca nome do centro de custo |

---

## Parâmetros / Filtros

| Parâmetro | Tipo | Descrição | Exemplo |
|-----------|------|-----------|---------|
| `{periodo}` | `VARCHAR` (YYYY-MM-DD) | Data de referência que deve estar entre `INICMP` e `FIMCMP` do cálculo | `'2026-01-15'` |
| `{numemp}` | `INT` | Número da empresa no Senior (TELOS = 6) | `6` |
| `{codccu}` | `VARCHAR` ou `LIST` (opcional) | Código(s) do centro de custo. Aceita um valor (`=`) ou múltiplos (`IN`) | `'01.001'` ou `['01.001', '02.002']` |
| `{db}` | `VARCHAR` | Nome do banco MSSQL (variável de ambiente `MSSQL_DB`) | `opus_hcm_221123` |

---

## Campos Retornados

| Campo | Tabela | Descrição |
|-------|--------|-----------|
| `NUMCAD` | R034FUN | Matrícula do funcionário |
| `NOMFUN` | R034FUN | Nome do funcionário |
| `NUMCPF` | R034FUN | CPF do funcionário |
| `DATADM` | R034FUN | Data de admissão |
| `CODCCU` | R034FUN | Código do centro de custo |
| `NOMCCU` | R018CCU | Nome do centro de custo |
| `DATAFA` | R034FUN | Data de afastamento |
| `VALSAL` | R034FUN | Valor do salário |
| `SITAFA` | R034FUN | Código da situação de afastamento |
| `DESSIT` | R010SIT | Descrição da situação (ex: Ativo, Férias) |
| `TITRED` | R024CAR | Título reduzido do cargo |
| `PERREF` | R044CAL | Período de referência do cálculo |
| `CODCAL` | R046VER | Código do cálculo |
| `CODEVE` | R046VER | Código do evento/verba |
| `DESEVE` | R008EVC | Descrição do evento (ex: Salário Mensal, Hora Extra) |
| `NATEVE` | R008EVC | Natureza do evento |
| `TIPEVE` | R008EVC | Tipo do evento |
| `REFEVE` | R046VER | Referência do evento (quantidade, horas, etc.) |
| `VALEVE` | R046VER | Valor do evento |

---

## Execução

A query **não** é executada diretamente no banco MSSQL. Ela é enviada via **API Senior** no endpoint:

```
POST {DOMAIN_API}/query
```

**Headers:**
```json
{
    "x-api-key": "{API_KEY}",
    "Content-Type": "application/json"
}
```

**Body:**
```json
{
    "sqlText": "<SQL acima>"
}
```

**Variáveis de ambiente necessárias:**
- `DOMAIN_API` — URL base da API Senior
- `API_KEY` — Chave de autenticação da API
- `MSSQL_DB` — Nome do banco (padrão: `opus_hcm_221123`)

---

## Arquivo Fonte

`app/services/senior_connector.py` — função `fetch_payroll()`
