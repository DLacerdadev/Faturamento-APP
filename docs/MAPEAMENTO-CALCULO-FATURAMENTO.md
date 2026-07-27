# Mapeamento do Cálculo de Faturamento — base para a IA de ajuste

**Data:** 27/07/2026 · **Origem:** engenharia reversa do gabarito Cooperstandard Atibaia (CCU 640045, competência 06/2026).
**Para que serve:** este documento registra COMO o faturamento é calculado e QUAIS campos precisam ser
**parametrizáveis por dados** (não por código), para o **chat de IA** (spec 010) permitir que o usuário
ajuste o cálculo — classificar codcal/tipo, adicionar/remover eventos da base, mudar percentuais — sem
mexer no código.

---

## 1. Contexto e objetivo

O faturamento é um cálculo **derivado da folha** (base de proventos → encargos → taxa → gross-up de imposto).
Hoje as regras (quais eventos entram, percentuais, layout) estão **espalhadas no código**
(`REMUNERACAO_BASE_COLUMNS`, `EVENT_TO_FEMSA_MAPPING`, `ENCARGOS_SOCIAIS_RATE`, modelos). Cada contrato
(FEMSA, Skyrail, Cooper…) tem regras diferentes. A IA deve editar uma **configuração por contrato**
(dados no banco), e o motor de cálculo lê essa config.

---

## 2. Validação 640045 / 06-2026 (o gabarito)

Três arquivos de referência (mesmo CCU e mês): folha Senior (xlsx), folha oficial (PDF "Cálculo Mensal"),
e o faturamento correto (xlsx). Resultado:

### 2.1 A FOLHA do sistema bate EXATO ✅
Nosso `fetch_payroll` (codcal 395) reproduz o resumo da folha oficial na casa do centavo:

| Verba | Folha oficial | Sistema (codcal 395) |
|-------|---------------|----------------------|
| Proventos + Vantagens | 259.212,02 + 1.212,46 = **260.424,48** | **260.424,48** |
| Descontos | 157.902,01 | 157.902,01 |
| Líquido | 102.522,47 | 102.522,47 |
| Colaboradores | 109 | 109 |

**Conclusão: o dado bruto da Senior que o sistema puxa está correto.** O problema é só recorte + transformação.

### 2.2 O problema do codcal / TIPCAL (crítico para a IA)
- Em 640045/junho existem **dois cálculos**: **codcal 395 = 109** (mensal, a referência) e **codcal 228 = 188 demitidos**
  (rescisão/complementar, **grupos disjuntos**, interseção zero). O faturamento deve usar **só o 395**.
- **Ambos contêm o evento "Dias Normais" (200)** → a heurística "codcal com evento 200 = mensal" **NÃO separa**.
- **O número do codcal muda a cada competência** (maio=392, junho=395, julho=400). Não dá pra fixar nem classificar
  manualmente todo mês de forma sustentável.
- A separação limpa exige o **TIPCAL (tipo de cálculo) da Senior**, que o **WS não expõe** (SOAP consultaRegistros
  devolve só `codCal`, sem o tipo). Pendência registrada junto à Senior (Etapa 3.3).
- **Papel da IA:** permitir que o usuário **atribua o tipo** a cada codcal da competência (mensal / rescisão /
  férias / 13º / complementar) — o "TIPCAL" passa a ser um dado nosso, editável por chat. O faturamento fatura
  só os codcal marcados como "mensal" (ou conforme a regra do contrato).

---

## 3. Fórmula decodificada — contrato "Cooper Atibaia" (exemplo completo)

Todos os números batem com o gabarito 640045/06 (109 funcionários, **TOTAL DA NF = 385.435,74**).

### 3.1 Base — "Total Remuneração" = 198.719,77
É uma **whitelist de eventos do MÊS** (não "todos os proventos"). Os 109 incluem 77 demitidos, mas as
**verbas de rescisão deles NÃO entram na base** (só o trabalho do mês).

**Entram (+):**
| Coluna gabarito | Evento Senior | Total |
|-----------------|---------------|-------|
| Dias Normais | **200** | 162.224,28 |
| Adicional Noturno | **1950** | 8.201,40 |
| Adicional Noturno 30% | **3022** | 150,62 |
| HORA EXTRA 52% | **3620** | 7.165,64 |
| Horas Extras c/ 100% | **259** | 17.737,43 |
| DSR Sobre Horas Extras | **265** | 5.354,35 |
| Int. Adic. Noturno no DSR | **3030** (DSR s/ Adic. Noturno) | 1.663,94 |
| Dias Lic. Médica até 15d | **213** | 824,85 |
| Dif. Salário | ⚠️ código a fechar (~949,80) | 949,80 |

**Saem (−):**
| Coluna gabarito | Evento Senior | Total |
|-----------------|---------------|-------|
| Desconto DSR | **2469** | 1.756,81 |
| Dias Faltas | **202** | 3.619,85 |
| Desconto Atrasos | ⚠️ ~3035 a confirmar (~175,88) | 175,88 |

Soma: proventos 204.272,31 − descontos 5.552,54 = **198.719,77** ✅

