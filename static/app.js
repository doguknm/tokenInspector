// ── Globals ──────────────────────────────────────────────────────────────────
const CHART_COLORS = ['#6366f1','#22c55e','#f59e0b','#ef4444','#06b6d4','#a855f7','#ec4899','#14b8a6'];
let charts = {};
let currentDays = 7;
let projPage = 1;
let projPageSize = 50;
let currentProject = '';
let projStatus = '';

// ── Utilities ─────────────────────────────────────────────────────────────────
const fmt = {
  num: n => n == null ? '—' : Number(n).toLocaleString(),
  cost: n => n == null ? '—' : '$' + Number(n).toFixed(4),
  ms: n => n == null ? '—' : n.toLocaleString() + ' ms',
  date: s => s ? s.replace('T', ' ').slice(0, 19) : '—',
};

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function badge(status) {
  const cls = { success: 'badge-success', error: 'badge-error', timeout: 'badge-timeout' }[status] || '';
  return `<span class="badge ${cls}">${status}</span>`;
}

// ── Navigation ────────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const view = link.dataset.view;
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    link.classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    document.getElementById('view-' + view).classList.remove('hidden');
    if (view === 'overview') loadOverview();
    if (view === 'projects') loadProjectsView();
    if (view === 'models') loadModels();
    if (view === 'complexity') loadComplexity();
    if (view === 'tasks') loadTasks();
    if (view === 'settings') loadSettings();
  });
});

// ── Overview ──────────────────────────────────────────────────────────────────
document.querySelectorAll('.day-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.day-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentDays = parseInt(btn.dataset.days);
    loadOverview();
  });
});

async function loadOverview() {
  const [summary, byProject, byModel, timeseries] = await Promise.all([
    api(`/api/analytics/summary?days=${currentDays}`),
    api(`/api/analytics/by-project?days=${currentDays}`),
    api(`/api/analytics/by-model?days=${currentDays}`),
    api(`/api/analytics/timeseries?days=${currentDays}`),
  ]);

  document.getElementById('stat-events').textContent = fmt.num(summary.total_events);
  document.getElementById('stat-tokens').textContent = fmt.num(summary.total_prompt_tokens + summary.total_completion_tokens);
  document.getElementById('stat-cost').textContent = fmt.cost(summary.total_cost_usd);
  document.getElementById('stat-projects').textContent = fmt.num(summary.unique_projects);

  // timeseries line chart
  destroyChart('timeseries');
  const tsCtx = document.getElementById('chart-timeseries').getContext('2d');
  charts['timeseries'] = new Chart(tsCtx, {
    data: {
      labels: timeseries.map(r => r.date),
      datasets: [
        { type: 'line', label: 'Tokens', data: timeseries.map(r => r.total_tokens), borderColor: CHART_COLORS[0], backgroundColor: CHART_COLORS[0] + '22', yAxisID: 'yTokens', tension: .3, fill: true },
        { type: 'line', label: 'Cost ($)', data: timeseries.map(r => r.total_cost_usd), borderColor: CHART_COLORS[1], backgroundColor: CHART_COLORS[1] + '22', yAxisID: 'yCost', tension: .3, fill: true },
      ],
    },
    options: {
      responsive: true, interaction: { mode: 'index' },
      plugins: { legend: { labels: { color: '#e2e8f0' } }, title: { display: true, text: 'Daily Tokens & Cost', color: '#e2e8f0' } },
      scales: {
        x: { ticks: { color: '#8892a4' }, grid: { color: '#2a2d3a' } },
        yTokens: { position: 'left', ticks: { color: '#6366f1' }, grid: { color: '#2a2d3a' } },
        yCost: { position: 'right', ticks: { color: '#22c55e', callback: v => '$' + v.toFixed(3) }, grid: { drawOnChartArea: false } },
      },
    },
  });

  // top 5 models bar chart
  const top5 = byModel.slice(0, 5);
  destroyChart('top-models');
  const tmCtx = document.getElementById('chart-top-models').getContext('2d');
  charts['top-models'] = new Chart(tmCtx, {
    type: 'bar',
    data: {
      labels: top5.map(r => r.model),
      datasets: [{ label: 'Cost ($)', data: top5.map(r => r.total_cost_usd), backgroundColor: CHART_COLORS }],
    },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { legend: { display: false }, title: { display: true, text: 'Top 5 Models by Cost', color: '#e2e8f0' } },
      scales: {
        x: { ticks: { color: '#8892a4', callback: v => '$' + v }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#e2e8f0' }, grid: { color: '#2a2d3a' } },
      },
    },
  });

  // projects table
  const tbody = document.querySelector('#table-projects tbody');
  tbody.innerHTML = byProject.map(r => `
    <tr>
      <td>${r.project_name}</td>
      <td>${fmt.num(r.event_count)}</td>
      <td>${fmt.num(r.total_tokens)}</td>
      <td>${fmt.cost(r.total_cost_usd)}</td>
      <td>${fmt.ms(r.avg_process_time_ms)}</td>
    </tr>`).join('');
}

