-- Consulta OFICIAL da rotina diária de EPI (feature 008).
-- Validada em produção (HTTP 201) em 2026-07-25 via WSGETDATA.
-- Retorna o ÚLTIMO preço unitário (C7_PRECO) por PRODUTO x CENTRO DE CUSTO (C7_CC).
-- A rotina faz upsert em CCItemPrice (codccu=CC, produto_codigo=COD, valor=PRECO),
-- pulando linhas com is_manual_price = True.
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
         AND LTRIM(RTRIM(B1_GRUPO)) = '0003'   -- grupo de EPI
    WHERE SC7.D_E_L_E_T_ <> '*' AND C7_PRECO > 0
) t
WHERE RN = 1
