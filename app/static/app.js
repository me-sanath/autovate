async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  return r.json();
}

async function refreshHealth() {
  try {
    const r = await api('/health');
    document.getElementById('api-health').textContent = 'API: ' + r.status;
  } catch (e) {
    document.getElementById('api-health').textContent = 'API: down';
  }
}

const jobLogs = {}; // job_id -> array of log messages

function addLog(jobId, message, level = 'INFO') {
  if (!jobLogs[jobId]) jobLogs[jobId] = [];
  jobLogs[jobId].push({ message, level, ts: new Date().toISOString() });
}

function getStateClass(state) {
  if (state === 'SUCCESS') return 'success';
  if (state === 'FAILURE') return 'error';
  if (state === 'PENDING') return 'pending';
  return '';
}

function renderJobs(items) {
  const container = document.getElementById('jobs-container');
  container.innerHTML = '';
  for (const j of items) {
    const accordion = document.createElement('div');
    accordion.className = 'job-accordion';
    const id = `job-${j.id}`;
    const logs = jobLogs[j.id] || [];
    const state = j.state || 'PENDING';
    accordion.innerHTML = `
      <div class="job-header" onclick="toggleAccordion('${j.id}')">
        <div class="job-info">
          <span><strong>${j.id.slice(0, 8)}...</strong></span>
          <span><span class="badge">${j.type || 'unknown'}</span></span>
          <span><code>${j.repo_path || 'N/A'}</code></span>
          <span class="state-${getStateClass(state)}">${state}</span>
          ${j.successful ? '<span>✓</span>' : ''}
        </div>
        <span>${logs.length} logs</span>
      </div>
      <div class="job-body" id="body-${j.id}">
        <div class="job-logs" id="logs-${j.id}"></div>
      </div>
    `;
    container.appendChild(accordion);
    renderLogs(j.id);
  }
}

function renderLogs(jobId) {
  const logsEl = document.getElementById(`logs-${jobId}`);
  if (!logsEl) return;
  const logs = jobLogs[jobId] || [];
  logsEl.innerHTML = logs.map(log => {
    const t = new Date(log.ts).toLocaleTimeString();
    return `<div class="log-entry ${log.level.toLowerCase()}"><span class="log-timestamp">[${t}]</span>${escapeHtml(log.message)}</div>`;
  }).join('');
  // auto-scroll to bottom if open
  const body = document.getElementById(`body-${jobId}`);
  if (body && body.classList.contains('open')) {
    logsEl.scrollTop = logsEl.scrollHeight;
  }
}

function toggleAccordion(jobId) {
  const body = document.getElementById(`body-${jobId}`);
  if (body) body.classList.toggle('open');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

window.toggleAccordion = toggleAccordion;

let ws;
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {};
  ws.onclose = () => { setTimeout(connectWS, 2000); };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'jobs') {
        renderJobs(msg.items || []);
      } else if (msg.type === 'job_update') {
        // fetch latest snapshot to merge, simplest approach
        api('/jobs').then(data => renderJobs(data.items || []));
      } else if (msg.type === 'log') {
        addLog(msg.job_id, msg.message, msg.level || 'INFO');
        renderLogs(msg.job_id);
        // refresh jobs list to update state
        api('/jobs').then(data => renderJobs(data.items || []));
      }
    } catch (e) {
      console.error('WS message error:', e);
    }
  };
}

function bindForms() {
  document.getElementById('form-doc').addEventListener('submit', async (e) => {
    e.preventDefault();
    const repo = document.getElementById('doc-repo').value || '/workspace';
    const useLLM = document.getElementById('doc-llm').checked;
    const manual = document.getElementById('doc-manual').checked;
    const template = document.getElementById('doc-template').value || 'api';
    const exportFormats = Array.from(document.querySelectorAll('.doc-export:checked')).map(x => x.value);
    await api('/jobs/doc', { method: 'POST', body: JSON.stringify({ repo_path: repo, use_llm: useLLM, manual_override: manual, template, export_formats: exportFormats }) });
    refreshJobs();
  });

  document.getElementById('form-tests').addEventListener('submit', async (e) => {
    e.preventDefault();
    const repo = document.getElementById('tests-repo').value || '/workspace';
    await api('/jobs/tests/generate-run', { method: 'POST', body: JSON.stringify({ repo_path: repo }) });
    refreshJobs();
  });

  document.getElementById('form-heal').addEventListener('submit', async (e) => {
    e.preventDefault();
    const repo = document.getElementById('heal-repo').value || '/workspace';
    const formatOnly = document.getElementById('heal-format').checked;
    await api('/jobs/self-heal', { method: 'POST', body: JSON.stringify({ repo_path: repo, format_only: formatOnly }) });
    refreshJobs();
  });

  document.getElementById('form-stage').addEventListener('submit', async (e) => {
    e.preventDefault();
    const repo = document.getElementById('stage-repo').value || '/workspace';
    const compose = document.getElementById('stage-compose').value || null;
    const service = document.getElementById('stage-service').value || null;
    const health = document.getElementById('stage-health').value || null;
    const timeout = parseInt(document.getElementById('stage-timeout').value || '120', 10);
    await api('/jobs/stage/validate', { method: 'POST', body: JSON.stringify({ repo_path: repo, compose_path: compose, service, health_url: health, timeout }) });
    refreshJobs();
  });
}

function start() {
  bindForms();
  refreshHealth();
  connectWS();
  setInterval(refreshHealth, 5000);
}

document.addEventListener('DOMContentLoaded', start);