// ── Projects ──────────────────────────────────────────────────────────────────
async function loadProjectsView() {
  const [inventory, byProject] = await Promise.all([
    api('/api/analytics/project-inventory?days=90'),
    api('/api/analytics/by-project?days=90'),
  ]);

  const inventoryBody = document.querySelector('#table-project-inventory tbody');
  inventoryBody.innerHTML = inventory.map(r => `
    <tr>
      <td><code>${r.project_name}</code></td>
      <td>${r.directory_name}</td>
      <td>${r.discovery_source}</td>
      <td><span class="badge ${r.has_telemetry ? 'badge-success' : ''}">${r.has_telemetry ? 'observed' : 'inventory only'}</span></td>
      <td>${fmt.num(r.event_count)}</td>
      <td>${fmt.num(r.total_tokens)}</td>
      <td>${fmt.date(r.last_activity_at)}</td>
    </tr>`).join('');

  const observedBody = document.querySelector('#table-observed-projects tbody');
  observedBody.innerHTML = byProject.map(r => `
    <tr>
      <td><code>${r.project_name}</code></td>
      <td>${fmt.num(r.event_count)}</td>
      <td>${fmt.num(r.total_tokens)}</td>
      <td>${fmt.cost(r.total_cost_usd)}</td>
      <td>${fmt.ms(r.avg_process_time_ms)}</td>
    </tr>`).join('');

  const sel = document.getElementById('project-select');
  const current = sel.value;
  const inventoryNames = new Set(inventory.map(r => r.project_name));
  const observedOnly = byProject.filter(r => !inventoryNames.has(r.project_name));
  const knownOptions = inventory.map(r => `<option value="${r.project_name}">${r.project_name} (${r.directory_name})</option>`).join('');
  const observedOptions = observedOnly.map(r => `<option value="${r.project_name}">${r.project_name}</option>`).join('');
  sel.innerHTML = '<option value="">Select project…</option>' +
    `<optgroup label="Known repositories">${knownOptions}</optgroup>` +
    (observedOptions ? `<optgroup label="Observed only">${observedOptions}</optgroup>` : '');
  if ([...inventoryNames, ...observedOnly.map(r => r.project_name)].includes(current)) {
    sel.value = current;
    loadProjectData(current);
  } else {
    currentProject = '';
  }
}

document.getElementById('project-select').addEventListener('change', e => {
  currentProject = e.target.value;
  projPage = 1;
  if (currentProject) loadProjectData(currentProject);
});

document.getElementById('proj-status-filter').addEventListener('change', e => {
  projStatus = e.target.value;
  projPage = 1;
  if (currentProject) loadEventLog();
});

