# Referência de Integração — TOTVS Protheus via WSGETDATA (REST → SQL)

**Spec**: 008-epi-auto-totvs
**Status**: Draft (referência técnica para o Agente C)

> ⚠️ **Segredos NÃO ficam neste arquivo.** URL, usuário e senha são lidos de variáveis de
> ambiente (`.env`, fora do git). Nada de credencial em código ou em arquivo versionado.

## 1. O que é

O Protheus expõe um web service REST **`WSGETDATA`** que funciona como um **gateway de SQL**:
recebe um comando `SELECT` no corpo da requisição (texto puro) e devolve o resultado em **JSON**.
Isso nos dá leitura direta de qualquer tabela do Protheus que a conta tenha permissão — o que
resolve a origem do dado de EPI (antes era upload manual de Excel).

Descoberto a partir de uma consulta Power BI existente do grupo (fonte confiável interna).

## 2. Contrato HTTP

- **Método**: `POST`
- **URL base**: `TOTVS_WSGETDATA_URL` (env) — ex.: `http://<host>:<porta>`
- **Path**: `/rest/WSGETDATA/v1`
- **Headers**:
  - `Authorization: Basic <base64(user:senha)>` — construído em runtime a partir de
    `TOTVS_WSGETDATA_USER` / `TOTVS_WSGETDATA_PASSWORD` (env). **Não** guardar o base64 pronto.
  - `Content-Type: text/plain`
- **Body**: o comando `SELECT` (texto puro). Dialeto **MS SQL Server** (base de produção
  `CY1A7F_..._PR_PD`); `TOP`, `ROW_NUMBER() OVER(...)`, `INFORMATION_SCHEMA` disponíveis.
- **Resposta** (✅ confirmada em teste real, 2026-07-25): sucesso vem como **HTTP 201** com envelope
  `{"registros": [ {coluna: valor}, ... ], "qtde": N, "dicionario": []}`. Erro vem como HTTP 500 com
  `{"code":500,"message":"...ODBC...","detail":"..."}`. **Encoding**: os textos vêm em cp1252/latin-1
  (ex.: "N�") — decodificar a resposta como latin-1/cp1252, não utf-8. Como casamos por **código**,
  a descrição é só informativa.

### Esqueleto do conector (Agente C — `app/services/totvs_connector.py`, novo)

```python
import base64, httpx
from app import config

def _auth_header() -> str:
    raw = f"{config.TOTVS_WSGETDATA_USER}:{config.TOTVS_WSGETDATA_PASSWORD}".encode()
    return "Basic " + base64.b64encode(raw).decode()

def run_query(sql: str, timeout: float = 60.0) -> list[dict]:
    if not config.is_totvs_configured():
        raise RuntimeError("TOTVS WSGETDATA não configurado (env vazio)")
    url = config.TOTVS_WSGETDATA_URL.rstrip("/") + "/rest/WSGETDATA/v1"
    resp = httpx.post(url, headers={"Authorization": _auth_header(),
                                    "Content-Type": "text/plain"},
                      content=sql.encode("utf-8"), timeout=timeout)
    resp.raise_for_status()
    return resp.json()  # normalizar conforme o envelope real
```

## 3. Glossário de tabelas Protheus (da consulta de referência)

| Tabela | Conteúdo | Campos-chave |
|--------|----------|--------------|
| `SB1010` | Cadastro de produtos (EPI = `B1_GRUPO='0003'`; **preços zerados** aqui) | `B1_COD`, `B1_DESC`, `B1_GRUPO`, `B1_TIPO` |
| `SC7010` | **Pedidos de compra — fonte do valor de EPI** | `C7_PRODUTO`, `C7_PRECO` (unit.), `C7_CC` (centro custo), `C7_EMISSAO`, `C7_TOTAL`, `C7_QUANT` |
| `SD1010` | Itens de NF de entrada | `D1_DOC`, `D1_PEDIDO`, `D1_COD` |
| `SA2010` | Fornecedores | `A2_COD`, `A2_NOME`, `A2_LOJA` |
| `CTT010` | Centros de custo | `CTT_CUSTO`, `CTT_DESC01` |
| `SYS_USR` | Usuários Protheus | `USR_ID`, `USR_NOME` |

