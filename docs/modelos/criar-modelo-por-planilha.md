# Criar um modelo de exportação a partir da planilha do cliente

Passo a passo usado para criar o modelo **SKYRAIL** (planilha "Relatório de
faturamento_Skyrail Terceirizados") — vale para qualquer outro cliente.

## Pelo sistema (caminho normal)

1. **Catálogos → "Novo Modelo por Planilha"** (ou Modelos de Exportação →
   botão "Adicionar por planilha"). Requer papel **gestor** ou superior.
2. **Envie o arquivo .xlsx** do relatório do cliente (o modelo real usado por
   ele, com uma linha de dados preenchida — as fórmulas são lidas de lá).
3. O sistema **analisa e mostra a conferência**: para cada coluna, o que foi
   detectado —
   - **campo**: cabeçalho casou com um dado do sistema (Nome, Salário,
     Encargos Sociais, EPIS (Valor)...) → será preenchido funcionário a
     funcionário. Sinônimos comuns são reconhecidos (COLABORADOR→Nome,
     ADMISSÃO→Dt Admissão, ENC. SOCIAIS→Encargos Sociais, VT→PAGTO.
     VALE-TRANSPORTE...).
   - **fórmula**: célula com fórmula vira template por linha (subtotais,
     taxas, gross-up — referências absolutas tipo `$U$4` são preservadas).
   - **constante**: valor fixo repetido em todas as linhas (ex.: benefícios
     tabelados — café/lanche/almoço no caso Skyrail). **Confira**: se o valor
     varia por funcionário no relatório original, ele foi capturado do
     primeiro funcionário e precisa de revisão.
   - **vazio**: coluna sem conteúdo.
4. Dê **nome** ao modelo, defina os **percentuais padrão** se o layout não os
   tiver em fórmula (no SKYRAIL: encargos 71% como padrão do modelo; taxa adm
   7% e tributos 16,25% já estavam nas fórmulas do layout) e **confirme**.
5. Se o cliente usa **metodologia própria de salário** (valor projetado em vez
   do salário-base), configure a **Fórmula do salário** no editor do modelo —
   ex.: `salario / 29 * 30`. Variáveis: `salario` (base cadastral),
   `total_remuneracao`, `salario_dia_qtde`, `dias_mes` (30). Só aritmética
   (+ − × ÷ e parênteses); expressão validada no salvamento (400 se inválida)
   e avaliada com segurança (sem eval; erro em runtime → cai no salário-base).

## Aba "Fórmulas" — configuração por campo (grade)

No editor do modelo, a aba **Fórmulas** mostra uma grade estilo planilha com
uma linha por campo: CAMPO | ORIGEM PADRÃO | CÓDIGO BUSCADO | NOME DO CÓDIGO |
FÓRMULA. Semântica:

- **Linha vazia** = mapeamento padrão do sistema (o que a coluna já busca hoje
  — a origem e os códigos aparecem como referência).
- **Código buscado** (`257` ou `257,259`): o campo passa a somar ESSES eventos
  da folha, substituindo o mapeamento padrão — e o valor entra nos totais.
- **Fórmula**: transforma o valor do campo. Variável `valor` = valor-base do
  campo (soma dos códigos configurados, ou o padrão). `valor * 2`,
  `salario / 29 * 30`… **Número puro = valor fixo** (ex.: `7.5` no Seguro).
- Campos **calculados pelo sistema** (Total Remuneração, Sub-Total, taxas,
  Total Geral) são somente-leitura na grade.
- Persistência em `BillingModel.campos_config` (JSON); validação no salvamento
  (campo existente, códigos numéricos, fórmula pelo avaliador seguro);
  alterações auditadas via `modelo.editar`.
5. O modelo aparece na listagem com o badge **"por planilha"** e no dropdown
   "Modelo de Exportação" da tela de Faturamento — a exportação sai no layout
   do cliente (mesma aba, cabeçalhos, fórmulas vivas).

## O que o sistema faz por trás (para depuração)

- `POST /api/billing-models/upload-preview` → `parse_model_xlsx`
  (app/services/model_structure.py): detecta a aba, o bloco de cabeçalho
  (linha mais densa em textos; títulos/emitente acima são ignorados), a
  primeira linha de dados e classifica cada coluna (contrato C1, JSON
  `estrutura`).
- `POST /api/billing-models/upload` → valida e salva o `BillingModel` com
  `estrutura` + `colunas` derivadas + `arquivo_origem`.
- Exportação: `_build_billing_export` resolve o modelo pelo NOME do dropdown;
  com `estrutura`, o renderizador (`excel_export._render_por_estrutura`)
  escreve cabeçalhos/fórmulas/constantes e preenche os campos com o df
  calculado.

## Limitações conhecidas

- Fórmulas que referenciam linhas de rodapé/totais ficam literais.
- Colunas de valores por funcionário SEM origem no sistema (ex.: café/lanche/
  almoço tabelados) entram como constantes — origem definitiva depende da
  integração com o módulo de Benefícios da Senior (chamado aberto) ou de
  cadastro contratual.
- O campo "Salário" preenche com o salário-base atual do funcionário
  (metodologias de projeção próprias do cliente — ex.: ÷29 — não são
  reproduzidas; alinhar critério projetado × realizado com o contrato).