document.getElementById('proj-prev').addEventListener('click', () => {
  if (projPage > 1) { projPage--; loadEventLog(); }
});
document.getElementById('proj-next').addEventListener('click', () => {
  projPage++;
  loadEventLog();
});
async function loadProjectData(project) {
  currentProject = project;
  const query = `days=30&project=${encodeURIComponent(project)}`;
  const [byModel, byRole, ts] = await Promise.all([
    api(`/api/analytics/by-model?${query}`),
    api(`/api/analytics/by-role?${query}`),
    api(`/api/analytics/timeseries?${query}`),
  ]);

  destroyChart('proj-timeseries');
  const ctx1 = document.getElementById('chart-proj-timeseries').getContext('2d');
  charts['proj-timeseries'] = new Chart(ctx1, {
    type: 'line',
    data: {
      labels: ts.map(r => r.date),
      datasets: [{ label: 'Tokens', data: ts.map(r => r.total_tokens), borderColor: CHART_COLORS[0], backgroundColor: CHART_COLORS[0] + '22', fill: true, tension: .3 }],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#e2e8f0' } }, title: { display: true, text: 'Daily Token Usage', color: '#e2e8f0' } },
      scales: { x: { ticks: { color: '#8892a4' }, grid: { color: '#2a2d3a' } }, y: { ticks: { color: '#e2e8f0' }, grid: { color: '#2a2d3a' } } },
    },
  });

  destroyChart('proj-roles');
  const ctx2 = document.getElementById('chart-proj-roles').getContext('2d');
  charts['proj-roles'] = new Chart(ctx2, {
    type: 'doughnut',
    data: { labels: byRole.map(r => r.role), datasets: [{ data: byRole.map(r => r.total_tokens), backgroundColor: CHART_COLORS }] },
    options: { plugins: { legend: { labels: { color: '#e2e8f0' } }, title: { display: true, text: 'Tokens by Role', color: '#e2e8f0' } } },
  });

  destroyChart('proj-models');
  const ctx3 = document.getElementById('chart-proj-models').getContext('2d');
  charts['proj-models'] = new Chart(ctx3, {
    type: 'doughnut',
    data: { labels: byModel.map(r => r.model), datasets: [{ data: byModel.map(r => r.total_cost_usd), backgroundColor: CHART_COLORS }] },
    options: { plugins: { legend: { labels: { color: '#e2e8f0' } }, title: { display: true, text: 'Cost by Model', color: '#e2e8f0' } } },
  });

  loadEventLog();
}

async function loadEventLog() {
  const params = new URLSearchParams({ page: projPage, page_size: projPageSize });
  if (currentProject) params.set('project', currentProject);
  if (projStatus) params.set('status', projStatus);
  const data = await api(`/api/events?${params}`);

  const tbody = document.querySelector('#table-proj-events tbody');
  tbody.innerHTML = data.items.map(r => `
    <tr>
      <td>${fmt.date(r.recorded_at)}</td>
      <td><code>${r.model}</code></td>
      <td>${r.role || '—'}</td>
      <td>${fmt.num(r.prompt_tokens)}</td>
      <td>${fmt.num(r.completion_tokens)}</td>
      <td>${fmt.cost(r.estimated_cost_usd)}</td>
      <td>${fmt.ms(r.process_time_ms)}</td>
      <td>${badge(r.status)}</td>
    </tr>`).join('');

  const totalPages = Math.max(1, Math.ceil(data.total / projPageSize));
  document.getElementById('proj-page-info').textContent = `Page ${projPage} of ${totalPages}`;
  document.getElementById('proj-prev').disabled = projPage <= 1;
  document.getElementById('proj-next').disabled = projPage >= totalPages;
}

