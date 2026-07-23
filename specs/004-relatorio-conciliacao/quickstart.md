# Quickstart — Relatório de Conciliação Contábil (004)

Como exercitar a feature ponta a ponta.

## Dev (DEV_MODE, dados locais)

1. Subir o app: `uvicorn app.main:app --reload` (sem `SENIOR_SOAP_USER`/`PASSWORD` → DEV_MODE usa folha local).
2. Logar com usuário **gestor** e abrir **Conciliação** no menu (`/conciliacao`).
3. Selecionar uma competência com dados locais e clicar **Gerar** — acompanhar a barra de progresso (poll em `/api/conciliacao/status/{job_id}`).
4. Conferir: cards de totais (competência inteira / recorte mensal / fora / resíduo), tabela por codcal, e codcals destacados como **não classificados** (primeira execução: todos).
5. Classificar cada codcal na própria tela (descrição + mensal/fora) — status deve migrar de `incompleta` para `fechada` com resíduo R$ 0,00.
6. Abrir o drill-down de um codcal e verificar que só há eventos agregados (valor total + lançamentos) — **nenhum nome/CPF/valor individual**.
7. Exportar a planilha e conferir as 3 abas (Resumo, Decomposição, Eventos) contra a tela.
8. Logar como **operador** e confirmar 403 em `/conciliacao` e nas APIs (Scenario 4).
9. Verificar em `/auditoria` os registros `conciliacao.gerar`, `conciliacao.classificar`, `conciliacao.export`.

## Validação em produção (fecha a Etapa 3 do Plano de Execução)

1. Gerar a conciliação de uma competência já conferida pela contabilidade (todos os CCUs).
2. Classificar os ~10 codcal reais com o conhecimento do recorte; status `fechada`, resíduo R$ 0,00 (SC-2).
3. Bater o "total do recorte mensal" (e a aba Eventos) com o relatório mensal da Senior da mesma competência — evento a evento.
4. Preencher `docs/CONCILIACAO.md` com 2 exemplos numéricos reais (somente totais/codcal — sem dados pessoais) e colher a aprovação de quem confere (SC-5).
5. Ciclo seguinte: contabilidade fecha a conferência usando apenas a planilha exportada, sem intervenção do Daniel (SC-1).

## Casos de borda a conferir

- Competência sem dados → mensagem clara, sem relatório vazio.
- Derrubar o WS (prod) durante a geração → job termina em `error` com mensagem e botão de tentar de novo.
- Codcal novo (simular removendo uma classificação) → volta a "não classificado" e o status deixa de ser `fechada`.
- Export após >1h do job → 404 com orientação de gerar novamente.