**Excluídos da base (verbas de rescisão presentes, mas NÃO faturadas):** 1550 Saldo de Salário,
662 Dias Férias Proporc. Rescisão, 659 1/3 Férias Rescisão, 850 13º Proporc. Rescisão, 652/851/856 médias.

### 3.2 Encargos, taxa, impostos (percentuais FIXOS, confirmados em todos os 108)
| Componente | Fórmula | Valor |
|-----------|---------|-------|
| **Encargos Sociais** | **55,84% × Total Remuneração** (razão constante 0,55840) | 110.965,12 |
| **Taxa Administrativa** | **6% × (Total Remuneração + Encargos)** + **piso por funcionário** p/ baixa remuneração | 18.788,25 |
| **Impostos (Encargos Fiscais)** | **gross-up: 16,53% da NF** (razão constante 0,16530) | 63.712,53 |

> ⚠️ O **encargo de 55,84% varia por contrato**: sistema (default) = 57,91%; modelo SKYRAIL = 71%; Cooper = 55,84%.
> A taxa aqui é **6%** (SKYRAIL usa 7%). A alíquota de imposto é ~**16,53%** (via gross-up `Total = base / (1 − alíq)`).

### 3.3 Custos e ajustes
| Item | Evento / regra | Valor |
|------|----------------|-------|
| Transporte Fretado 3% | evento **2453** (Vale Transporte 6%) | 2.663,60 |
| Ex. Médico | ⚠️ evento a fechar | 3.452,58 |
| Unif/EPI's, Botas, Reembolso, Desc. Refeição | zerados neste mês | 0,00 |

### 3.4 Montagem da NF (a fechar 100%)
Estrutura confirmada: `NF ≈ (Total Remuneração + Encargos + Taxa) com gross-up de 16,53% (+ custos)`.
Falta cravar a composição exata (posição do transporte/exames e o piso da taxa) — diferença residual
de ~R$ 200–600 no total, atribuída ao piso da taxa e ao arredondamento por funcionário.

---

## 4. Campos que a IA precisa poder ajustar (config POR CONTRATO)

Este é o **schema-alvo** da configuração de cálculo, editável por chat (hoje espalhado no código):

1. **Regras de codcal / tipo ("TIPCAL nosso")** — para cada codcal da competência, o tipo:
   `mensal | rescisao | ferias | decimo_terceiro | complementar | ignorar`. Fatura só os do tipo faturável
   (em geral `mensal`). *(Hoje: `codcal_classifications` — precisa evoluir para tipo, e o número do codcal
   muda todo mês, então a IA reidentifica por competência.)*
2. **Whitelist de eventos da BASE** — lista de códigos de evento que entram (+) e que saem (−) do Total
   Remuneração, POR CONTRATO. *(Hoje: `REMUNERACAO_BASE_COLUMNS` + `EVENT_TO_FEMSA_MAPPING`, fixos em código.)*
3. **Percentuais**: `encargos_pct` (ex.: 55,84 / 57,91 / 71), `taxa_adm_pct` (6 / 7) + **piso da taxa**,
   `imposto_pct`/alíquota do gross-up (16,53). *(Hoje: `ENCARGOS_SOCIAIS_RATE` fixo + `BillingModel.*_pct`.)*
4. **Custos**: mapeamento evento→coluna de transporte (2453), exames, uniformes/EPIs; e sinal (soma/subtrai).
5. **Layout/modelo**: qual planilha-modelo (por evento, tipo Cooper) o contrato usa.

> Objetivo: mudar qualquer um desses via chat da IA **sem deploy**. O motor de cálculo lê a config e aplica.

---

## 5. Itens em aberto (fechar na implementação)
1. Código Senior de **Dif. Salário** (~949,80) e **Desconto Atrasos** (~175,88) — provável composto.
2. **Piso da Taxa Administrativa** por funcionário (mínimo) — total ~R$ 207 acima dos 6%.
3. Evento de **Ex. Médico** (3.452,58).
4. Composição exata da **NF** no gross-up (posição de transporte/exames).
5. **TIPCAL** — depende da Senior expor, OU da IA permitir classificação por competência (mecanismo nosso).

---

## 6. Onde vive no código hoje (para a IA parametrizar)
- Base/eventos: `app/services/billing_processor.py` (`REMUNERACAO_BASE_COLUMNS`, `ENCARGOS_SOCIAIS_RATE=0.5791`),
  `app/services/excel_export.py` (`EVENT_TO_FEMSA_MAPPING`, `calcular_faturamento`, gross-up).
- Recorte por codcal: `app/routers/integrations.py` (`_codcals_do_mensal`, auto-detecção por evento base 200 —
  insuficiente, ver §2.2).
- Classificação de codcal: `app/models/codcal_classification.py` + tela `/conciliacao`.
- Modelos por contrato: `app/models/billing_model.py` (colunas, estrutura, %); IA (spec 010): `app/routers/ai_chat.py`.

**Regra de ouro para a IA:** o cálculo deve virar **dado configurável por contrato** (itens da §4); a IA edita
esses dados por chat, e o motor de faturamento passa a ler a config em vez de constantes no código.