// ── Models ────────────────────────────────────────────────────────────────────
async function loadModels() {
  const data = await api(`/api/analytics/by-model?days=90`);

  const withLatency = data.filter(r => r.avg_process_time_ms != null);
  destroyChart('model-latency');
  const ctx = document.getElementById('chart-model-latency').getContext('2d');
  charts['model-latency'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: withLatency.map(r => r.model),
      datasets: [{ label: 'Avg Latency (ms)', data: withLatency.map(r => r.avg_process_time_ms), backgroundColor: CHART_COLORS }],
    },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { legend: { display: false }, title: { display: true, text: 'Avg Latency by Model', color: '#e2e8f0' } },
      scales: {
        x: { ticks: { color: '#8892a4' }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#e2e8f0' }, grid: { color: '#2a2d3a' } },
      },
    },
  });

  const tbody = document.querySelector('#table-models tbody');
  tbody.innerHTML = data.map(r => `
    <tr>
      <td><code>${r.model}</code></td>
      <td>${fmt.num(r.event_count)}</td>
      <td>${fmt.num(r.avg_prompt_tokens)}</td>
      <td>${fmt.num(r.avg_completion_tokens)}</td>
      <td>${fmt.ms(r.avg_process_time_ms)}</td>
      <td>${fmt.cost(r.total_cost_usd)}</td>
      <td>${r.cost_per_1k_tokens != null ? '$' + r.cost_per_1k_tokens.toFixed(4) : '—'}</td>
    </tr>`).join('');
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  const data = await api('/api/settings/pricing');
  const tbody = document.querySelector('#table-pricing tbody');
  tbody.innerHTML = data.map(r => `
    <tr data-model="${r.model}">
      <td><code>${r.model}</code></td>
      <td class="editable-cell"><input type="number" step="any" value="${r.input_price_per_1m}" /></td>
      <td class="editable-cell"><input type="number" step="any" value="${r.output_price_per_1m}" /></td>
      <td>${fmt.date(r.updated_at)}</td>
      <td>
        <button onclick="savePricing('${r.model}', this)">Save</button>
        <button class="danger" onclick="deletePricing('${r.model}', this)">Delete</button>
      </td>
    </tr>`).join('');
}

async function savePricing(model, btn) {
  const row = btn.closest('tr');
  const inputs = row.querySelectorAll('input[type="number"]');
  await fetch('/api/settings/pricing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, input_price_per_1m: parseFloat(inputs[0].value), output_price_per_1m: parseFloat(inputs[1].value) }),
  });
  loadSettings();
}

async function deletePricing(model, btn) {
  if (!confirm(`Delete pricing rule for "${model}"?`)) return;
  await fetch(`/api/settings/pricing/${encodeURIComponent(model)}`, { method: 'DELETE' });
  loadSettings();
}

document.getElementById('pricing-add-form').addEventListener('submit', async e => {
  e.preventDefault();
  const model = document.getElementById('new-model').value.trim();
  const inp = parseFloat(document.getElementById('new-input-price').value);
  const out = parseFloat(document.getElementById('new-output-price').value);
  await fetch('/api/settings/pricing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, input_price_per_1m: inp, output_price_per_1m: out }),
  });
  document.getElementById('new-model').value = '';
  document.getElementById('new-input-price').value = '';
  document.getElementById('new-output-price').value = '';
  loadSettings();
});

