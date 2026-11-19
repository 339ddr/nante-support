(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const chat = $("#chat");
  const welcome = $("#welcome");
  const input = $("#input");
  const sendBtn = $("#sendBtn");
  const sidebar = $("#sidebar");
  const mask = $("#drawerMask");
  const modal = $("#citeModal");

  const SESSION_KEY = "nante_support_session";
  let sessionId = localStorage.getItem(SESSION_KEY) || "";
  let busy = false;

  // ---------------- 模型凭据（仅保存在本机浏览器）----------------
  const CRED_KEY = "nante_support_llm";

  function loadCred() {
    try {
      const raw = localStorage.getItem(CRED_KEY);
      if (!raw) return null;
      const cred = JSON.parse(raw);
      return cred && cred.base_url && cred.api_key ? cred : null;
    } catch {
      return null;
    }
  }

  function saveCred(cred) {
    if (cred) localStorage.setItem(CRED_KEY, JSON.stringify(cred));
    else localStorage.removeItem(CRED_KEY);
  }

  let cred = loadCred();

  // 发送时把凭据带上；服务端不会保存
  function credPayload() {
    if (!cred) return {};
    return { base_url: cred.base_url, api_key: cred.api_key, model: cred.model || null };
  }

  const ICONS = {
    engine: '<svg viewBox="0 0 24 24"><path d="M5 9h8l3 3h3v5h-3l-3 3H7l-2-2H3v-7h2z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 6h5M9 18v2M16 6v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    brake: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M12 3.5v5.3M12 15.2v5.3M3.5 12h5.3M15.2 12h5.3" stroke="currentColor" stroke-width="1.5"/></svg>',
    electric: '<svg viewBox="0 0 24 24"><path d="M13 2 5.5 13H11l-1 9 8-11h-5.5z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
    suspension: '<svg viewBox="0 0 24 24"><path d="M7 3v4M17 3v4M7 21v-4M17 21v-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M9 7h6M9 17h6M12 7v3.5M12 13.5V17" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="1.8" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    body: '<svg viewBox="0 0 24 24"><path d="M3 14l2-4h14l2 4v4h-3M7 18H3v-4M7 18h10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="7" cy="18" r="1.8" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="17" cy="18" r="1.8" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    wear: '<svg viewBox="0 0 24 24"><path d="M5 12a7 7 0 0 1 14 0v3a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9 12v3M12 12v3M15 12v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    part: '<svg viewBox="0 0 24 24"><path d="M14.5 6a3.5 3.5 0 0 0-4.6 4.2L5 15.1V19h3.9l4.9-4.9A3.5 3.5 0 0 0 18 9.5L16 11l-1.5-1.5L16 8z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  };

  const SYSTEM_ICON = {
    "发动机系统": "engine",
    "制动系统": "brake",
    "电气系统": "electric",
    "悬挂转向系统": "suspension",
    "车身附件及空调系统": "body",
    "易损消耗件": "wear",
  };

  const TOOL_LABEL = {
    search_products: "商品库检索",
    calculate_refund: "退款测算",
    check_warranty: "保修查询",
    install_guide: "安装指引",
    maintenance_cycle: "更换周期查询",
    human_handoff: "转人工",
  };

  const BOOK_ICON = '<svg viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M4 18.5V5.5M8 7h8M8 10.5h5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

  // ---------------- Markdown ----------------
  const escapeHtml = (str) =>
    String(str).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  function inline(text) {
    return text
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function renderTable(rows) {
    const cells = rows.map((row) => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    if (cells.length > 1 && cells[1].every((c) => /^:?-{2,}:?$/.test(c))) cells.splice(1, 1);
    if (!cells.length) return "";
    const head = cells[0].map((c) => `<th>${inline(c)}</th>`).join("");
    const body = cells.slice(1).map((row) => `<tr>${row.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("");
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function renderMarkdown(md) {
    const lines = escapeHtml(md).replace(/\r/g, "").split("\n");
    const out = [];
    let list = null;
    let paragraph = [];

    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const flushParagraph = () => {
      if (paragraph.length) { out.push(`<p>${inline(paragraph.join("<br>"))}</p>`); paragraph = []; }
    };

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) { closeList(); flushParagraph(); continue; }

      if (trimmed.startsWith("|")) {
        closeList(); flushParagraph();
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(lines[i].trim()); i += 1; }
        i -= 1;
        out.push(renderTable(rows));
        continue;
      }

      if (/^#{1,4}\s+/.test(trimmed)) {
        closeList(); flushParagraph();
        out.push(`<h4>${inline(trimmed.replace(/^#{1,4}\s+/, ""))}</h4>`);
        continue;
      }

      if (/^>\s?/.test(trimmed)) {
        closeList(); flushParagraph();
        out.push(`<blockquote>${inline(trimmed.replace(/^>\s?/, ""))}</blockquote>`);
        continue;
      }

      const ul = trimmed.match(/^[-*•]\s+(.*)$/);
      const ol = trimmed.match(/^\d+[.、)]\s+(.*)$/);
      if (ul || ol) {
        flushParagraph();
        const type = ul ? "ul" : "ol";
        if (list !== type) { closeList(); out.push(`<${type}>`); list = type; }
        out.push(`<li>${inline((ul || ol)[1])}</li>`);
        continue;
      }

      closeList();
      paragraph.push(trimmed);
    }
    closeList();
    flushParagraph();
    return out.join("");
  }

  // ---------------- UI helpers ----------------
  function scrollBottom() {
    requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight + 80; });
  }

  function hideWelcome() {
    if (welcome && welcome.parentNode) welcome.remove();
  }

  function addUserMessage(text) {
    hideWelcome();
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="avatar">我</div><div class="msg-body"><div class="bubble">${escapeHtml(text).replace(/\n/g, "<br>")}</div></div>`;
    chat.appendChild(el);
    scrollBottom();
  }

  function addBotShell() {
    const el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = `<div class="avatar">LC</div><div class="msg-body"><div class="bubble"></div></div>`;
    chat.appendChild(el);
    scrollBottom();
    return el;
  }

  function addTyping() {
    const el = addBotShell();
    el.querySelector(".bubble").innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    return el;
  }

  function toolChip(name) {
    return `<span class="chip tool">${ICONS.part}<span>${TOOL_LABEL[name] || name}</span></span>`;
  }

  function citationChip(cite) {
    const label = `${cite.title || "知识库"}${cite.section ? " · " + cite.section : ""}`;
    const btn = document.createElement("button");
    btn.className = "chip cite";
    btn.innerHTML = `${BOOK_ICON}<span>${escapeHtml(label)}</span>`;
    btn.addEventListener("click", () => openCitation(cite));
    return btn;
  }

  function iconFor(system) {
    return ICONS[SYSTEM_ICON[system]] || ICONS.part;
  }

  function productCards(cards) {
    const wrap = document.createElement("div");
    wrap.className = "p-cards";
    cards.forEach((card) => {
      const el = document.createElement("article");
      el.className = "p-card";
      const price = typeof card.price === "number" && card.price > 0 ? `¥${card.price.toFixed(2)}` : "询价";
      el.innerHTML = `
        <div class="pc-top">
          <span class="pc-icon">${iconFor(card.system)}</span>
          <span class="pc-name">${escapeHtml(card.name)}</span>
        </div>
        <div class="pc-price">${price}<small>${escapeHtml(card.brand || "")}</small></div>
        <div class="pc-meta"><b>适配</b> ${escapeHtml(card.fitment || "通用 / 咨询客服")}</div>
        <div class="pc-meta"><b>品类</b> ${escapeHtml(card.category || "")}</div>
        <span class="pc-tag">${escapeHtml(card.warranty || "见保修政策")}</span>`;
      wrap.appendChild(el);
    });
    return wrap;
  }

  function renderBot(data) {
    const el = addBotShell();
    const body = el.querySelector(".msg-body");
    const bubble = el.querySelector(".bubble");
    bubble.innerHTML = renderMarkdown(data.answer || "抱歉，我暂时没有生成回答。");

    if (data.cards && data.cards.length) body.appendChild(productCards(data.cards));

    const meta = document.createElement("div");
    meta.className = "meta-row";
    (data.tools || []).forEach((tool) => {
      const span = document.createElement("span");
      span.innerHTML = toolChip(tool.name);
      meta.appendChild(span.firstElementChild);
    });
    (data.citations || []).forEach((cite) => meta.appendChild(citationChip(cite)));

    if (data.mode && data.mode !== "llm") {
      const mode = document.createElement("span");
      mode.className = "chip mode";
      mode.textContent = data.mode === "local-fallback" ? "本地降级回答" : "本地知识库回答";
      meta.appendChild(mode);
    }
    if (meta.children.length) body.appendChild(meta);
    scrollBottom();
  }

  function renderError(text) {
    const el = addBotShell();
    const bubble = el.querySelector(".bubble");
    bubble.classList.add("error");
    bubble.textContent = text;
    scrollBottom();
  }

  // ---------------- Citation modal ----------------
  function openCitation(cite) {
    $("#citeTitle").textContent = cite.title || "知识库";
    $("#citeSection").textContent = cite.section ? `章节：${cite.section}` : "";
    $("#citeSnippet").textContent = cite.snippet || "（无内容）";
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeModal() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }

  // ---------------- Send ----------------
  async function send(text, fromInput = false) {
    const message = (text || "").trim();
    if (!message || busy) return;
    busy = true;
    sendBtn.disabled = true;
    if (fromInput) {
      input.value = "";
      autoResize();
      input.focus();
    }
    addUserMessage(message);
    const typing = addTyping();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId || null, ...credPayload() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `服务返回 ${res.status}`);
      if (data.session_id) {
        sessionId = data.session_id;
        localStorage.setItem(SESSION_KEY, sessionId);
      }
      typing.remove();
      renderBot(data);
    } catch (err) {
      typing.remove();
      renderError(`连接失败：${err.message}。请确认服务已启动，或稍后重试。`);
    } finally {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  // ---------------- Init ----------------
  async function loadMeta() {
    try {
      const [metaRes, healthRes] = await Promise.all([fetch("/api/meta"), fetch("/api/health")]);
      const meta = await metaRes.json();
      const health = await healthRes.json();

      $("#productTotal").textContent = `${meta.products_count} 件`;
      const statProducts = $("#statProducts");
      if (statProducts) statProducts.textContent = meta.products_count;
      const modeLabel = cred ? (cred.model || "大模型") : meta.model;
      $("#modelInfo").innerHTML = `<span class="pulse"></span> ${escapeHtml(modeLabel)} · 知识库 ${health.vector_store.vectors} 条向量`;
      const badge = $("#modeBadge");
      badge.textContent = cred ? "大模型模式" : "本地检索模式";
      badge.classList.toggle("local", !cred);
      $("#kbStatus").textContent = `知识库已连接 · ${meta.products_count} 件商品`;

      const list = $("#categoryList");
      list.innerHTML = "";
      (meta.categories || []).forEach((cat) => {
        const icon = ICONS[SYSTEM_ICON[cat.name]] || ICONS.part;
        const btn = document.createElement("button");
        btn.className = "cat-item";
        btn.innerHTML = `${icon}<span>${escapeHtml(cat.name)}</span><b>${cat.count}</b>`;
        btn.addEventListener("click", () => {
          closeDrawer();
          send(`推荐几款${cat.name}的配件`);
        });
        list.appendChild(btn);
      });

      const quick = $("#quickList");
      quick.innerHTML = "";
      (meta.quick_questions || []).forEach((q) => {
        const btn = document.createElement("button");
        btn.className = "quick-btn";
        btn.textContent = q;
        btn.addEventListener("click", () => { closeDrawer(); send(q); });
        quick.appendChild(btn);
      });
    } catch (err) {
      $("#modelInfo").innerHTML = '<span class="pulse" style="background:#f87171"></span> 服务未连接';
      $("#kbStatus").textContent = "知识库连接失败";
      console.error(err);
    }
  }

  async function restoreHistory() {
    if (!sessionId) return;
    try {
      const res = await fetch(`/api/session/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.messages || !data.messages.length) return;
      hideWelcome();
      data.messages.forEach((msg) => {
        if (msg.role === "user") addUserMessage(msg.content);
        else {
          const el = addBotShell();
          el.querySelector(".bubble").innerHTML = renderMarkdown(msg.content);
        }
      });
      scrollBottom();
    } catch (err) {
      console.warn("恢复会话失败", err);
    }
  }

  function resetChat() {
    if (sessionId) {
      fetch("/api/session/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }).catch(() => {});
    }
    sessionId = "";
    localStorage.removeItem(SESSION_KEY);
    chat.innerHTML = "";
    window.location.reload();
  }

  function autoResize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  }

  function openDrawer() { sidebar.classList.add("open"); mask.classList.add("show"); }
  function closeDrawer() { sidebar.classList.remove("open"); mask.classList.remove("show"); }

  // ---------------- 模型配置弹窗 ----------------
  const llmModal = $("#llmModal");
  const llmStatus = $("#llmStatus");
  const llmBaseUrl = $("#llmBaseUrl");
  const llmApiKey = $("#llmApiKey");
  const llmModel = $("#llmModel");

  function setStatus(text, kind) {
    llmStatus.textContent = text || "";
    llmStatus.className = `llm-status${kind ? " " + kind : ""}`;
  }

  function openLlmModal() {
    const saved = cred || {};
    llmBaseUrl.value = saved.base_url || llmBaseUrl.dataset.default || "";
    llmApiKey.value = saved.api_key || "";
    llmModel.value = saved.model || llmModel.dataset.default || "";
    setStatus("");
    llmModal.classList.add("open");
    llmModal.setAttribute("aria-hidden", "false");
    (saved.api_key ? llmBaseUrl : llmApiKey).focus();
  }

  function closeLlmModal() {
    llmModal.classList.remove("open");
    llmModal.setAttribute("aria-hidden", "true");
  }

  function readForm() {
    return {
      base_url: llmBaseUrl.value.trim().replace(/\/+$/, ""),
      api_key: llmApiKey.value.trim(),
      model: llmModel.value.trim() || null,
    };
  }

  function applyCred(next) {
    cred = next;
    saveCred(next);
    const badge = $("#modeBadge");
    if (next) {
      badge.textContent = "大模型模式";
      badge.classList.remove("local");
      $("#modelInfo").innerHTML = `<span class="pulse"></span> ${escapeHtml(next.model || "默认模型")} · 知识库就绪`;
    } else {
      badge.textContent = "本地检索模式";
      badge.classList.add("local");
    }
  }

  async function testCred() {
    const form = readForm();
    if (!form.base_url || !form.api_key) {
      setStatus("请先填写接口地址和 API Key", "err");
      return;
    }
    setStatus("正在测试连接…", "busy");
    $("#llmTestBtn").disabled = true;
    try {
      const res = await fetch("/api/llm/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) setStatus(`连接成功　模型 ${data.model}`, "ok");
      else setStatus(data.error || "连接失败", "err");
    } catch (err) {
      setStatus(`请求失败：${err.message}`, "err");
    } finally {
      $("#llmTestBtn").disabled = false;
    }
  }

  function saveAndUse() {
    const form = readForm();
    if (form.base_url || form.api_key) {
      if (!form.base_url || !form.api_key) {
        setStatus("接口地址和 API Key 需要同时填写", "err");
        return;
      }
      applyCred(form);
    } else {
      applyCred(null);
    }
    closeLlmModal();
    setStatus("");
    loadMeta();
  }

  function clearCred() {
    applyCred(null);
    llmBaseUrl.value = llmBaseUrl.dataset.default || "";
    llmApiKey.value = "";
    llmModel.value = llmModel.dataset.default || "";
    setStatus("已清除本机保存的密钥", "ok");
  }

  async function initLlmDefaults() {
    try {
      const res = await fetch("/api/llm/defaults");
      const data = await res.json();
      llmBaseUrl.dataset.default = data.base_url || "";
      llmModel.dataset.default = data.model || "";
      if (!llmBaseUrl.value) llmBaseUrl.placeholder = data.base_url || "https://api.deepseek.com/v1";
      if (!llmModel.value) llmModel.placeholder = data.model || "deepseek-chat";
    } catch {
      /* 用 HTML 里的默认占位符即可 */
    }
  }

  // ---------------- Events ----------------
  sendBtn.addEventListener("click", () => send(input.value, true));

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input.value, true);
    }
  });
  input.addEventListener("input", autoResize);

  document.querySelectorAll(".sug-card").forEach((card) => {
    card.addEventListener("click", () => send(card.dataset.q));
  });

  $("#newChatBtn").addEventListener("click", resetChat);
  $("#menuBtn").addEventListener("click", openDrawer);
  mask.addEventListener("click", closeDrawer);
  $("#closeModal").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  // 模型配置弹窗
  $("#settingsBtn").addEventListener("click", openLlmModal);
  $("#closeLlm").addEventListener("click", closeLlmModal);
  $("#llmTestBtn").addEventListener("click", testCred);
  $("#llmClearBtn").addEventListener("click", clearCred);
  $("#llmSaveBtn").addEventListener("click", saveAndUse);
  $("#llmSkipBtn").addEventListener("click", () => { applyCred(null); closeLlmModal(); loadMeta(); });
  llmModal.addEventListener("click", (e) => { if (e.target === llmModal) closeLlmModal(); });
  llmApiKey.addEventListener("keydown", (e) => { if (e.key === "Enter") saveAndUse(); });

  loadMeta();
  restoreHistory();
  initLlmDefaults();

  // 首次进入（本机还没保存过密钥）自动弹出配置窗
  if (!cred) setTimeout(openLlmModal, 320);
})();