Convenções: filtrar sempre `D_E_L_E_T_ <> '*'` (registro não deletado). Campos vêm com padding
(usar `LTRIM(RTRIM(...))`). Filial em `*_FILIAL`.

## 4. Consulta de EPI para a rotina diária (✅ CONFIRMADA em produção — 2026-07-25)

**Descobertas por consulta real ao Protheus:**
1. EPI = produtos do **grupo `B1_GRUPO='0003'`** (TIPO `MC`) em `SB1010` — confirma o `GROUP_MAP`
   do `product_import.py`.
2. **O valor NÃO está no cadastro de produto** (`SB1010`): `B1_UPRC`, `B1_CUSTD`, `B1_UVLRC` estão
   **zerados** para os EPIs. (`B1_PRECO` nem existe nesta base.)
3. **O valor do EPI é o `C7_PRECO`** (preço unitário do **pedido de compra**, tabela `SC7010`), e ele
   é **por centro de custo** (`C7_CC`), com data de emissão (`C7_EMISSAO`). Ex. real: mesmo item
   (`030315`, capa de chuva EXG) custa **22,95** no CC `640021` e **45,61** no CC `420204`.

Isso resolve a regra do dono ("valor por centro de custo"): o preço já nasce por CCU no TOTVS.

**Consulta FINAL da rotina** (último preço por produto × centro de custo) — validada, retorna 201:

```sql
SELECT COD, CC, DESCR, PRECO, EMISSAO FROM (
    SELECT
        LTRIM(RTRIM(C7_PRODUTO)) AS COD,
        LTRIM(RTRIM(C7_CC))      AS CC,
        LTRIM(RTRIM(C7_DESCRI))  AS DESCR,
        C7_PRECO                 AS PRECO,
        C7_EMISSAO               AS EMISSAO,
        ROW_NUMBER() OVER (PARTITION BY C7_PRODUTO, C7_CC
                           ORDER BY C7_EMISSAO DESC, SC7.R_E_C_N_O_ DESC) AS RN
    FROM SC7010 SC7
    JOIN SB1010 SB1 ON B1_COD = C7_PRODUTO AND SB1.D_E_L_E_T_ <> '*'
         AND LTRIM(RTRIM(B1_GRUPO)) = '0003'
    WHERE SC7.D_E_L_E_T_ <> '*' AND C7_PRECO > 0
) t
WHERE RN = 1
```

A rotina faz **upsert em `CCItemPrice`** por `(codccu=CC, produto_codigo=COD)` com `valor=PRECO`,
`is_manual_price=False`, `last_auto_sync=agora` — pulando as linhas travadas manualmente (ver spec).

**Confirmar com o time TOTVS (não bloqueia dev):** se existem outros grupos de EPI além de `0003`;
e a regra quando há vários fornecedores no mesmo dia (hoje: mais recente por emissão + R_E_C_N_O_).

A consulta original de pedidos (SC7/SD1/SA2/CTT/SYS_USR) fica em
`totvs-wsgetdata-sample-pedidos.sql`; a consulta de EPI em `totvs-wsgetdata-epi-precos.sql`.

## 5. Segurança (obrigatório)

- **Credenciais só via env** (`.env` gitignored). `.env.example` traz apenas placeholders vazios.
- O endpoint/segredo **nunca** vai ao frontend nem a logs. A rotina roda 100% server-side.
- O `SELECT` é **fixo no código** (parametrizado com bind quando houver filtro dinâmico) — nunca
  montado a partir de entrada do usuário (gateway de SQL = superfície de injeção).
- A conta `powerbi` tem leitura ampla no Protheus; usar somente para os `SELECT`s necessários.
- **Recomendação**: rotacionar a senha que foi compartilhada em texto puro e, se possível, criar
  um usuário Protheus dedicado com permissão mínima (somente as tabelas/campos usados).