// ── Complexity ────────────────────────────────────────────────────────────────
async function loadComplexity() {
  const [byComplexity, recs] = await Promise.all([
    api('/api/analytics/by-complexity?days=30'),
    api('/api/analytics/routing-recommendations?days=30'),
  ]);

  destroyChart('complexity');
  const ctx = document.getElementById('chart-complexity').getContext('2d');
  charts['complexity'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: byComplexity.map(r => 'Tier ' + r.complexity),
      datasets: [{
        label: 'Avg Tokens',
        data: byComplexity.map(r => r.avg_tokens ?? 0),
        backgroundColor: CHART_COLORS,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: { display: true, text: 'Avg Tokens by Deterministic Complexity', color: '#e2e8f0' },
      },
      scales: {
        x: { ticks: { color: '#e2e8f0' }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#8892a4', callback: v => fmt.num(v) }, grid: { color: '#2a2d3a' } },
      },
    },
  });

  const noRecs = document.getElementById('complexity-no-recs');
  const tbody = document.querySelector('#table-recs tbody');
  if (recs.length === 0) {
    noRecs.classList.remove('hidden');
    tbody.innerHTML = '';
  } else {
    noRecs.classList.add('hidden');
    tbody.innerHTML = recs.map(r => `
      <tr>
        <td>${r.role}</td>
        <td>C${r.complexity}</td>
        <td><code>${r.most_used_model}</code></td>
        <td><code>${r.recommended_model}</code></td>
        <td style="color:#22c55e;">−${r.estimated_savings_pct}%</td>
        <td>${fmt.num(r.data_points)}</td>
      </tr>`).join('');
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadOverview();

// ── Tasks ─────────────────────────────────────────────────────────────────────
// Every API string goes through esc() in content and attributes. No prompt text is requested.
const TASKS_PAGE_SIZE = 50;
let tasksDays = 30;
let tasksPage = 1;
let tasksProject = '';
let tasksScoredOnly = false;
let tasksRootOnly = false;
let tasksTableGen = 0;
let tasksChartGen = 0;
let taskDetailGen = 0;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const COMPLETION_TEXT = { session_end: 'session end', next_task: 'next task', inferred: 'inferred', open: 'open' };
const PROMPT_TEXT = { retained: 'retained', expired: 'expired', purged: 'purged', none: '—' };

function tasksQuery(extra) {
  const params = new URLSearchParams({ days: tasksDays, ...extra });
  if (tasksProject) params.set('project', tasksProject);
  if (tasksRootOnly) params.set('root_only', 'true');
  return params.toString();
}

function taskLabel(item) {
  return item.turn_id ?? item.task_id ?? '';
}

function probStrip(probabilities) {
  if (!Array.isArray(probabilities)) return '';
  const bars = probabilities.map(p => `<i style="opacity:${Math.max(0.12, Number(p) || 0).toFixed(2)}"></i>`).join('');
  return `<span class="prob-strip" aria-hidden="true">${bars}</span>`;
}

function taskRow(item) {
  const full = taskLabel(item);
  const shown = full.length > 24 ? full.slice(0, 24) + '…' : full;
  const prefix = item.hierarchy_status === 'child' ? '↳ ' : '';
  const jev = item.jev
    ? `<span class="jev-cell">${item.jev.display_score.toFixed(1)}/5 ${probStrip(item.jev.probabilities)}</span>`
    : '<span class="muted">not scored</span>';
  const unpriced = item.unpriced_count > 0 ? ' <span class="badge badge-muted">unpriced</span>' : '';
  return `<tr data-ref="${esc(item.task_ref)}" tabindex="0">
    <td>${esc(fmt.date(item.last_seen_at))}</td>
    <td>${esc(item.project_name)}</td>
    <td class="task-id" title="${esc(full)}">${prefix}${esc(shown)}</td>
    <td>${item.start_complexity == null ? '—' : 'C' + esc(item.start_complexity)}</td>
    <td>${jev}</td>
    <td class="num">${item.jev ? item.jev.confidence.toFixed(2) : '—'}</td>
    <td class="num">${fmt.num(item.total_tokens)}</td>
    <td class="num">${fmt.cost(item.cost_usd)}${unpriced}</td>
    <td class="num">${fmt.num(item.tool_call_count)}</td>
    <td class="num">${fmt.ms(item.wall_time_ms)}</td>
    <td>${esc(COMPLETION_TEXT[item.completion] ?? item.completion)}</td>
    <td>${esc(PROMPT_TEXT[item.prompt_state] ?? item.prompt_state)}</td>
  </tr>`;
}

function setTasksError(message) {
  const el = document.getElementById('tasks-error');
  el.textContent = message || '';
  el.classList.toggle('hidden', !message);
}

function fillProjects(projects) {
  // Returns true when the active project vanished from this window and the filter was cleared.
  const select = document.getElementById('tasks-project');
  const cleared = tasksProject !== '' && !projects.includes(tasksProject);
  if (cleared) tasksProject = '';
  select.innerHTML = '<option value="">All projects</option>' +
    projects.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
  select.value = tasksProject;
  return cleared;
}

async function loadTasksTable(clamped = false) {
  const gen = ++tasksTableGen;
  const body = document.querySelector('#table-tasks tbody');
  const info = document.getElementById('tasks-page-info');
  const prev = document.getElementById('tasks-prev');
  const next = document.getElementById('tasks-next');
  const empty = document.getElementById('tasks-empty');
  let data;
  try {
    const extra = { page: tasksPage, page_size: TASKS_PAGE_SIZE };
    if (tasksScoredOnly) extra.evaluated = 'true';
    data = await api(`/api/tasks?${tasksQuery(extra)}`);
  } catch (err) {
    if (gen !== tasksTableGen) return;
    body.innerHTML = '';
    info.textContent = '';
    prev.disabled = next.disabled = true;
    setTasksError('Could not load tasks: ' + err.message);
    return;
  }
  if (gen !== tasksTableGen) return;
  setTasksError('');
  if (fillProjects(data.projects || [])) {
    tasksFiltersChanged();  // the visible selector and the request must never disagree
    return;
  }
  const pages = Math.ceil(data.total / TASKS_PAGE_SIZE);
  if (data.items.length === 0 && data.total > 0 && tasksPage > pages && !clamped) {
    tasksPage = pages;
    return loadTasksTable(true);
  }
  const none = data.total === 0;
  empty.classList.toggle('hidden', !none);
  body.parentElement.classList.toggle('hidden', none);
  body.innerHTML = none ? '' : data.items.map(taskRow).join('');
  info.textContent = none ? 'No tasks' : `Page ${tasksPage} of ${pages} (${data.total} tasks)`;
  prev.disabled = none || tasksPage <= 1;
  next.disabled = none || tasksPage >= pages;
}

async function loadTasksChart() {
  const gen = ++tasksChartGen;
  const canvas = document.getElementById('chart-tasks-scatter');
  const cap = document.getElementById('tasks-chart-cap');
  const emptyEl = document.getElementById('tasks-chart-empty');
  const errorEl = document.getElementById('tasks-chart-error');
  let data;
  try {
    data = await api(`/api/tasks?${tasksQuery({ evaluated: 'true', page: 1, page_size: 200 })}`);
  } catch (err) {
    if (gen !== tasksChartGen) return;
    destroyChart('tasks-scatter');
    canvas.classList.add('hidden');
    cap.classList.add('hidden');
    emptyEl.classList.add('hidden');
    errorEl.classList.remove('hidden');
    return;
  }
  if (gen !== tasksChartGen) return;
  errorEl.classList.add('hidden');
  const points = data.items
    .filter(i => i.jev && i.start_complexity != null)
    .map(i => ({ x: i.start_complexity, y: i.jev.display_score }));
  destroyChart('tasks-scatter');
  emptyEl.classList.toggle('hidden', points.length > 0);
  canvas.classList.toggle('hidden', points.length === 0);
  const capped = data.total > data.items.length;
  cap.textContent = capped ? `Showing latest 200 of ${data.total} scored tasks` : '';
  cap.classList.toggle('hidden', !capped);
  if (!points.length) return;
  const axis = title => ({ min: 0.5, max: 5.5, ticks: { stepSize: 1, color: '#8892a4' }, grid: { color: '#2a2d3a' },
    title: { display: true, text: title, color: '#8892a4' } });
  charts['tasks-scatter'] = new Chart(canvas.getContext('2d'), {
    type: 'scatter',
    data: { datasets: [{ label: 'Scored tasks', data: points, backgroundColor: CHART_COLORS[0] + 'cc', pointRadius: 4 }] },
    options: {
      responsive: true,
      plugins: { legend: { display: false }, title: { display: true, text: 'Start complexity vs JEV difficulty', color: '#e2e8f0' } },
      scales: { x: axis('request-shape-v1 (1–5)'), y: axis('JEV difficulty (1–5)') },
    },
  });
}

async function loadTasksStatus() {
  const el = document.getElementById('tasks-jev-status');
  let s;
  try {
    s = await api('/api/tasks/evaluator-status');
  } catch (err) {
    el.textContent = 'JEV: status unavailable';
    return;
  }
  if (!s.enabled) {
    const configError = ['ingest_token_missing', 'invalid_budget_config'].includes(s.disabled_reason);
    el.innerHTML = 'JEV: disabled' + (configError ? ` <span class="warn">(config error: ${esc(s.disabled_reason)})</span>` : '');
    return;
  }
  let text = `JEV: enabled — today $${Number(s.spent_today_usd).toFixed(4)} of $${esc(s.budget_day_usd)}, ` +
    `${esc(s.calls_today)}/${esc(s.max_calls_per_day)} calls`;
  if (s.running) text += ' — running';
  if (s.last_error_type === 'budget_exceeded') text += ' <span class="warn">— budget ceiling reached</span>';
  if (s.last_error_type === 'deferred_rate_limited') {
    text += ` — rate-limited, retry after ${esc(fmt.date(s.last_run && s.last_run.retry_not_before))}`;
  }
  el.innerHTML = text;
}

function loadTasks() {
  loadTasksStatus();
  loadTasksTable();
  loadTasksChart();
}

function tasksFiltersChanged() {
  tasksPage = 1;
  loadTasksTable();
  loadTasksChart();
}

document.querySelectorAll('.tasks-day').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tasks-day').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    tasksDays = parseInt(btn.dataset.days, 10);
    tasksFiltersChanged();
  });
});
document.getElementById('tasks-project').addEventListener('change', e => { tasksProject = e.target.value; tasksFiltersChanged(); });
document.getElementById('tasks-scored-only').addEventListener('change', e => { tasksScoredOnly = e.target.checked; tasksFiltersChanged(); });
document.getElementById('tasks-root-only').addEventListener('change', e => { tasksRootOnly = e.target.checked; tasksFiltersChanged(); });
document.getElementById('tasks-prev').addEventListener('click', () => { if (tasksPage > 1) { tasksPage--; loadTasksTable(); } });
document.getElementById('tasks-next').addEventListener('click', () => { tasksPage++; loadTasksTable(); });

