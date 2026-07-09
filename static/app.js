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
  const byProject = await api(`/api/analytics/by-project?days=90`);
  const sel = document.getElementById('project-select');
  const current = sel.value;
  sel.innerHTML = '<option value="">Select project…</option>' +
    byProject.map(r => `<option value="${r.project_name}" ${r.project_name === current ? 'selected' : ''}>${r.project_name}</option>`).join('');
  if (current) loadProjectData(current);
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
  const [ts, byRole, byModel] = await Promise.all([
    api(`/api/analytics/timeseries?days=30&project=${encodeURIComponent(project)}`),
    api(`/api/analytics/by-role?days=30`),
    api(`/api/analytics/by-model?days=30`),
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
      labels: byComplexity.map(r => 'C' + r.complexity),
      datasets: [{
        label: 'Avg Cost ($)',
        data: byComplexity.map(r => r.avg_cost_usd ?? 0),
        backgroundColor: CHART_COLORS,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: { display: true, text: 'Avg Cost by Complexity Tier', color: '#e2e8f0' },
      },
      scales: {
        x: { ticks: { color: '#e2e8f0' }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#8892a4', callback: v => '$' + v.toFixed(4) }, grid: { color: '#2a2d3a' } },
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
