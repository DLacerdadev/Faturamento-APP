/*
 * Assistente de IA do faturamento (spec 010).
 *
 * Monta um painel de chat em #ai-assistant-root (fornecido pela planilha da
 * spec 009), conversa com POST /ia/chat, PRÉ-VISUALIZA as `ops` devolvidas e as
 * aplica via window.FaturamentoGrid.applyOps (contrato grid-ops §2).
 *
 * Desacoplamento: se window.FaturamentoGrid ainda não existir (planilha não
 * carregada, ou uso na página standalone /ia/chat), o painel continua
 * funcionando — só desabilita o botão "Aplicar" e avisa. A IA nunca escreve no
 * grid diretamente; ela sempre devolve ops que o front aplica sob confirmação.
 *
 * Sem dependências externas (vanilla JS, padrão do projeto).
 */
(function () {
  "use strict";

  const ROOT_ID = "ai-assistant-root";

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === "style") node.style.cssText = attrs[k];
        else if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      }
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function hasGrid() {
    return !!(window.FaturamentoGrid && typeof window.FaturamentoGrid.applyOps === "function");
  }

  function currentModelId() {
    // A planilha da 009 injeta window.PLANILHA_MODELOS e um <select id="pl-modelo">.
    // Na página standalone usamos <select id="ia-modelo">.
    const sel = document.getElementById("ia-modelo") || document.getElementById("pl-modelo");
    if (sel && sel.selectedOptions && sel.selectedOptions[0]) {
      const id = sel.selectedOptions[0].getAttribute("data-id");
      if (id) return parseInt(id, 10);
    }
    if (window.AI_DEFAULT_MODEL_ID) return window.AI_DEFAULT_MODEL_ID;
    return null;
  }

  function getSnapshot() {
    if (hasGrid() && typeof window.FaturamentoGrid.getSnapshot === "function") {
      try { return window.FaturamentoGrid.getSnapshot(); } catch (e) { /* ignore */ }
    }
    return null; // backend deriva do modelo
  }

  function AIPanel(root) {
    this.root = root;
    this.sessionId = null;
    this.pendingOps = null;
    this.build();
  }

  AIPanel.prototype.build = function () {
    this.root.innerHTML = "";
    this.root.appendChild(el("h3", {
      style: "margin:0 0 .5rem;font-size:.95rem;color:var(--gold,#D4A84B);",
      text: "Assistente de IA",
    }));

    this.log = el("div", {
      class: "ai-log",
      style: "display:flex;flex-direction:column;gap:.5rem;max-height:48vh;overflow:auto;" +
        "font-size:.8rem;line-height:1.45;margin-bottom:.6rem;",
    });
    this.root.appendChild(this.log);

    this.preview = el("div", { style: "margin-bottom:.5rem;" });
    this.root.appendChild(this.preview);

    this.input = el("textarea", {
      rows: "3", placeholder: "Ex.: adicione uma coluna Uniformes depois de Seguro de Vida",
      style: "width:100%;box-sizing:border-box;background:var(--bg-card,#10203a);" +
        "color:var(--text,#e8edf3);border:1px solid var(--border,#23344d);border-radius:8px;" +
        "padding:.5rem;font-family:'JetBrains Mono',monospace;font-size:.78rem;resize:vertical;",
    });
    this.root.appendChild(this.input);

    const self = this;
    this.sendBtn = el("button", {
      type: "button",
      style: "margin-top:.5rem;width:100%;background:var(--gold,#D4A84B);color:#10203a;" +
        "border:none;border-radius:8px;padding:.5rem;font-weight:700;cursor:pointer;",
      text: "Enviar",
    });
    this.sendBtn.addEventListener("click", function () { self.send(); });
    this.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); self.send(); }
    });
    this.root.appendChild(this.sendBtn);

    this.status = el("div", {
      style: "font-size:.72rem;color:var(--text-muted,#9aa5b1);margin-top:.4rem;min-height:1em;",
    });
    this.root.appendChild(this.status);

    if (!hasGrid()) {
      this.setStatus("Grid não detectado — a IA gera as operações, mas a aplicação automática está indisponível aqui.");
    }
  };

  AIPanel.prototype.setStatus = function (msg) { this.status.textContent = msg || ""; };

  AIPanel.prototype.addMsg = function (role, text) {
    const isUser = role === "user";
    const bubble = el("div", {
      style: "padding:.45rem .6rem;border-radius:8px;white-space:pre-wrap;" +
        (isUser
          ? "background:var(--bg-hover,#17273f);align-self:flex-end;max-width:92%;"
          : "background:rgba(212,168,75,.10);border:1px solid var(--border,#23344d);max-width:96%;"),
      text: text,
    });
    this.log.appendChild(bubble);
    this.log.scrollTop = this.log.scrollHeight;
  };

  AIPanel.prototype.renderPreview = function (ops) {
    this.preview.innerHTML = "";
    this.pendingOps = null;
    if (!ops || !ops.length) return;
    this.pendingOps = ops;

    const box = el("div", {
      style: "border:1px dashed var(--gold,#D4A84B);border-radius:8px;padding:.5rem;font-size:.74rem;",
    });
    box.appendChild(el("div", {
      style: "font-weight:700;margin-bottom:.3rem;color:var(--gold,#D4A84B);",
      text: "Pré-visualização de operações (" + ops.length + ")",
    }));
    ops.forEach(function (op) {
      box.appendChild(el("div", {
        style: "font-family:'JetBrains Mono',monospace;opacity:.9;margin:.1rem 0;",
        text: "• " + describeOp(op),
      }));
    });

    const self = this;
    const applyBtn = el("button", {
      type: "button",
      style: "margin-top:.5rem;width:100%;background:transparent;color:var(--gold,#D4A84B);" +
        "border:1px solid var(--gold,#D4A84B);border-radius:8px;padding:.45rem;font-weight:700;cursor:pointer;",
      text: hasGrid() ? "Aplicar no grid" : "Aplicar (grid indisponível)",
    });
    applyBtn.disabled = !hasGrid();
    applyBtn.addEventListener("click", function () { self.apply(); });
    box.appendChild(applyBtn);
    this.preview.appendChild(box);
  };

  AIPanel.prototype.apply = function () {
    if (!this.pendingOps || !hasGrid()) return;
    let res;
    try {
      res = window.FaturamentoGrid.applyOps(this.pendingOps);
    } catch (e) {
      this.setStatus("Erro ao aplicar no grid: " + e);
      return;
    }
    if (res && res.ok) {
      this.setStatus("Aplicado: " + (res.applied != null ? res.applied : this.pendingOps.length) + " operação(ões).");
      this.preview.innerHTML = "";
      this.pendingOps = null;
    } else {
      const errs = (res && res.errors && res.errors.join("; ")) || "erro desconhecido";
      this.setStatus("O grid recusou: " + errs);
    }
  };

  AIPanel.prototype.send = function () {
    const msg = (this.input.value || "").trim();
    if (!msg) return;
    this.addMsg("user", msg);
    this.input.value = "";
    this.preview.innerHTML = "";
    this.setStatus("Consultando a IA…");
    this.sendBtn.disabled = true;

    const self = this;
    const body = {
      model_id: currentModelId(),
      message: msg,
      snapshot: getSnapshot(),
      session_id: this.sessionId,
    };

    fetch("/ia/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
      })
      .then(function (out) {
        self.sendBtn.disabled = false;
        if (!out.ok) {
          const detail = (out.data && out.data.detail) || ("Erro " + out.status);
          self.addMsg("assistant", "⚠ " + detail);
          self.setStatus("");
          return;
        }
        self.sessionId = out.data.session_id || self.sessionId;
        self.addMsg("assistant", out.data.reply || "(sem resposta)");
        self.renderPreview(out.data.ops || []);
        self.setStatus(
          (out.data.ops && out.data.ops.length ? out.data.ops.length + " operação(ões) sugerida(s). " : "") +
          (out.data.rejected && out.data.rejected.length ? out.data.rejected.length + " descartada(s)." : "")
        );
      })
      .catch(function (e) {
        self.sendBtn.disabled = false;
        self.addMsg("assistant", "⚠ Falha de rede ao falar com a IA.");
        self.setStatus(String(e));
      });
  };

  function describeOp(op) {
    switch (op.op) {
      case "add_column": return "Adicionar coluna '" + op.header + "'" + (op.after ? " após '" + op.after + "'" : "");
      case "remove_column": return "Remover coluna '" + op.header + "'";
      case "rename_column": return "Renomear '" + op.header + "' → '" + op.novo_header + "'";
      case "move_column": return "Mover '" + op.header + "'" + (op.before ? " antes de '" + op.before + "'" : (op.after ? " após '" + op.after + "'" : ""));
      case "set_field_config": return "Configurar campo '" + op.campo + "'" + (op.codigo ? " (evento " + op.codigo + ")" : "");
      case "set_model_param": return "Parâmetro " + op.param + " = " + op.valor;
      case "add_item": return "Adicionar item (" + (op.target || "?") + ")";
      default: return JSON.stringify(op);
    }
  }

  function mount() {
    const root = document.getElementById(ROOT_ID);
    if (!root) return;
    // Evita montagem dupla.
    if (root.getAttribute("data-ai-mounted") === "1") return;
    root.setAttribute("data-ai-mounted", "1");
    new AIPanel(root);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }

  // Exposto para a página standalone (permite remontar após trocar de modelo).
  window.AIAssistant = { mount: mount };
})();
