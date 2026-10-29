/* =========================================================
   FinTrack — script.js
   Handles the interactive parts of the UI:
     • toast messages          • add / edit transaction pop-up
     • confirm dialogs         • notification bell + dismissing insights
     • charts (Chart.js)       • the FinTrack AI chat box
   Money calculations are NOT done here — Python does them.
   ========================================================= */

(function () {
  "use strict";

  // ---------- tiny helpers ----------
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const readJSON = (id) => {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  };
  const pad = (n) => String(n).padStart(2, "0");
  // Local (browser) date and time — so "today" is the student's own today.
  const localDate = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const localTime = (d = new Date()) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

  // ---------- toasts ----------
  const toastStack = $("#toastStack");
  // actions: optional list of {label, onClick} shown as small buttons inside the toast
  function toast(message, type, actions) {
    if (!toastStack) return;
    type = type || "success";
    const el = document.createElement("div");
    el.className = "toast toast-" + type;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    const icon = { success: "✅", error: "⚠️", warning: "⚠️", info: "ℹ️" }[type] || "✅";
    const body = document.createElement("div");
    body.className = "toast-body";
    const text = document.createElement("span");
    text.textContent = icon + "  " + message;
    body.appendChild(text);
    if (actions && actions.length) {
      const row = document.createElement("div");
      row.className = "toast-actions";
      actions.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = a.label;
        b.addEventListener("click", () => { el.remove(); a.onClick(); });
        row.appendChild(b);
      });
      body.appendChild(row);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss message");
    close.textContent = "✕";
    close.addEventListener("click", () => el.remove());
    el.append(body, close);
    toastStack.appendChild(el);
    setTimeout(() => el.remove(), actions && actions.length ? 15000 : (type === "error" ? 9000 : 6000));
  }

  // Small helper: POST a hidden form (used by Undo)
  function postForm(action, fields) {
    const f = document.createElement("form");
    f.method = "post";
    f.action = action;
    Object.entries(fields || {}).forEach(([k, v]) => {
      const i = document.createElement("input");
      i.type = "hidden"; i.name = k; i.value = v;
      f.appendChild(i);
    });
    document.body.appendChild(f);
    f.submit();
  }

  const flashes = readJSON("ft-flash") || [];
  const undoId = readJSON("ft-undo");
  let undoUsed = false;
  flashes.forEach(([category, message], i) => setTimeout(() => {
    // The first success message after a one-tap add gets an Undo button
    if (undoId && !undoUsed && category === "success") {
      undoUsed = true;
      toast(message, category, [{
        label: "Undo",
        onClick: () => postForm(`/transactions/${undoId}/delete`, { next: location.pathname + location.search }),
      }]);
    } else {
      toast(message, category);
    }
  }, i * 250));

  // ---------- transaction pop-up / forms ----------
  const txDialog = $("#txDialog");
  const txTitle = $("#txDialogTitle");

  function setupForm(form) {
    form.dataset.addAction = form.getAttribute("action");
    const typeRadios = $$('input[name="type"]', form);
    const groups = $$(".cat-group", form);
    const customField = $(".custom-cat", form);

    function syncType() {
      const kind = (typeRadios.find((r) => r.checked) || {}).value || "expense";
      const quick = $(".quick-section", form);
      if (quick) quick.hidden = kind !== "expense";     // Frequent items are expenses only
      groups.forEach((g) => {
        const show = g.dataset.kind === kind;
        g.hidden = !show;
        $$("input", g).forEach((i) => { i.disabled = !show; if (!show) i.checked = false; });
      });
      syncCustom();
    }
    function syncCustom() {
      const picked = $('input[name="category"]:checked:not(:disabled)', form);
      const isOther = picked && picked.value === "Other";
      customField.hidden = !isOther;
      if (!isOther) $('input[name="custom_category"]', form).value = "";
    }
    typeRadios.forEach((r) => r.addEventListener("change", syncType));
    $$('input[name="category"]', form).forEach((r) => r.addEventListener("change", syncCustom));
    form._sync = syncType;

    form.addEventListener("submit", (e) => {
      // One-tap Frequent buttons post straight to the server - no validation needed here
      if (e.submitter && e.submitter.hasAttribute("data-quick")) return;
      const errors = [];
      const amount = parseFloat($('input[name="amount"]', form).value);
      if (!(amount > 0)) errors.push("Enter an amount greater than ₹0.");
      const chosen = $('input[name="category"]:checked:not(:disabled)', form);
      if (!chosen) errors.push("Pick a category.");
      const d = $('input[name="tx_date"]', form).value;
      if (!d) errors.push("Choose a date.");
      else if (d > localDate()) errors.push("The date can't be in the future.");
      if (!$('input[name="tx_time"]', form).value) errors.push("Choose a time.");
      if (errors.length) {
        e.preventDefault();
        showErrors(form, errors);
      }
    });
  }

  function showErrors(form, errors) {
    const box = $(".form-errors", form);
    if (!box) return;
    box.innerHTML = "";
    const ul = document.createElement("ul");
    errors.forEach((msg) => { const li = document.createElement("li"); li.textContent = msg; ul.appendChild(li); });
    box.appendChild(ul);
    box.hidden = false;
  }

  function resetForm(form, dateOverride) {
    form.reset();
    form.setAttribute("action", form.dataset.addAction);
    form.classList.remove("is-edit");
    const now = new Date();
    $('input[name="tx_date"]', form).value = dateOverride || localDate(now);
    $('input[name="tx_date"]', form).max = localDate(now);
    $('input[name="tx_time"]', form).value = localTime(now);
    const box = $(".form-errors", form);
    if (box) box.hidden = true;
    form._sync();
  }

  function fillForm(form, v) {
    const typeRadio = $(`input[name="type"][value="${v.type}"]`, form);
    if (typeRadio) typeRadio.checked = true;
    form._sync();
    if (v.amount !== undefined) $('input[name="amount"]', form).value = v.amount;
    if (v.description !== undefined) $('input[name="description"]', form).value = v.description || "";
    if (v.tx_date) $('input[name="tx_date"]', form).value = v.tx_date;
    if (v.tx_time) $('input[name="tx_time"]', form).value = v.tx_time;
    const cat = $$('input[name="category"]', form).find((r) => !r.disabled && r.value === v.category);
    if (cat) cat.checked = true;
    form._sync();
    if (v.custom_category) $('input[name="custom_category"]', form).value = v.custom_category;
  }

  const modalForm = txDialog ? $(".tx-form", txDialog) : null;
  $$(".tx-form").forEach(setupForm);

  function openAdd(date) {
    if (!txDialog) return;
    txTitle.textContent = "Add Transaction";
    $(".btn-primary[data-save]", modalForm).textContent = "Save Transaction";
    resetForm(modalForm, date);
    txDialog.showModal();
    $('input[name="amount"]', modalForm).focus();
  }

  function openEdit(tx) {
    txTitle.textContent = "Edit Transaction";
    $(".btn-primary[data-save]", modalForm).textContent = "Save Changes";
    resetForm(modalForm);
    modalForm.setAttribute("action", `/transactions/${tx.id}/edit`);
    modalForm.classList.add("is-edit");
    fillForm(modalForm, tx);
    txDialog.showModal();
    $('input[name="amount"]', modalForm).focus();
  }

  document.addEventListener("click", (e) => {
    const addBtn = e.target.closest("[data-open-tx]");
    if (addBtn) {
      const inline = document.getElementById("add");
      if (inline && !addBtn.closest("#add") && !addBtn.dataset.date) {
        // The Transactions page already shows the form — jump to it.
        inline.scrollIntoView({ behavior: "smooth", block: "start" });
        $('input[name="amount"]', inline).focus({ preventScroll: true });
      } else {
        openAdd(addBtn.dataset.date);
      }
      return;
    }
    const editBtn = e.target.closest("[data-edit-tx]");
    if (editBtn) {
      try { openEdit(JSON.parse(editBtn.dataset.editTx)); } catch (err) { toast("Couldn't open that transaction.", "error"); }
      return;
    }
    if (e.target.closest("[data-close-dialog]")) {
      const dlg = e.target.closest("dialog");
      if (dlg) dlg.close();
      else if (txDialog && txDialog.open) txDialog.close();
    }
    // click on the dimmed backdrop closes the dialog
    if (e.target.tagName === "DIALOG") e.target.close();
  });

  // Re-open the form with the student's text if the server rejected it
  const draft = readJSON("ft-draft");
  if (draft && modalForm) {
    resetForm(modalForm);
    txTitle.textContent = draft.mode === "edit" ? "Edit Transaction" : "Add Transaction";
    fillForm(modalForm, draft.values);
    modalForm.setAttribute("action", draft.action);
    showErrors(modalForm, flashes.filter((f) => f[0] === "error").map((f) => f[1]));
    txDialog.showModal();
  } else {
    $$(".tx-form").forEach((f) => resetForm(f));
  }

  // ---------- confirmation dialog for forms with data-confirm ----------
  const confirmDialog = $("#confirmDialog");
  let pendingForm = null;
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.dataset && form.dataset.confirm && !form.dataset.confirmed) {
      e.preventDefault();
      pendingForm = form;
      $("#confirmText").textContent = form.dataset.confirm;
      confirmDialog.showModal();
    }
  });
  if (confirmDialog) {
    $("#confirmYes").addEventListener("click", () => {
      confirmDialog.close();
      if (pendingForm) { pendingForm.dataset.confirmed = "1"; pendingForm.submit(); }
    });
    $("#confirmNo").addEventListener("click", () => { confirmDialog.close(); pendingForm = null; });
  }

  // ---------- notification bell ----------
  const bellBtn = $("#bellBtn");
  const bellPanel = $("#bellPanel");
  if (bellBtn && bellPanel) {
    const setOpen = (open) => { bellPanel.hidden = !open; bellBtn.setAttribute("aria-expanded", String(open)); };
    bellBtn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(bellPanel.hidden); });
    document.addEventListener("click", (e) => { if (!bellPanel.hidden && !bellPanel.contains(e.target)) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !bellPanel.hidden) { setOpen(false); bellBtn.focus(); } });
  }

  // ---------- dismissing insights (saved in SQLite) ----------
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-dismiss]");
    if (!btn) return;
    const key = btn.dataset.dismiss;
    fetch("/insights/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => {
      if (!r.ok) throw new Error();
      $$("[data-insight-key]").forEach((el) => { if (el.dataset.insightKey === key) el.remove(); });
      const badge = $(".notif-count");
      if (badge) {
        const left = $$(".bell-item").length;
        if (left === 0) badge.remove(); else badge.textContent = left > 9 ? "9+" : left;
      }
      if (!$$(".bell-item").length && $(".bell-list")) {
        $(".bell-list").outerHTML = '<p class="bell-empty">You\'re all caught up.</p>';
      }
    }).catch(() => toast("Couldn't dismiss that right now.", "error"));
  });

  // ---------- charts ----------
  const page = readJSON("ft-page") || {};
  const chartFonts = { family: "Inter", size: 12 };

  const spendingCanvas = $("#spendingChart");
  if (spendingCanvas && window.Chart && page.series) {
    const ctx = spendingCanvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, 190);
    gradient.addColorStop(0, "rgba(31, 157, 107, 0.28)");
    gradient.addColorStop(1, "rgba(31, 157, 107, 0)");
    new Chart(spendingCanvas, {
      type: "line",
      data: {
        labels: page.series.labels,
        datasets: [{
          label: "Everyday Spending", data: page.series.expenses,
          borderColor: "#1F9D6B", backgroundColor: gradient, borderWidth: 2.5, fill: true, tension: 0.35,
          pointBackgroundColor: "#1F9D6B", pointBorderColor: "#fff", pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#102A22", padding: 10, bodyFont: { family: "JetBrains Mono", size: 12 },
            callbacks: { label: (c) => "₹" + c.parsed.y.toLocaleString("en-IN") },
          },
        },
        scales: {
          y: { beginAtZero: true, border: { display: false }, grid: { color: "#EFEBE3" },
               ticks: { font: { family: "JetBrains Mono", size: 11 }, color: "#6E7C76", maxTicksLimit: 5,
                        callback: (v) => "₹" + v.toLocaleString("en-IN") } },
          x: { border: { display: false }, grid: { display: false }, ticks: { font: chartFonts, color: "#6E7C76" } },
        },
      },
    });
  }

  const donutCanvas = $("#categoryChart");
  if (donutCanvas && window.Chart && page.labels) {
    new Chart(donutCanvas, {
      type: "doughnut",
      data: { labels: page.labels, datasets: [{ data: page.values, backgroundColor: page.colors, borderColor: "#fff", borderWidth: 3, hoverOffset: 6 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "66%",
        plugins: {
          legend: { position: "bottom", labels: { font: chartFonts, boxWidth: 10, usePointStyle: true } },
          tooltip: { backgroundColor: "#102A22", padding: 10, bodyFont: { family: "JetBrains Mono", size: 12 },
                     callbacks: { label: (c) => ` ${c.label}: ₹${c.parsed.toLocaleString("en-IN")}` } },
        },
      },
    });
  }

  // ---------- FinTrack AI chat ----------
  const chatForm = $("#chatForm");
  if (chatForm) {
    const log = $("#chatLog");
    const input = $("#chatInput");

    const addMsg = (text, who) => {
      const div = document.createElement("div");
      div.className = "msg msg-" + who;
      div.textContent = text;           // textContent keeps everything safely escaped
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      return div;
    };

    async function ask(question) {
      question = question.trim();
      if (!question) return;
      addMsg(question, "user");
      const thinking = addMsg("…", "bot");
      try {
        const res = await fetch("/ai/ask", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });
        if (!res.ok) throw new Error();
        const data = await res.json();
        thinking.textContent = data.answer;
      } catch (err) {
        thinking.textContent = "Sorry, I couldn't answer that just now. Please try again.";
      }
      log.scrollTop = log.scrollHeight;
    }

    chatForm.addEventListener("submit", (e) => { e.preventDefault(); const q = input.value; input.value = ""; ask(q); });
    $$("[data-ask]").forEach((b) => b.addEventListener("click", () => ask(b.dataset.ask)));
    if (chatForm.dataset.initial) ask(chatForm.dataset.initial);
  }
})();