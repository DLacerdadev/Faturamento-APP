/*
 * Planilha de Faturamento (spec 009) — grid Univer + window.FaturamentoGrid.
 *
 * Responsabilidades (dono do grid, contrato grid-ops §2):
 *   - Inicializa o grid Univer self-hosted (/static/vendor/univer/). Se o bundle
 *     não estiver presente (window.__UNIVER_MISSING), cai para uma tabela HTML
 *     mínima — o núcleo testável é o BACKEND; a UI degrada sem travar.
 *   - Expõe window.FaturamentoGrid = { getSnapshot, applyOps, highlight }.
 *     A IA (spec 010) chama essa API; nunca manipula o Univer direto.
 *   - getSnapshot() devolve SÓ estrutura (colunas/tipos/params), SEM PII.
 *
 * Fluxo:
 *   Carregar modelo  -> GET /api/faturamento/grid/model/{id}  (cabeçalhos, sem dados)
 *   Consultar        -> POST /api/faturamento/grid/preview -> poll status -> result (grid do .xlsx)
 *   Exportar         -> GET  /api/faturamento/grid/export/{job}  (mesmos bytes do preview)
 *   applyOps         -> POST /api/faturamento/grid/apply-ops
 */
(function () {
  "use strict";

  var state = {
    grid: null,        // grid corrente {sheet, cells, merges, meta}
    modelId: null,     // id do BillingModel selecionado (para snapshot/apply-ops)
    snapshot: null,    // snapshot de estrutura corrente (sem PII)
    jobId: null,       // job de preview corrente
    univer: null,      // instância Univer, quando disponível
  };

  function $(id) { return document.getElementById(id); }

  function setStatus(msg) {
    var el = $("pl-status");
    if (el) el.textContent = msg || "";
  }

  function api(url, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    return fetch(url, opts).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(j.detail || ("Erro " + r.status));
        });
      }
      return r.json();
    });
  }

  // ---------------------------------------------------------------------------
  // Renderização — Univer quando disponível, senão fallback de tabela.
  // ---------------------------------------------------------------------------
  var univerAvailable = !window.__UNIVER_MISSING &&
    typeof window.UniverCore !== "undefined" &&
    typeof window.UniverFacade !== "undefined";

  function renderGrid(grid) {
    state.grid = grid || { cells: [], merges: [], meta: {} };
    if (univerAvailable) {
      try { renderUniver(state.grid); return; } catch (e) {
        univerAvailable = false;  // não tenta de novo; usa fallback estável
        setStatus("Univer indisponível — grid em modo tabela. " + (e.message || ""));
      }
    }
    renderFallback(state.grid);
  }

  // Converte o grid neutro (cells row-major) em cellData do Univer {r:{c:{v}}}.
  function gridToUniverCellData(grid) {
    var cellData = {};
    var cells = (grid && grid.cells) || [];
    for (var i = 0; i < cells.length; i++) {
      var linha = cells[i] || [];
      for (var j = 0; j < linha.length; j++) {
        var cel = linha[j];
        var v = cel && typeof cel === "object" ? cel.v : cel;
        if (v == null) continue;
        if (!cellData[i]) cellData[i] = {};
        cellData[i][j] = { v: v };
      }
    }
    return cellData;
  }

  function renderUniver(grid) {
    // Bootstrap self-hosted do Univer (bundle UMD em /static/vendor/univer/).
    // Usa a Facade API (createUniver/newAPI). A montagem é best-effort: qualquer
    // exceção derruba para o fallback de tabela (o backend é o núcleo testável).
    var root = $("grid-root");
    root.style.display = "";
    root.innerHTML = "";
    $("grid-fallback").style.display = "none";

    // Reutiliza a instância entre chamadas: recria a planilha quando já existe.
    if (state.univer && state.univer.dispose) {
      try { state.univer.dispose(); } catch (e) { /* ignore */ }
      state.univer = null;
    }

    var facade = window.UniverFacade;
    var create = facade.createUniver || facade.newAPI;
    if (typeof create !== "function") throw new Error("Facade sem createUniver");

    var res = create({
      locale: (window.UniverCore.LocaleType && window.UniverCore.LocaleType.EN_US) || "enUS",
      presets: [],
    });
    var univerAPI = res.univerAPI || res;
    state.univer = res.univer || null;

    var workbookData = {
      id: "faturamento",
      sheets: {
        "sheet-1": {
          id: "sheet-1",
          name: (grid && grid.sheet) || "Faturamento",
          cellData: gridToUniverCellData(grid),
          mergeData: (grid && grid.merges) || [],
        },
      },
      sheetOrder: ["sheet-1"],
    };
    if (univerAPI.createWorkbook) univerAPI.createWorkbook(workbookData);
    else if (univerAPI.createUniverSheet) univerAPI.createUniverSheet(workbookData);
    else throw new Error("API sem createWorkbook");
  }

  function renderFallback(grid) {
    var root = $("grid-fallback");
    var uv = $("grid-root");
    if (uv) uv.style.display = "none";
    root.style.display = "";
    var cells = (grid && grid.cells) || [];
    if (!cells.length) {
      root.innerHTML = '<p style="padding:1rem;color:#9aa5b1;">Sem dados. Escolha um modelo e clique em “Consultar faturamento”.</p>';
      return;
    }
    var html = "<table><tbody>";
    for (var i = 0; i < cells.length; i++) {
      html += "<tr>";
      var linha = cells[i] || [];
      for (var j = 0; j < linha.length; j++) {
        var cel = linha[j];
        var v = cel && typeof cel === "object" ? cel.v : cel;
        var tag = i === 0 ? "th" : "td";
        html += "<" + tag + ' data-r="' + (i + 1) + '" data-c="' + (j + 1) + '">'
          + (v == null ? "" : escapeHtml(String(v))) + "</" + tag + ">";
      }
      html += "</tr>";
    }
    html += "</tbody></table>";
    root.innerHTML = html;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---------------------------------------------------------------------------
  // window.FaturamentoGrid — API do contrato (§2), consumida pela IA (spec 010).
  // ---------------------------------------------------------------------------
  window.FaturamentoGrid = {
    /**
     * Estado atual: colunas + amostra, SEM PII (§5 do contrato). A estrutura
     * vem do snapshot do backend (nunca de dados de linha reais renderizados).
     */
    getSnapshot: function () {
      if (state.snapshot) return state.snapshot;
      // Deriva um snapshot mínimo do grid corrente sem valores de dado (só
      // cabeçalhos da 1ª linha) — defesa em profundidade contra PII.
      var grid = state.grid || {};
      var headerRow = (grid.cells && grid.cells[0]) || [];
      var colunas = headerRow.map(function (c) {
        return c && typeof c === "object" ? c.v : c;
      }).filter(function (v) { return v != null && v !== ""; });
      return {
        model_id: state.modelId,
        colunas: colunas,
        colunas_meta: colunas.map(function (h) { return { header: h }; }),
        params: {},
        sample_row: colunas.reduce(function (o, h) { o[h] = null; return o; }, {}),
      };
    },

    /**
     * Aplica GridOp[] via backend (dono 009). Retorna a Promise<ApplyResult>.
     * A IA produz as ops; o front as aplica — a IA nunca escreve no grid direto.
     */
    applyOps: function (ops) {
      if (!state.modelId) {
        return Promise.resolve({ ok: false, applied: 0, warnings: [], errors: ["Selecione um modelo antes de aplicar operações."], model: null });
      }
      return api("/api/faturamento/grid/apply-ops", {
        method: "POST",
        body: JSON.stringify({ model_id: state.modelId, ops: ops || [], persist: false }),
      }).then(function (res) {
        // Reflete a nova estrutura no snapshot corrente (sem PII).
        if (res && res.ok && res.model) {
          reloadModelGrid(state.modelId);
        }
        return res;
      }).catch(function (e) {
        return { ok: false, applied: 0, warnings: [], errors: [String(e.message || e)], model: null };
      });
    },

    /**
     * Destaca uma coluna/célula referida pela IA. target = {header} | {row, col}.
     */
    highlight: function (target) {
      clearHighlight();
      if (!target) return;
      var root = $("grid-fallback");
      if (root && root.style.display !== "none") {
        var sel;
        if (target.header != null) {
          var idx = colIndexByHeader(target.header);
          if (idx >= 0) sel = root.querySelectorAll('[data-c="' + (idx + 1) + '"]');
        } else if (target.row != null && target.col != null) {
          sel = root.querySelectorAll('[data-r="' + target.row + '"][data-c="' + target.col + '"]');
        }
        if (sel) sel.forEach(function (el) { el.classList.add("hl"); });
      }
      // No caminho Univer, a API de seleção/realce seria usada aqui.
    },
  };

  function clearHighlight() {
    document.querySelectorAll("#grid-fallback .hl").forEach(function (el) {
      el.classList.remove("hl");
    });
  }

  function colIndexByHeader(header) {
    var grid = state.grid || {};
    var headerRow = (grid.cells && grid.cells[0]) || [];
    for (var j = 0; j < headerRow.length; j++) {
      var c = headerRow[j];
      var v = c && typeof c === "object" ? c.v : c;
      if (v === header) return j;
    }
    return -1;
  }

  // ---------------------------------------------------------------------------
  // Ações da toolbar.
  // ---------------------------------------------------------------------------
  function selectedModelId() {
    var sel = $("pl-modelo");
    var opt = sel && sel.options[sel.selectedIndex];
    var id = opt && opt.getAttribute("data-id");
    return id ? parseInt(id, 10) : null;
  }

  function reloadModelGrid(modelId) {
    if (!modelId) return Promise.resolve();
    return api("/api/faturamento/grid/model/" + modelId).then(function (res) {
      state.snapshot = res.snapshot || null;
      renderGrid(res.grid);
    });
  }

  function onCarregarModelo() {
    var id = selectedModelId();
    state.modelId = id;
    if (!id) {
      setStatus("Modelo FEMSA padrão não tem estrutura editável. Use “Consultar faturamento”.");
      renderGrid({ cells: [[{ v: "Selecione um modelo cadastrado para carregar sua estrutura." }]], merges: [], meta: {} });
      return;
    }
    setStatus("Carregando estrutura do modelo…");
    reloadModelGrid(id).then(function () { setStatus("Estrutura carregada (sem dados de funcionário)."); })
      .catch(function (e) { setStatus("Erro: " + e.message); });
  }

  function onConsultar() {
    var modelo = $("pl-modelo").value || "femsa";
    var competencia = $("pl-competencia").value;
    var codccu = ($("pl-ccu").value || "").trim() || null;
    state.modelId = selectedModelId();
    if (!competencia) { setStatus("Informe a competência."); return; }

    var btn = $("btn-consultar");
    btn.disabled = true;
    setStatus("Enviando consulta…");
    api("/api/faturamento/grid/preview", {
      method: "POST",
      body: JSON.stringify({ modelo: modelo, periodo: competencia, codccu: codccu }),
    }).then(function (res) {
      state.jobId = res.job_id;
      return pollPreview(res.job_id);
    }).then(function () {
      btn.disabled = false;
    }).catch(function (e) {
      btn.disabled = false;
      setStatus("Erro: " + e.message);
    });
  }

  function pollPreview(jobId) {
    return new Promise(function (resolve, reject) {
      var tries = 0;
      function tick() {
        api("/api/faturamento/grid/status/" + jobId).then(function (st) {
          tries++;
          setStatus((st.message || "Processando…") + (st.total ? " (" + st.percent + "%)" : ""));
          if (st.status === "done") {
            return api("/api/faturamento/grid/result/" + jobId).then(function (r) {
              renderGrid(r.grid);
              $("btn-exportar").disabled = false;
              setStatus("Preview carregado — idêntico ao .xlsx que será exportado.");
              resolve();
            });
          }
          if (st.status === "error") { reject(new Error(st.error || "Falha na consulta.")); return; }
          if (tries > 600) { reject(new Error("Tempo esgotado.")); return; }
          setTimeout(tick, 1500);
        }).catch(reject);
      }
      tick();
    });
  }

  function onExportar() {
    if (!state.jobId) { setStatus("Consulte o faturamento primeiro."); return; }
    window.location.href = "/api/faturamento/grid/export/" + state.jobId;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var d = new Date();
    var mm = String(d.getMonth() + 1).padStart(2, "0");
    var comp = $("pl-competencia");
    if (comp && !comp.value) comp.value = d.getFullYear() + "-" + mm;

    var b1 = $("btn-consultar"); if (b1) b1.addEventListener("click", onConsultar);
    var b2 = $("btn-carregar-modelo"); if (b2) b2.addEventListener("click", onCarregarModelo);
    var b3 = $("btn-exportar"); if (b3) b3.addEventListener("click", onExportar);

    renderGrid({ cells: [], merges: [], meta: {} });
    if (window.__UNIVER_MISSING) {
      setStatus("Grid em modo tabela (bundle Univer não vendorizado — ver INTEGRATION-NOTES).");
    }
  });
})();
