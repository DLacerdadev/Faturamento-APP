# Research — Relatório de Conciliação Contábil (004)

Decisões técnicas que sustentam o plano. Fontes: código do repo (22/07/2026), sessão de clarify na spec e conhecimento operacional documentado (memória do projeto / `docs/PLANO-EXECUCAO-STATUS.md`).

## D1 — Fonte dos dados: WS ao vivo via job assíncrono

- **Decision**: A geração chama `fetch_payroll(periodo, numemp=6, codccu, progress_cb)` no momento do pedido, dentro de um job do `export_jobs.py` (thread daemon), exatamente como `/senior/billing/export-async`.
- **Rationale**: `fetch_payroll` já retorna `codcal` em cada evento — nenhum dado novo é necessário. O padrão async já resolve o timeout de ~100s do Cloudflare (postmortem 524) e dá `progress_cb` de graça. Decisão confirmada no clarify (números sempre atuais; sem armazenamento novo de folha).
- **Alternatives considered**: snapshot persistido da folha (nova tabela + fluxo de refresh — rejeitado no clarify); consulta síncrona (rejeitada: estoura o limite do proxy em competências grandes).

## D2 — Resultado do job em JSON; Excel derivado do job retido (uma ida só ao WS)

- **Decision**: O job serializa o resultado da conciliação como JSON e chama `finish_ok(job_id, json_bytes, "conciliacao_<periodo>.json", "application/json")`. A tela consome esse JSON. O botão "Exportar planilha" chama um endpoint que converte o JSON **retido no job** (retenção de 1h em memória) em `.xlsx` com openpyxl — sem segunda chamada ao WS.
- **Rationale**: `ExportJob` guarda `content/filename/media_type` arbitrários; derivar o Excel do conteúdo retido evita pagar 2× o custo (~1–2 min) do WS e garante que tela e planilha mostram exatamente os mesmos números.
- **Alternatives considered**: dois jobs (tela e planilha) — desperdício de WS e risco de números divergirem entre gerações; job já produzir o xlsx e a tela parsear — inverte a dependência e complica o drill-down.
- **Consequência aceita**: exportar depois de 1h (ou após restart, jobs em memória) exige gerar de novo — coerente com "resultado não persistido" (FR-10).

## D3 — Classificação: tabela própria, global por codcal, sem descrição vinda do WS

- **Decision**: Tabela `codcal_classifications` no padrão `BenefitEvent` (id, `codcal` unique, `descricao` manual, flag de recorte, `origem`, `observacao`, timestamps). Uma linha por codcal, válida para todas as competências. Codcal presente na folha e sem linha na tabela = "não classificado".
- **Rationale**: O SOAP retorna só o código numérico do cálculo (ex.: 362), sem nome — a descrição precisa ser editável na tela. Clarify decidiu classificação global. "Ausência de linha = não classificado" torna impossível um codcal novo passar despercebido (SC-3) sem necessidade de sincronização prévia.
- **Alternatives considered**: classificação por competência (rejeitada no clarify); buscar descrição na tabela `R044CAL` via MSSQL (`billing_analyzer.query_payroll_breakdown`) — conexão MSSQL não é garantida em prod; fica como enriquecimento futuro opcional, não dependência.

## D4 — Preparação para o TIPCAL futuro

- **Decision**: Campo `origem` na classificação com valores `manual` | `heuristica` | `oficial`. Quando a Senior expuser a marcação de tipo de cálculo, um passo de sincronização grava/atualiza linhas com `origem="oficial"`, que passam a prevalecer visualmente (badge na tela) — o modelo não muda.
- **Rationale**: FR-4 pede evolução sem retrabalho; distinguir a origem também documenta a confiabilidade de cada classificação para o conferente.
- **Alternatives considered**: esperar o TIPCAL (bloqueia a Etapa 3 por prazo de terceiro); campo booleano "oficial" (perde a distinção manual × heurística).

## D5 — Agregação e semântica dos totais

- **Decision**: O serviço (`app/services/conciliacao.py`, função pura) agrega os registros do `fetch_payroll` em dois níveis: codcal → evento (`codigo_evento`, `descricao_evento`, valor total somado, qtde de lançamentos). Totais: competência inteira = soma de todos os codcal; recorte mensal = soma dos codcal classificados como mensal; resíduo = inteira − fora − mensal (zero por construção quando tudo classificado; o status "com resíduo" cobre inconsistências de arredondamento/parse). Valores negativos (estornos/descontos) somam com sinal.
- **Rationale**: A conferência da contabilidade é evento a evento contra o relatório mensal da Senior — agregação por evento dentro do codcal espelha exatamente esse uso (clarify: sem nível funcionário; sem dados pessoais).
- **Alternatives considered**: separar proventos × descontos em colunas — adia-se; o valor com sinal é o que o relatório Senior mostra e mantém a ponte simples.

## D6 — Escopo de CCU e acesso

- **Decision**: Sem filtro → todos os CCUs ativos via `fetch_all_cost_centers()` (cache 6h da feature 003). Com filtro → lista com o CCU escolhido. Tela e APIs exigem `require_role(request, db, "gestor")`; geração, alteração de classificação e export auditados com `audit()` (ações `conciliacao.gerar`, `conciliacao.classificar`, `conciliacao.export`).
- **Rationale**: Mesmo contrato de chamada das exportações (lista de CCUs), reusa cache/throttle; RBAC e auditoria seguem o padrão do repo (ex.: `contract_params`).
- **Alternatives considered**: novo papel "contabilidade" — fora de escopo (assumption da spec).

## D7 — Documento de conciliação (FR-8/FR-9)

- **Decision**: `docs/CONCILIACAO.md` versionado no repo: explicação do recorte (competência inteira × relatório mensal), 2 exemplos numéricos reais **somente com totais e codcal** (política da organização: nenhum dado pessoal), seção "Aprovação" (nome/função/data de quem confere) e seção "Pendência TIPCAL na Senior" com data do último follow-up mensal.
- **Rationale**: Critério 3.1 do Plano de Execução pede documento aprovado por quem confere; versão em git dá trilha de mudanças.
- **Alternatives considered**: página HTML no sistema — a aprovação é de gente externa ao sistema; documento é o instrumento certo, e a tela já linka para ele.
