(() => {
  const $ = (sel) => document.querySelector(sel);

  let sessionId = null;
  let currentRows = [];
  let lastLookupResults = [];
  let attachedParts = [];
  let currentOffer = "";
  let lastSizeResults = [];

  function setSizeStatus(msg, kind = "") {
    const el = $("#size-status");
    if (!el) return;
    el.textContent = msg;
    el.className = `hint ${kind}`.trim();
  }

  function parseKwLines(text) {
    const lines = [];
    for (const raw of text.split(/\r?\n|;/)) {
      const s = raw.trim();
      if (!s) continue;
      // 22,4 or 22;4 → kw,qty
      let m = s.match(/^(\d+(?:[.,]\d+)?)\s*[,;]\s*(\d+)\s*$/i);
      if (m) {
        lines.push({ kw: Number(m[1].replace(",", ".")), qty: Number(m[2]) });
        continue;
      }
      // 22/30 → kw/hp
      m = s.match(/^(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)\s*(?:hp)?$/i);
      if (m) {
        lines.push({
          kw: Number(m[1].replace(",", ".")),
          hp: Number(m[2].replace(",", ".")),
        });
        continue;
      }
      // 22 kW Qty 4
      m = s.match(/^(\d+(?:[.,]\d+)?)\s*k?w?\s*(?:qty\s*)?(\d+)?$/i);
      if (m) {
        const row = { kw: Number(m[1].replace(",", ".")) };
        if (m[2]) row.qty = Number(m[2]);
        lines.push(row);
        continue;
      }
      m = s.match(/(\d+(?:[.,]\d+)?)/);
      if (m) lines.push({ kw: Number(m[1].replace(",", ".")) });
    }
    return lines;
  }

  function renderSizeResults(payload) {
    lastSizeResults = payload.results || [];
    const wrap = $("#size-results-wrap");
    const tbody = $("#size-table tbody");
    tbody.innerHTML = "";
    wrap.hidden = false;
    $("#size-meta").textContent = `${payload.ok_count || 0} / ${payload.count || 0} matched`;
    lastSizeResults.forEach((row) => {
      const rec = row.recommended || {};
      const pf = row.passive_filters || {};
      const tr = document.createElement("tr");
      if (!row.ok) tr.classList.add("row-miss");
      const drive = row.ok ? (rec.reference || "No match") : "No match";
      const family = row.ok ? (rec.family || "") : "";
      const duty = row.ok ? (rec.duty || "") : "";
      const filter5 = row.ok
        ? (pf.preferred_filter || pf.filter_5pct || "")
        : "";
      tr.innerHTML = `
        <td>${escapeHtml(row.input?.kw ?? "")}</td>
        <td>${escapeHtml(row.input?.qty ?? "")}</td>
        <td>${escapeHtml(duty)}</td>
        <td class="mono">${escapeHtml(drive)}</td>
        <td>${escapeHtml(family)}</td>
        <td class="mono">${escapeHtml(filter5)}</td>
      `;
      tbody.appendChild(tr);
    });
    const refs = recommendedRefs();
    $("#btn-size-go").disabled = !refs.length;
  }

  function recommendedRefs() {
    const refs = [];
    const seen = new Set();
    for (const r of lastSizeResults || []) {
      if (!r.ok || !r.recommended?.reference) continue;
      const drive = String(r.recommended.reference).toUpperCase();
      if (!seen.has(drive)) {
        seen.add(drive);
        refs.push(drive);
      }
      const pf = r.passive_filters || {};
      const filter = String(pf.preferred_filter || pf.filter_5pct || "").toUpperCase();
      if (filter && !seen.has(filter)) {
        seen.add(filter);
        refs.push(filter);
      }
    }
    return refs;
  }

  function preferredFilterRefs() {
    const refs = [];
    const seen = new Set();
    for (const r of lastSizeResults || []) {
      if (!r.ok) continue;
      const pf = r.passive_filters || {};
      const filter = String(pf.preferred_filter || pf.filter_5pct || "").toUpperCase();
      if (filter && !seen.has(filter)) {
        seen.add(filter);
        refs.push(filter);
      }
    }
    return refs;
  }

  async function downloadMergedPdf(parts) {
    const res = await fetch("/api/lookup/merged-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: parts.map((p) => ({
          part_number: p.part_number,
          documents: (p.documents || []).slice(0, 1),
        })),
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(formatDetail(data.detail) || "Combined datasheet failed");
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = /filename=\"([^\"]+)\"/i.exec(cd);
    const filename = match ? match[1] : "Combined_Product_Datasheets.pdf";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function attachParts(parts) {
    const recByPn = {};
    (lastSizeResults || []).forEach((row) => {
      const ref = row.recommended?.reference;
      if (!ref) return;
      recByPn[String(ref).toUpperCase()] = row.recommended || {};
    });
    const res = await fetch("/api/attached", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: parts.map((p) => {
          const pn = String(p.part_number || "").toUpperCase();
          const rec = recByPn[pn] || {};
          const out = {
            part_number: p.part_number,
            title: p.title,
            url: p.url,
            documents: p.documents || [],
          };
          if (rec.source_page) out.source_page = String(rec.source_page);
          if (rec.family) out.family = rec.family;
          if (rec.ip_note) out.ip_note = rec.ip_note;
          return out;
        }),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(formatDetail(data.detail) || "Attach failed");
    attachedParts = data.parts || [];
    currentOffer = data.offer || "";
    refreshAttachedUI();
    return data;
  }

  $("#size-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const lines = parseKwLines($("#kw-input").value);
    if (!lines.length) {
      setSizeStatus("Enter at least one kW value.", "error");
      return;
    }
    setSizeStatus("Looking up drives & filters from catalog…");
    $("#btn-size-go").disabled = true;
    try {
      const res = await fetch("/api/size", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lines,
          duty: "AUTO",
          cabinet: $("#size-cabinet").checked,
          hz: Number($("#size-hz").value || 50),
          harmonics: $("#size-harmonics").value || "standard",
          supply_pref: "380-480",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail) || "Sizing failed");
      renderSizeResults(data);
      setSizeStatus(
        `Recommended ${data.ok_count}/${data.count} drive(s). Next: look up, combine datasheet & attach.`,
        "ok"
      );
    } catch (err) {
      setSizeStatus(err.message || String(err), "error");
    }
  });

  $("#btn-size-go").addEventListener("click", async () => {
    const refs = recommendedRefs();
    if (!refs.length) {
      setSizeStatus("Recommend drives first.", "error");
      return;
    }
    $("#btn-size-go").disabled = true;
    try {
      setSizeStatus(`Looking up ${refs.length} part(s) on Schneider…`);
      const lookupRes = await fetch("/api/lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ part_numbers: refs.join("\n") }),
      });
      const lookupData = await lookupRes.json();
      if (!lookupRes.ok) throw new Error(formatDetail(lookupData.detail) || "Lookup failed");
      lastLookupResults = lookupData.results || [];

      const withDocs = lastLookupResults.filter((p) => (p.documents || []).length);
      if (!withDocs.length) {
        throw new Error("Lookup found no Product Datasheets to attach.");
      }

      const filterWanted = preferredFilterRefs();
      const foundPn = new Set(
        withDocs.map((p) => String(p.part_number || "").toUpperCase())
      );
      const missingFilters = filterWanted.filter((f) => !foundPn.has(f));
      const filterOk = filterWanted.filter((f) => foundPn.has(f));

      setSizeStatus(
        `Building combined Product Datasheet for ${withDocs.length} part(s)` +
          (filterOk.length ? ` (includes ${filterOk.length} × 5% filter datasheet)` : "") +
          (missingFilters.length
            ? ` — warn: no datasheet for filter(s) ${missingFilters.join(", ")}`
            : "") +
          "…"
      );
      await downloadMergedPdf(withDocs);

      setSizeStatus("Attaching Product Datasheets for compliance…");
      await attachParts(withDocs);

      let doneMsg = `Done — ${withDocs.length} datasheet(s) attached (drives`;
      if (filterOk.length) doneMsg += ` + ${filterOk.length} filter(s)`;
      doneMsg += "). Continue on Step 2.";
      if (missingFilters.length) {
        doneMsg += ` Missing filter datasheet: ${missingFilters.join(", ")}.`;
      }
      setSizeStatus(doneMsg, missingFilters.length ? "error" : "ok");
      document.querySelector('.tab[data-tab="compliance"]').click();
    } catch (err) {
      setSizeStatus(err.message || String(err), "error");
    } finally {
      $("#btn-size-go").disabled = !recommendedRefs().length;
    }
  });

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#panel-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "compliance") refreshAttachedUI();
    });
  });

  function setComplianceStatus(msg, kind = "") {
    const el = $("#compliance-status");
    el.textContent = msg;
    el.className = `hint ${kind}`.trim();
  }

  function setLookupStatus(msg, kind = "") {
    const el = $("#lookup-status");
    el.textContent = msg;
    el.className = `hint ${kind}`.trim();
  }

  function setExportEnabled(on) {
    ["btn-regenerate", "btn-save", "btn-xlsx", "btn-docx"].forEach((id) => {
      $(`#${id}`).disabled = !on;
    });
  }

  function statusClass(status) {
    const s = (status || "").toUpperCase();
    if (s === "YES") return "status-yes";
    if (s === "NO") return "status-no";
    return "status-na";
  }

  function renderDraft(rows, meta) {
    currentRows = rows.map((r) => ({ ...r }));
    const wrap = $("#draft-wrap");
    const tbody = $("#draft-table tbody");
    tbody.innerHTML = "";
    wrap.hidden = false;
    $("#draft-meta").textContent = meta;

    rows.forEach((row, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="clause"></td>
        <td class="req"></td>
        <td class="status-cell"></td>
        <td></td>
        <td class="page"></td>
        <td class="source-doc"></td>
      `;
      tr.children[0].textContent = row.clause_id || "";
      tr.children[1].textContent = row.requirement || "";

      const sel = document.createElement("select");
      sel.className = `status-select ${statusClass(row.status)}`;
      ["Yes", "No", "N/A"].forEach((opt) => {
        const o = document.createElement("option");
        o.value = opt;
        o.textContent = opt === "Yes" ? "Yes — complies" : opt === "No" ? "No — does not" : "N/A";
        if ((row.status || "Yes") === opt) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", () => {
        currentRows[idx].status = sel.value;
        sel.className = `status-select ${statusClass(sel.value)}`;
      });
      tr.children[2].appendChild(sel);

      const ta = document.createElement("textarea");
      ta.className = "remark-edit";
      ta.value = row.remarks || "";
      ta.addEventListener("input", () => {
        currentRows[idx].remarks = ta.value;
      });
      tr.children[3].appendChild(ta);

      tr.children[4].textContent = row.source_page ?? "";
      tr.children[5].textContent = row.source_document || "";
      tbody.appendChild(tr);
    });
  }

  function refreshAttachedUI() {
    const el = $("#attached-list");
    const offerEl = $("#offer-line");
    if (!attachedParts.length) {
      el.textContent = "None yet — complete Step 1 and click Attach for compliance.";
      el.classList.add("empty");
      offerEl.hidden = true;
      return;
    }
    el.classList.remove("empty");
    el.innerHTML = attachedParts
      .map((p) => {
        const docs = p.documents || [];
        const name = docs[0] ? docs[0].file_name || docs[0].title : "missing Product Datasheet";
        return `<div class="attached-item"><code>${escapeHtml(p.part_number)}</code> — Product Datasheet: <span class="wrap">${escapeHtml(name)}</span></div>`;
      })
      .join("");
    currentOffer = attachedParts.map((p) => p.part_number).join("/");
    offerEl.hidden = false;
    offerEl.textContent = `Offered parts (auto): ${currentOffer}`;
  }

  async function loadAttached() {
    try {
      const res = await fetch("/api/attached");
      const data = await res.json();
      attachedParts = data.parts || [];
      currentOffer = data.offer || "";
      refreshAttachedUI();
    } catch (_) {
      /* ignore */
    }
  }

  async function maybeUploadKnowledge() {
    const f = $("#knowledge-file").files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    await fetch("/api/knowledge/upload", { method: "POST", body: fd });
  }

  $("#compliance-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("#pdf-file").files[0];
    if (!file) return;
    if (!attachedParts.length) {
      setComplianceStatus("Attach Product Datasheets from Step 1 first.", "error");
      return;
    }
    setComplianceStatus("Extracting VFD clauses and drafting Yes/No/N/A answers…");
    setExportEnabled(false);
    try {
      await maybeUploadKnowledge();
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/compliance/generate", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail) || "Generate failed");
      sessionId = data.session_id;
      currentOffer = data.drive_family || currentOffer;
      renderDraft(
        data.rows,
        `${data.source_file} · Parts: ${data.drive_family} · ${data.clause_count} clauses`
      );
      setExportEnabled(true);
      setComplianceStatus("Draft ready — review Complies? before export.", "ok");
    } catch (err) {
      setComplianceStatus(err.message || String(err), "error");
    }
  });

  $("#btn-regenerate").addEventListener("click", async () => {
    if (!sessionId) return;
    setComplianceStatus("Regenerating…");
    try {
      const fd = new FormData();
      fd.append("session_id", sessionId);
      const res = await fetch("/api/compliance/regenerate", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail) || "Regenerate failed");
      renderDraft(data.rows, `Regenerated · Parts: ${data.drive_family} · ${data.clause_count} clauses`);
      setComplianceStatus(`Regenerated for ${data.drive_family}.`, "ok");
    } catch (err) {
      setComplianceStatus(err.message || String(err), "error");
    }
  });

  $("#btn-save").addEventListener("click", async () => {
    if (!sessionId) return;
    try {
      const res = await fetch("/api/compliance/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          rows: currentRows,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail) || "Save failed");
      setComplianceStatus("Edits saved to session.", "ok");
    } catch (err) {
      setComplianceStatus(err.message || String(err), "error");
    }
  });

  async function download(fmt) {
    if (!sessionId) return;
    await fetch("/api/compliance/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, rows: currentRows }),
    });
    window.location.href = `/api/compliance/export/${fmt}?session_id=${encodeURIComponent(sessionId)}`;
  }

  $("#btn-xlsx").addEventListener("click", () => download("xlsx"));
  $("#btn-docx").addEventListener("click", () => download("docx"));

  function badgeClass(status) {
    const s = (status || "").toLowerCase();
    if (s === "found") return "found";
    if (s === "ambiguous") return "ambiguous";
    return "notfound";
  }

  function renderLookupResults(results) {
    const grid = $("#lookup-results");
    grid.innerHTML = "";
    results.forEach((item, idx) => {
      const card = document.createElement("article");
      card.className = "card result-card";
      const docs = item.documents || [];
      const missing = item.datasheet_missing || !docs.length;
      const docList = docs
        .map((d) => {
          const href = `/api/lookup/document?url=${encodeURIComponent(d.url)}&filename=${encodeURIComponent(d.file_name || d.title || "Product_Datasheet.pdf")}`;
          return `<li><span class="doc-type">${escapeHtml(d.doc_type || "Product Datasheet")}</span><a class="doc-link" href="${escapeAttr(href)}">${escapeHtml(d.title || d.file_name)}</a></li>`;
        })
        .join("");
      const highlights = (item.highlights || [])
        .slice(0, 4)
        .map((h) => `<li>${escapeHtml(h)}</li>`)
        .join("");
      card.innerHTML = `
        <div class="card-top">
          <label class="pick"><input type="checkbox" class="part-pick" data-idx="${idx}" ${item.status === "Found" && docs.length ? "checked" : ""} /> Select</label>
          <span class="badge ${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
        </div>
        <div class="part-ref">${escapeHtml(item.part_number)}${item.country ? " · " + escapeHtml(item.country) : ""}</div>
        <h3>${escapeHtml(item.title || item.part_number)}</h3>
        <p class="desc">${escapeHtml(item.description || "")}</p>
        ${highlights ? `<ul class="highlights">${highlights}</ul>` : ""}
        <div class="docs-block">
          <div class="docs-title">Product Datasheet</div>
          ${
            docs.length
              ? `<ul class="docs-list">${docList}</ul>`
              : `<p class="hint warn-text">Product Datasheet not found for this part. Open Schneider to verify.</p>`
          }
        </div>
        <div class="actions">
          ${item.url ? `<a class="btn ghost" href="${escapeAttr(item.url)}" target="_blank" rel="noopener">Open on Schneider</a>` : ""}
        </div>
      `;
      if (missing) card.classList.add("missing-datasheet");
      grid.appendChild(card);
    });
    $("#btn-zip").disabled = !results.some((r) => (r.documents || []).length);
  }

  function selectedResults() {
    const picks = [...document.querySelectorAll(".part-pick:checked")].map((el) => Number(el.dataset.idx));
    if (!picks.length) return lastLookupResults.filter((r) => (r.documents || []).length);
    return picks.map((i) => lastLookupResults[i]).filter(Boolean);
  }

  $("#lookup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#part-input").value.trim();
    if (!text) return;
    setLookupStatus("Searching Schneider for products & Product Datasheet…");
    $("#lookup-results").innerHTML = "";
    $("#btn-zip").disabled = true;
    try {
      const res = await fetch("/api/lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ part_numbers: text }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail) || "Lookup failed");
      lastLookupResults = data.results || [];
      renderLookupResults(lastLookupResults);
      const withDocs = lastLookupResults.filter((r) => (r.documents || []).length).length;
      setLookupStatus(`Loaded ${data.count} result(s) · ${withDocs} with Product Datasheet.`, "ok");
    } catch (err) {
      setLookupStatus(err.message || String(err), "error");
    }
  });

  $("#btn-zip").addEventListener("click", async () => {
    const parts = selectedResults().filter((p) => (p.documents || []).length);
    if (!parts.length) {
      setLookupStatus("No Product Datasheet selected to combine.", "error");
      return;
    }
    setLookupStatus("Building combined Product Datasheet PDF…");
    try {
      await downloadMergedPdf(parts);
      setLookupStatus(
        `Combined Product Datasheet downloaded (cover + ${parts.length} datasheet(s)).`,
        "ok"
      );
    } catch (err) {
      setLookupStatus(err.message || String(err), "error");
    }
  });

  function formatDetail(detail) {
    if (!detail) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    return JSON.stringify(detail);
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replaceAll("'", "&#39;");
  }

  $("#learn-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("#learn-file").files[0];
    const status = $("#learn-status");
    if (!file) {
      status.textContent = "Choose an Excel or Word file.";
      status.className = "hint error";
      return;
    }
    status.textContent = "Importing corrections into the rule book…";
    status.className = "hint";
    try {
      const fd = new FormData();
      fd.append("file", file);
      const offer = ($("#learn-offer").value || "").trim();
      if (offer) fd.append("offer", offer);
      const res = await fetch("/api/compliance/import-corrections", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail) || "Import failed");
      $("#learn-report").hidden = false;
      $("#learn-meta").textContent =
        `Applied ${data.applied}/${data.parsed} matched · unmatched ${data.unmatched}` +
        ` · gold DB +${data.reference_appended ?? 0}` +
        ` · sources updated ${data.sources_updated ?? 0}` +
        ` · sources ignored ${data.sources_ignored ?? 0}` +
        ` · family ${data.family}`;
      $("#learn-json").textContent = JSON.stringify(
        {
          policy: data.policy,
          reference_appended: data.reference_appended,
          reference_db: data.reference_db,
          sources_updated: data.sources_updated,
          sources_ignored: data.sources_ignored,
          applied_rows: data.applied_rows,
          unmatched_rows: data.unmatched_rows,
          gold_log: data.gold_log,
        },
        null,
        2
      );
      status.textContent =
        "Saved. Fix is in the rule book and gold reference DB — same error should not repeat on the next generate.";
      status.className = "hint ok";
    } catch (err) {
      status.textContent = err.message || String(err);
      status.className = "hint error";
    }
  });

  loadAttached();
})();
