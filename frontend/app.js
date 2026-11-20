/* 客服工单主题分析前端：原生 JS + 内联 SVG，无构建依赖 */
const API = "";
const SERIES = [
  "--series-1", "--series-2", "--series-3", "--series-4",
  "--series-5", "--series-6", "--series-7", "--series-8",
];
const UNKNOWN_COLOR = "var(--unknown)";

const cssVar = (name) => getComputedStyle(document.querySelector(".viz-root")).getPropertyValue(name).trim();

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.detail ? JSON.stringify(data.detail) : `HTTP ${res.status}`);
  return data;
}

const show = (id, data) => {
  const el = document.getElementById(id);
  el.classList.remove("hidden");
  el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = (s) => (s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—");
const badge = (text, cls) => `<span class="badge ${cls}">${esc(text)}</span>`;

// ---------------- 页签与主题 ----------------
document.getElementById("tabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("active", p.id === `tab-${tab.dataset.tab}`));
  if (tab.dataset.tab === "tickets") loadTickets();
  if (tab.dataset.tab === "versions") loadVersions();
  if (tab.dataset.tab === "lineage") loadLineageView();
  if (tab.dataset.tab === "unknown") loadUnknownView();
  if (tab.dataset.tab === "alerts") loadAlertView();
});
document.getElementById("themeSwitch").addEventListener("change", (e) => {
  document.documentElement.dataset.theme = e.target.checked ? "dark" : "light";
});

// ---------------- SVG 折线图（十字线 + tooltip） ----------------
function lineChart(containerId, legendId, days, labels, series) {
  const container = document.getElementById(containerId);
  const W = Math.max(720, days.length * 46), H = 320;
  const M = { top: 16, right: 20, bottom: 42, left: 44 };
  const iw = W - M.left - M.right, ih = H - M.top - M.bottom;
  const maxV = Math.max(1, ...labels.flatMap((l) => series[l] || []));
  const x = (i) => M.left + (days.length <= 1 ? iw / 2 : (i * iw) / (days.length - 1));
  const y = (v) => M.top + ih - (v / maxV) * ih;

  const colorOf = (l) => (l === "unknown" ? UNKNOWN_COLOR : `var(${SERIES[labels.filter((x) => x !== "unknown").indexOf(l)] ?? "--series-8"})`);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="主题趋势折线图" width="100%">`;
  // 网格 + Y 轴刻度
  const ticks = 4;
  for (let t = 0; t <= ticks; t++) {
    const v = Math.round((maxV * t) / ticks);
    const yy = y(v);
    svg += `<line class="grid-line" x1="${M.left}" y1="${yy}" x2="${W - M.right}" y2="${yy}"/>`;
    svg += `<text class="axis-text" x="${M.left - 8}" y="${yy + 4}" text-anchor="end">${v}</text>`;
  }
  svg += `<line class="axis-line" x1="${M.left}" y1="${M.top + ih}" x2="${W - M.right}" y2="${M.top + ih}"/>`;
  // X 轴标签（稀疏显示）
  const step = Math.max(1, Math.ceil(days.length / 10));
  days.forEach((d, i) => {
    if (i % step === 0 || i === days.length - 1)
      svg += `<text class="axis-text" x="${x(i)}" y="${H - 14}" text-anchor="middle">${d.slice(5)}</text>`;
  });
  // 折线
  labels.forEach((l) => {
    const pts = (series[l] || []).map((v, i) => `${x(i)},${y(v)}`).join(" ");
    svg += `<polyline class="series-line" stroke="${colorOf(l)}" points="${pts}"/>`;
    (series[l] || []).forEach((v, i) => {
      if (v > 0) svg += `<circle class="series-dot" cx="${x(i)}" cy="${y(v)}" r="4" fill="${colorOf(l)}" data-label="${esc(l)}" data-value="${v}" data-day="${esc(days[i])}"/>`;
    });
  });
  // 十字线
  svg += `<line id="crosshair" x1="0" y1="${M.top}" x2="0" y2="${M.top + ih}" stroke="${cssVar("--baseline")}" stroke-dasharray="4 3" style="display:none"/>`;
  svg += `</svg>`;
  container.innerHTML = svg;

  document.getElementById(legendId).innerHTML = labels.map((l) =>
    `<span class="legend-item"><span class="legend-line" style="border-color:${colorOf(l)}"></span>${esc(l)}${l === "unknown" ? "（未知主题）" : ""}</span>`
  ).join("");

  // 悬停层
  const tip = ensureTooltip();
  const cross = container.querySelector("#crosshair");
  container.querySelectorAll("circle").forEach((dot) => {
    dot.addEventListener("mouseenter", (e) => {
      tip.style.display = "block";
      tip.innerHTML = `<div class="tt-title">${esc(dot.dataset.day)}</div>
        <div class="tt-row"><span class="legend-swatch" style="background:${colorOf(dot.dataset.label)}"></span>${esc(dot.dataset.label)}：<b>${dot.dataset.value}</b></div>`;
      tip.style.left = `${e.clientX + 12}px`;
      tip.style.top = `${e.clientY + 12}px`;
      cross.style.display = "";
      cross.setAttribute("x1", dot.getAttribute("cx"));
      cross.setAttribute("x2", dot.getAttribute("cx"));
    });
    dot.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.style.display = "none"; });
  });
}

function ensureTooltip() {
  let tip = document.getElementById("chartTooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "chartTooltip";
    tip.className = "tooltip";
    document.body.appendChild(tip);
  }
  return tip;
}

// ---------------- 分布条形图 + 表格（对比度 WARN 的兜底视图） ----------------
function distributionView(dist) {
  const entries = Object.entries(dist).sort((a, b) => b[1] - a[1]);
  const known = entries.filter(([k]) => k !== "unknown");
  const colorOf = (k, i) => (k === "unknown" ? UNKNOWN_COLOR : `var(${SERIES[i % SERIES.length]})`);
  const max = Math.max(...entries.map(([, v]) => v), 0.01);
  const svgW = 760, rowH = 34, M = { left: 110, right: 60 };
  let svg = `<svg viewBox="0 0 ${svgW} ${entries.length * rowH + 10}" width="100%" role="img" aria-label="主题分布条形图">`;
  entries.forEach(([k, v], i) => {
    const yy = i * rowH + 8;
    const w = (v / max) * (svgW - M.left - M.right);
    svg += `<text class="axis-text" x="${M.left - 8}" y="${yy + 15}" text-anchor="end">${esc(k)}</text>`;
    svg += `<rect class="bar" x="${M.left}" y="${yy}" width="${Math.max(w, 2)}" height="20" fill="${colorOf(k, known.findIndex(([x]) => x === k))}"/>`;
    svg += `<text class="axis-text" x="${M.left + w + 6}" y="${yy + 15}">${(v * 100).toFixed(1)}%</text>`;
  });
  svg += `</svg>`;
  document.getElementById("distChart").innerHTML = svg;
  document.querySelector("#distTable tbody").innerHTML = entries.map(([k, v], i) => `
    <tr><td>${esc(k)}${k === "unknown" ? badge("未知", "muted") : ""}</td>
    <td class="num">${(v * 100).toFixed(1)}%</td>
    <td><div class="dist-bar" style="width:${(v / max) * 100}%;background:${colorOf(k, known.findIndex(([x]) => x === k))}"></div></td></tr>`
  ).join("");
}

// ---------------- 总览 ----------------
async function loadOverview() {
  const days = Number(document.getElementById("trendDays").value);
  const [trend, drift, unknown] = await Promise.all([
    api(`/api/analytics/trend?days=${days}`),
    api("/api/analytics/drift?limit=5"),
    api("/api/analytics/unknown-rate?days=7"),
  ]);
  lineChart("trendChart", "trendLegend", trend.days, trend.labels, trend.series);
  const latest = drift[0];
  if (latest) distributionView(latest.distribution);
  else document.getElementById("distChart").innerHTML = `<p class="subtitle">暂无漂移快照，点击「立即检测漂移」生成</p>`;

  const driftCls = latest?.drift_score == null ? "" : latest.drift_score >= 0.15 ? "critical" : latest.drift_score >= 0.08 ? "warning" : "good";
  document.getElementById("statGrid").innerHTML = `
    <div class="stat-tile"><div class="label">窗口样本量</div><div class="value">${latest?.total ?? 0}</div><div class="delta">最近漂移窗口</div></div>
    <div class="stat-tile"><div class="label">漂移分数 (JS 散度)</div><div class="value ${driftCls}">${latest?.drift_score ?? "—"}</div><div class="delta">≥0.15 视为显著漂移</div></div>
    <div class="stat-tile"><div class="label">未知主题率（7天）</div><div class="value ${unknown.unknown_rate >= 0.1 ? "critical" : "good"}">${(unknown.unknown_rate * 100).toFixed(1)}%</div><div class="delta">${unknown.unknown} / ${unknown.total} 条</div></div>
    <div class="stat-tile"><div class="label">显著变化主题数</div><div class="value warning">${latest?.drifted_topics?.length ?? 0}</div><div class="delta">占比变化 ≥10%</div></div>`;

  document.querySelector("#driftTable tbody").innerHTML = drift.length ? drift.map((s) => `
    <tr><td>${fmtTime(s.window_start)}<br>~ ${fmtTime(s.window_end)}</td>
    <td class="num">${s.total}</td>
    <td class="num">${s.drift_score ?? "—"}</td>
    <td>${(s.drifted_topics || []).map((d) => `${esc(d.topic)} (${d.delta >= 0 ? "+" : ""}${(d.delta * 100).toFixed(1)}%)`).join("<br>") || "—"}</td></tr>`
  ).join("") : `<tr><td colspan="4" class="subtitle">暂无数据</td></tr>`;
}
document.getElementById("trendDays").addEventListener("change", loadOverview);
document.getElementById("btnDrift").addEventListener("click", async () => {
  await api("/api/analytics/drift/detect?window_days=7", { method: "POST" });
  loadOverview();
});
document.getElementById("btnEvaluate").addEventListener("click", async () => {
  const alerts = await api("/api/analytics/alerts/evaluate?window_days=7", { method: "POST" });
  alert(alerts.length ? `新产生 ${alerts.length} 条告警` : "没有新告警（规则未命中或已存在相同告警）");
  loadOverview();
});

// ---------------- 工单与证据 ----------------
async function loadTickets() {
  const q = document.getElementById("ticketSearch").value.trim();
  let tickets = await api("/api/tickets?limit=500");
  if (q) tickets = tickets.filter((t) => t.ticket_no.includes(q) || t.title.includes(q));
  document.querySelector("#ticketTable tbody").innerHTML = tickets.map((t) => `
    <tr><td>${esc(t.ticket_no)}</td><td>${esc(t.title)}</td>
    <td>${badge(t.status, t.status === "updated" ? "warning" : "muted")}</td>
    <td>${t.is_corrected ? badge("已纠正", "good") : "—"}</td>
    <td>${fmtTime(t.event_time)}</td>
    <td><button class="btn btn-small" onclick="showEvidence(${t.id})">样本证据</button></td></tr>`
  ).join("") || `<tr><td colspan="6" class="subtitle">无工单</td></tr>`;
}
document.getElementById("btnLoadTickets").addEventListener("click", loadTickets);
document.getElementById("ticketSearch").addEventListener("input", loadTickets);

window.showEvidence = async (id) => {
  const ev = await api(`/api/tickets/${id}/evidence`);
  document.getElementById("evidenceCard").classList.remove("hidden");
  document.getElementById("evidenceBody").innerHTML = `
    <p class="subtitle">工单号 <b>${esc(ev.ticket_no)}</b>，共 ${ev.classifications.length} 条分类记录（历史结果全部保留）</p>
    ${ev.classifications.map((c) => `
      <div class="evidence-block ${c.source === "manual" ? "manual" : ""}">
        <b>${esc(c.version)}</b>（${c.type} / 来源 ${c.source}）
        → 标签：${c.labels.map((l) => badge(l, l === "unknown" ? "muted" : "info")).join(" ")}
        置信度 ${c.confidence} ${c.is_unknown ? badge("未知", "warning") : ""}
        <pre class="result-box">${esc(JSON.stringify(c.evidence, null, 2))}</pre>
      </div>`).join("")}
    ${ev.corrections.map((c) => `<p>${badge("人工纠正", "good")} ${esc(c.by)}：${esc(c.original.join(","))} → ${esc(c.corrected.join(","))}（${esc(c.reason || "无")}）</p>`).join("")}`;
  document.getElementById("evidenceCard").scrollIntoView({ behavior: "smooth" });
};
document.getElementById("closeEvidence").addEventListener("click", () =>
  document.getElementById("evidenceCard").classList.add("hidden"));

document.getElementById("btnIngest").addEventListener("click", async () => {
  try {
    const body = {
      ticket_no: document.getElementById("newNo").value,
      title: document.getElementById("newTitle").value,
      content: document.getElementById("newContent").value,
    };
    const r = await api("/api/tickets", { method: "POST", body });
    show("ingestResult", r.duplicate
      ? "⚠️ 判定为重复工单，已关联到既有工单，未重复分类。"
      : { labels: r.classification?.labels, confidence: r.classification?.confidence,
          is_unknown: r.classification?.is_unknown, evidence: r.classification?.evidence });
    loadTickets();
  } catch (e) { show("ingestResult", `错误：${e.message}`); }
});

// ---------------- 版本与重分类 ----------------
async function loadVersions() {
  const versions = await api("/api/versions");
  document.querySelector("#versionTable tbody").innerHTML = versions.map((v) => `
    <tr><td><b>${esc(v.version)}</b><br><span class="subtitle">${esc(v.description)}</span></td>
    <td>${badge(v.type, "info")}</td><td class="num">${v.threshold}</td>
    <td>${v.is_active ? badge("激活中", "good") : "—"}</td><td>${fmtTime(v.created_at)}</td>
    <td>${v.is_active ? "" : `<button class="btn btn-small" onclick="activate('${esc(v.version)}')">激活</button>`}</td></tr>`
  ).join("");
  const opts = versions.map((v) => `<option value="${esc(v.version)}">${esc(v.version)} (${v.type})</option>`).join("");
  document.getElementById("reclassVersion").innerHTML = opts;
  document.getElementById("diffA").innerHTML = opts;
  document.getElementById("diffB").innerHTML = opts;
  const active = versions.find((v) => v.is_active);
  document.getElementById("activeVersion").textContent =
    `激活版本：${active ? `${active.version}（${active.type}）` : "无"}`;
}
window.activate = async (version) => {
  await api("/api/versions/activate", { method: "POST", body: { version } });
  loadVersions();
};
document.getElementById("btnReclass").addEventListener("click", async () => {
  try {
    const job = await api("/api/versions/reclassify", {
      method: "POST",
      body: {
        version: document.getElementById("reclassVersion").value,
        include_unknown_only: document.getElementById("unknownOnly").checked,
      },
    });
    show("reclassResult", {
      任务: job.status, 工单总数: job.total, 标签变化: job.changed,
      未知被解决: job.unknown_resolved,
      对比明细: job.diffs.slice(0, 20),
    });
  } catch (e) { show("reclassResult", `错误：${e.message}`); }
});
document.getElementById("btnDiff").addEventListener("click", async () => {
  try {
    const d = await api(`/api/versions/diff?a=${encodeURIComponent(document.getElementById("diffA").value)}&b=${encodeURIComponent(document.getElementById("diffB").value)}`);
    show("diffResult", d);
  } catch (e) { show("diffResult", `错误：${e.message}`); }
});
document.getElementById("btnTrain").addEventListener("click", async () => {
  try {
    const v = await api("/api/versions/train", {
      method: "POST",
      body: { version: document.getElementById("trainVersion").value, description: "页面触发训练" },
    });
    show("trainResult", { 版本: v.version, 训练样本: v.config.trained_docs, 类别: v.config.classes });
    loadVersions();
  } catch (e) { show("trainResult", `错误：${e.message}`); }
});

// ---------------- 血缘 ----------------
async function loadLineageView() {
  const [topics, lineage] = await Promise.all([api("/api/topics"), api("/api/topics/lineage")]);
  document.querySelector("#topicTable tbody").innerHTML = topics.map((t) => `
    <tr><td><code>${esc(t.code)}</code></td><td>${esc(t.name)}</td>
    <td>${t.active ? badge("活跃", "good") : badge("已归档", "muted")}</td><td>${fmtTime(t.archived_at)}</td></tr>`
  ).join("");
  document.querySelector("#lineageTable tbody").innerHTML = lineage.map((l) => `
    <tr><td>${badge(l.type, l.type === "merge" ? "info" : "warning")}</td>
    <td>${l.sources.map(esc).join(" + ")}</td><td>${l.targets.map(esc).join(" , ")}</td>
    <td>${esc(l.reason)}</td><td>${fmtTime(l.created_at)}</td></tr>`
  ).join("") || `<tr><td colspan="5" class="subtitle">暂无血缘记录</td></tr>`;
}
document.getElementById("btnLineage").addEventListener("click", async () => {
  try {
    const r = await api("/api/topics/lineage", {
      method: "POST",
      body: {
        type: document.getElementById("lineageType").value,
        source_topic_codes: document.getElementById("lineageSource").value.split(",").map((s) => s.trim()).filter(Boolean),
        target_topic_codes: document.getElementById("lineageTarget").value.split(",").map((s) => s.trim()).filter(Boolean),
        reason: document.getElementById("lineageReason").value,
      },
    });
    show("lineageResult", r);
    loadLineageView();
  } catch (e) { show("lineageResult", `错误：${e.message}`); }
});

// ---------------- 未知样本与人工纠正 ----------------
async function loadUnknownView() {
  const [unknowns, corrections] = await Promise.all([
    api("/api/unknown-samples"), api("/api/corrections"),
  ]);
  const tickets = await api("/api/tickets?limit=500");
  const ticketMap = Object.fromEntries(tickets.map((t) => [t.id, t.ticket_no]));
  document.querySelector("#unknownTable tbody").innerHTML = unknowns.map((u) => `
    <tr><td class="num">${u.id}</td><td>${esc(ticketMap[u.ticket_id] || u.ticket_id)}</td>
    <td><pre class="result-box" style="max-height:80px;margin:0">${esc(JSON.stringify(u.top_guesses))}</pre></td>
    <td>${badge(u.status, u.status === "resolved" ? "good" : "warning")}</td>
    <td>${u.status === "pending" ? `
      <button class="btn btn-small" onclick="resolveUnknown(${u.id}, false)">标记已解决</button>
      <button class="btn btn-small" onclick="resolveUnknown(${u.id}, true)">忽略</button>` : "—"}</td></tr>`
  ).join("") || `<tr><td colspan="5" class="subtitle">没有未知样本</td></tr>`;
  document.querySelector("#correctionTable tbody").innerHTML = corrections.map((c) => `
    <tr><td class="num">${c.ticket_id}</td>
    <td>${c.original_labels.map((l) => badge(l, "muted")).join(" ")}</td>
    <td>${c.corrected_labels.map((l) => badge(l, "good")).join(" ")}</td>
    <td>${esc(c.reason)}</td><td>${esc(c.corrected_by)}</td><td>${fmtTime(c.created_at)}</td></tr>`
  ).join("") || `<tr><td colspan="6" class="subtitle">暂无纠正记录</td></tr>`;
}
window.resolveUnknown = async (id, ignore) => {
  const topic_codes = ignore ? [] : (prompt("输入归属主题 code（多个用逗号）") || "").split(",").map((s) => s.trim());
  await api(`/api/unknown-samples/${id}/resolve`, { method: "POST", body: { topic_codes, ignore } });
  loadUnknownView();
};
document.getElementById("btnCorrect").addEventListener("click", async () => {
  try {
    const r = await api(`/api/tickets/${document.getElementById("correctTicketId").value}/correction`, {
      method: "POST",
      body: {
        corrected_labels: document.getElementById("correctLabels").value.split(",").map((s) => s.trim()).filter(Boolean),
        reason: document.getElementById("correctReason").value,
      },
    });
    show("correctResult", r);
    loadUnknownView();
  } catch (e) { show("correctResult", `错误：${e.message}`); }
});

// ---------------- 告警与导出 ----------------
const levelCls = { info: "info", warning: "warning", critical: "critical" };
async function loadAlertView() {
  const [alerts, rules, audit] = await Promise.all([
    api("/api/analytics/alerts"), api("/api/analytics/alert-rules"), api("/api/export/audit"),
  ]);
  document.querySelector("#alertTable tbody").innerHTML = alerts.map((a) => `
    <tr><td>${badge(a.level, levelCls[a.level] || "muted")}</td><td>${esc(a.title)}</td>
    <td><pre class="result-box" style="max-height:90px;margin:0">${esc(JSON.stringify(a.detail))}</pre></td>
    <td>${a.acknowledged ? badge("已确认", "good") : badge("待处理", "warning")}</td>
    <td>${fmtTime(a.created_at)}</td>
    <td>${a.acknowledged ? "—" : `<button class="btn btn-small" onclick="ackAlert(${a.id})">确认</button>`}</td></tr>`
  ).join("") || `<tr><td colspan="6" class="subtitle">暂无告警</td></tr>`;
  document.querySelector("#ruleTable tbody").innerHTML = rules.map((r) => `
    <tr><td>${esc(r.name)}</td><td><code>${esc(r.metric)}</code></td>
    <td class="num">${r.threshold}</td><td>${badge(r.level, levelCls[r.level] || "muted")}</td>
    <td>${r.enabled ? badge("启用", "good") : badge("停用", "muted")}</td></tr>`
  ).join("");
  document.querySelector("#auditTable tbody").innerHTML = audit.map((a) => `
    <tr><td>${esc(a.actor)}</td><td>${badge(a.role, "info")}</td><td>${esc(a.action)}</td>
    <td><pre class="result-box" style="max-height:80px;margin:0">${esc(JSON.stringify(a.detail))}</pre></td>
    <td>${fmtTime(a.created_at)}</td></tr>`
  ).join("") || `<tr><td colspan="5" class="subtitle">暂无审计记录</td></tr>`;
}
window.ackAlert = async (id) => {
  await api(`/api/analytics/alerts/${id}/ack`, { method: "POST" });
  loadAlertView();
};
document.getElementById("btnExport").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        role: document.getElementById("exportRole").value,
        include_content: document.getElementById("exportContent").checked,
        actor: document.getElementById("exportActor").value || "anonymous",
      }),
    });
    const text = await res.text();
    if (!res.ok) { show("exportResult", `错误：${text}`); return; }
    show("exportResult", `审计 ID：${res.headers.get("X-Audit-Id")}\n\n${text.slice(0, 3000)}`);
    loadAlertView();
  } catch (e) { show("exportResult", `错误：${e.message}`); }
});

// ---------------- 初始化 ----------------
(async function init() {
  try {
    await loadOverview();
    const versions = await api("/api/versions");
    const active = versions.find((v) => v.is_active);
    document.getElementById("activeVersion").textContent =
      `激活版本：${active ? `${active.version}（${active.type}）` : "无"}`;
  } catch (e) {
    document.getElementById("activeVersion").textContent = `加载失败：${e.message}`;
  }
})();
