# REST Contracts — Relatório de Conciliação Contábil (004)

Router: `app/routers/conciliacao.py`. **Todos os endpoints exigem `require_role(request, db, "gestor")`** (401 sem login, 403 para operador). Ações auditadas: `conciliacao.gerar`, `conciliacao.classificar`, `conciliacao.export`.

## Tela

### `GET /conciliacao` → HTML
Renderiza `conciliacao.html` (herda `base.html`). Contexto: `user`, `token`, lista de CCUs (cache) e classificações existentes. Sem login → redirect 303 `/login`.

## Geração (job assíncrono — padrão export-async)

### `POST /api/conciliacao/gerar`
```jsonc
// request
{ "periodo": "2026-06-01", "codccu": "620083" }   // codccu opcional; ausente = todos os CCUs
// response 200
{ "success": true, "job_id": "uuid" }
```
Cria `ExportJob`, dispara thread (`fetch_payroll` com `progress_cb` → `set_progress`), audita `conciliacao.gerar`. Erros do WS em prod → job `status="error"` com mensagem (P2).

### `GET /api/conciliacao/status/{job_id}`
```jsonc
{ "job_id": "uuid", "status": "running", "percent": 42, "message": "CCU 5/12" }
// status: "pending" | "running" | "done" | "error"; em "error": campo "error" com a mensagem
```
404 se o job não existe (expirado >1h ou pós-restart) — a tela orienta gerar novamente.

### `GET /api/conciliacao/resultado/{job_id}`
Retorna o JSON da conciliação (estrutura em `data-model.md`) quando `status=="done"`; 409 se ainda não concluído; 404 se expirado.

### `GET /api/conciliacao/export/{job_id}`
Converte o JSON **retido no job** em `.xlsx` (abas Resumo/Decomposição/Eventos — sem ida nova ao WS, D2). `StreamingResponse` com `Content-Disposition: attachment; filename=Conciliacao_<periodo>[_<ccu>].xlsx`. Audita `conciliacao.export`. 409/404 como acima.

## Classificação de codcal

### `GET /api/conciliacao/classificacoes`
```jsonc
{ "items": [ { "codcal": 362, "descricao": "Folha mensal", "recorte_mensal": true,
               "origem": "manual", "observacao": null, "updated_at": "..." } ] }
```

### `PUT /api/conciliacao/classificacoes/{codcal}`
Upsert (cria se não existe — é assim que um "não classificado" é resolvido).
```jsonc
// request
{ "descricao": "Folha mensal", "recorte_mensal": true, "observacao": "ok contabilidade 07/2026" }
// response 200
{ "success": true, "item": { ...como no GET... } }
```
`origem` é gravada como `"manual"` neste endpoint (valores `heuristica`/`oficial` são de fluxos internos — D4). Audita `conciliacao.classificar` com antes/depois. 422 se payload inválido.

### `DELETE /api/conciliacao/classificacoes/{codcal}`
Remove a classificação (codcal volta a "não classificado"). Audita com o estado anterior. 404 se não existe.

## Erros comuns (todos os endpoints)

| Código | Quando |
|--------|--------|
| 401 | sem sessão válida |
| 403 | papel < gestor |
| 404 | job expirado/inexistente; classificação inexistente (DELETE) |
| 409 | resultado/export pedido antes de `status=="done"` |
| 422 | payload inválido (Pydantic) |
| 502 | falha do WS Senior na geração síncrona de contexto (a geração em si falha via job `status="error"`) |