const tasksBody = document.querySelector('#table-tasks tbody');
tasksBody.addEventListener('click', e => {
  const row = e.target.closest('tr[data-ref]');
  if (row) openTaskDetail(row.dataset.ref);
});
tasksBody.addEventListener('keydown', e => {
  const row = e.target.closest('tr[data-ref]');
  if (row && e.key === 'Enter') openTaskDetail(row.dataset.ref);
});

function closeTaskDetail() {
  taskDetailGen++;  // Close is authoritative: any in-flight response is discarded
  destroyChart('task-probs');
  document.getElementById('task-detail').classList.add('hidden');
}
document.getElementById('task-detail-close').addEventListener('click', closeTaskDetail);

function field(label, value) {
  return `<dt>${esc(label)}</dt><dd>${value}</dd>`;
}

function promptStateText(d) {
  if (d.prompt_state === 'retained') return 'retained until ' + esc(fmt.date(d.prompt_expires_at));
  if (d.prompt_state === 'none') return 'not captured';
  return esc(d.prompt_state);
}

function renderTaskDetail(d) {
  document.getElementById('task-detail-title').textContent = taskLabel(d);
  const completion = esc(COMPLETION_TEXT[d.completion] ?? d.completion) + (d.completed_at ? ' at ' + esc(fmt.date(d.completed_at)) : '');
  const tokens = [d.prompt_tokens, d.completion_tokens, d.cache_read_tokens, d.cache_creation_tokens].map(fmt.num).join(' / ') +
    ', total ' + fmt.num(d.total_tokens);
  const jev = d.jev
    ? `${d.jev.display_score.toFixed(1)}/5, confidence ${d.jev.confidence.toFixed(2)}, ${esc(d.jev.provider_used)}, ` +
      `${esc(d.jev.rubric_version)}, ${esc(fmt.date(d.jev.evaluated_at))}`
    : '<span class="muted">not scored</span>';
  document.getElementById('task-detail-body').innerHTML = `<dl class="detail-grid">
    ${field('Project', esc(d.project_name))}
    ${field('Session', esc(d.session_id ?? '—'))}
    ${field('Hierarchy', esc(d.hierarchy_status))}
    ${field('Parent / root', esc(d.parent_task_ref ?? '—') + ' / ' + esc(d.root_task_ref ?? '—'))}
    ${field('First / last seen', esc(fmt.date(d.first_seen_at)) + ' / ' + esc(fmt.date(d.last_seen_at)))}
    ${field('Completion', completion)}
    ${field('Wall time', fmt.ms(d.wall_time_ms))}
    ${field('LLM requests / tools', fmt.num(d.llm_request_count) + ' / ' + fmt.num(d.tool_call_count))}
    ${field('Errors / retries', fmt.num(d.error_count) + ' / ' + fmt.num(d.retry_count))}
    ${field('Tokens (prompt / completion / cache read / cache write)', tokens)}
    ${field('Cost / estimated / unpriced', fmt.cost(d.cost_usd) + ' / ' + fmt.cost(d.estimated_cost_usd) + ' / ' + fmt.num(d.unpriced_count))}
    ${field('RS-v1', d.start_complexity == null ? '—' : 'C' + esc(d.start_complexity) + ' (' + esc(d.start_complexity_method) + ')')}
    ${field('Prompt', promptStateText(d))}
    ${field('JEV', jev)}
  </dl>`;

  destroyChart('task-probs');
  const probsBox = document.getElementById('task-probs-box');
  probsBox.classList.toggle('hidden', !d.jev);
  if (d.jev && Array.isArray(d.jev.probabilities)) {
    charts['task-probs'] = new Chart(document.getElementById('chart-task-probs').getContext('2d'), {
      type: 'bar',
      data: { labels: ['L1', 'L2', 'L3', 'L4', 'L5'], datasets: [{ label: 'Probability', data: d.jev.probabilities, backgroundColor: CHART_COLORS[0] }] },
      options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false } },
        scales: { x: { min: 0, max: 1, ticks: { color: '#8892a4' }, grid: { color: '#2a2d3a' } }, y: { ticks: { color: '#8892a4' }, grid: { display: false } } } },
    });
  }

  const children = (d.children || []).map(c => `<tr><td title="${esc(taskLabel(c))}">${esc(taskLabel(c))}</td>` +
    `<td>${c.start_complexity == null ? '—' : 'C' + esc(c.start_complexity)}</td>` +
    `<td>${c.jev_raw_score == null ? '<span class="muted">not scored</span>' : (c.jev_raw_score + 1).toFixed(1) + '/5'}</td></tr>`).join('');
  const evaluations = (d.evaluations || []).map(e => {
    const score = e.raw_score != null ? (e.raw_score + 1).toFixed(1) : (e.label != null ? String(e.label + 1) : '—');
    return `<tr><td>${esc(e.evaluator)}</td><td>${esc(e.status)}</td><td>${esc(score)}</td>` +
      `<td>${e.confidence == null ? '—' : Number(e.confidence).toFixed(2)}</td><td>${esc(e.provider_used ?? '—')}</td>` +
      `<td>${fmt.num(e.http_attempts)}</td><td>${esc(e.error_type ?? '—')}</td><td>${esc(fmt.date(e.evaluated_at))}</td></tr>`;
  }).join('');
  document.getElementById('task-detail-tables').innerHTML =
    `<h3>Child tasks${d.children_truncated ? ' (first 200 shown)' : ''}</h3>` +
    (children ? `<div class="table-wrap"><table><thead><tr><th>Task</th><th>RS-v1</th><th>JEV</th></tr></thead><tbody>${children}</tbody></table></div>`
      : '<p class="muted">No child tasks.</p>') +
    '<h3>Evaluations</h3>' +
    (evaluations ? `<div class="table-wrap"><table><thead><tr><th>Evaluator</th><th>Status</th><th>Score</th><th>Conf.</th><th>Provider</th><th>Attempts</th><th>Error</th><th>Evaluated</th></tr></thead><tbody>${evaluations}</tbody></table></div>`
      : '<p class="muted">No evaluations.</p>');
}

function showDetailMessage(html) {
  destroyChart('task-probs');
  document.getElementById('task-detail-title').textContent = 'Task';
  document.getElementById('task-detail-body').innerHTML = html;
  document.getElementById('task-probs-box').classList.add('hidden');
  document.getElementById('task-detail-tables').innerHTML = '';
  document.getElementById('task-detail').classList.remove('hidden');
}

async function openTaskDetail(ref) {
  const gen = ++taskDetailGen;
  let d;
  try {
    const r = await fetch(`/api/tasks/${encodeURIComponent(ref)}`);
    if (gen !== taskDetailGen) return;
    if (r.status === 404) {
      showDetailMessage('Task not found.');
      return;
    }
    if (!r.ok) throw new Error(await r.text());
    d = await r.json();
  } catch (err) {
    if (gen !== taskDetailGen) return;
    showDetailMessage('Could not load task detail. <span class="muted">' + esc(err.message) + '</span>');
    return;
  }
  if (gen !== taskDetailGen) return;
  renderTaskDetail(d);
  document.getElementById('task-detail').classList.remove('hidden');
}
