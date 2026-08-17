/**
 * Claude Nightcrawler — Dashboard JavaScript
 *
 * Features:
 *   • Auto-refresh: polls /api/tasks every 8s and updates the table live
 *   • Claude status polling: updates the status pill without page reload
 *   • Character counter on the prompt textarea
 *   • Delete confirmation modal
 *   • Success banner (shown when ?submitted=1 in URL)
 *   • Refresh toggle (on/off)
 */

'use strict';

/* ── Config ─────────────────────────────────────────────────────────────────── */
const POLL_INTERVAL_MS = 8000;  // how often to poll for updates
const STATUS_POLL_MS   = 15000; // how often to check Claude status
const MAX_CHARS        = 50000;

/* ── State ──────────────────────────────────────────────────────────────────── */
let autoRefresh   = true;
let taskPollTimer = null;
let statusTimer   = null;
let pendingDelete = null;   // task id waiting for modal confirm

/* ── DOM refs ───────────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);

const promptInput     = $('prompt-input');
const charCounter     = $('char-counter');
const submitBtn       = $('submit-btn');
const submitForm      = $('submit-form');
const taskTbody       = $('task-tbody');
const refreshToggle   = $('refresh-toggle');
const refreshIcon     = $('refresh-icon');
const refreshLabel    = $('refresh-label');
const successBanner   = $('success-banner');
const confirmModal    = $('confirm-modal');
const modalConfirmBtn = $('modal-confirm');
const modalCancelBtn  = $('modal-cancel');
const claudeLabel     = $('claude-status-label');

/* ── Helpers ────────────────────────────────────────────────────────────────── */
function fmtDate(iso) {
  if (!iso) return '—';
  return iso.slice(0, 16).replace('T', ' ');
}

function statusLabel(s) {
  return { queued:'Queued', running:'Running', completed:'Completed',
           waiting_limit:'Waiting', failed:'Failed' }[s] || s;
}
function statusClass(s) {
  return { queued:'status-queued', running:'status-running', completed:'status-completed',
           waiting_limit:'status-waiting', failed:'status-failed' }[s] || '';
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── Character counter ──────────────────────────────────────────────────────── */
function updateCharCounter() {
  if (!promptInput || !charCounter) return;
  const len = promptInput.value.length;
  charCounter.textContent = `${len.toLocaleString()} / ${MAX_CHARS.toLocaleString()}`;
  charCounter.classList.toggle('near-limit', len > MAX_CHARS * 0.85);
  charCounter.classList.toggle('at-limit',   len >= MAX_CHARS);
  if (submitBtn) submitBtn.disabled = len === 0 || len > MAX_CHARS;
}

if (promptInput) {
  promptInput.addEventListener('input', updateCharCounter);
  updateCharCounter();
}

/* ── Success banner ─────────────────────────────────────────────────────────── */
(function checkSuccessBanner() {
  if (!successBanner) return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('submitted') === '1') {
    successBanner.classList.remove('hidden');
    // Clean URL without reloading
    history.replaceState({}, '', window.location.pathname);
    setTimeout(() => successBanner.classList.add('hidden'), 4000);
  }
})();

/* ── Task table rendering ───────────────────────────────────────────────────── */
function buildTaskRow(t) {
  const promptPreview = escapeHtml(t.prompt || '').slice(0, 120)
    + (t.prompt && t.prompt.length > 120 ? '…' : '');
  const errorHint = t.error_message
    ? `<div class="error-hint" title="${escapeHtml(t.error_message)}">
         ⚠ ${escapeHtml(t.error_message.slice(0, 60))}${t.error_message.length > 60 ? '…' : ''}
       </div>`
    : '';
  const spinner = t.status === 'running'
    ? '<span class="spinner" aria-label="Processing"></span>' : '';
  const downloadBtn = (t.status === 'completed' && t.result_path)
    ? `<a href="/results/${t.id}" class="btn btn-ghost btn-xs" title="Download result" download>↓ Result</a>` : '';
  const completedTime = t.completed_at
    ? `<div class="time-sub">✓ ${fmtDate(t.completed_at)}</div>` : '';
  const startedTime = (!t.completed_at && t.started_at)
    ? `<div class="time-sub">▶ ${fmtDate(t.started_at)}</div>` : '';

  return `
    <tr class="task-row" data-id="${t.id}" data-status="${t.status}">
      <td class="col-id"><span class="task-id">${t.id}</span></td>
      <td class="col-status">
        <span class="badge ${statusClass(t.status)}">${statusLabel(t.status)}</span>
        ${spinner}
      </td>
      <td class="col-prompt">
        <span class="prompt-preview" title="${escapeHtml(t.prompt)}">${promptPreview}</span>
        ${errorHint}
      </td>
      <td class="col-priority" style="text-align:center">
        <span class="priority-badge ${t.priority > 0 ? 'priority-high' : ''}">${t.priority}</span>
      </td>
      <td class="col-time">
        <time class="time-cell" datetime="${t.created_at || ''}">${fmtDate(t.created_at)}</time>
        ${completedTime}${startedTime}
      </td>
      <td class="col-actions">
        ${downloadBtn}
        <button class="btn btn-danger btn-xs delete-btn" data-id="${t.id}" title="Delete task">✕</button>
      </td>
    </tr>`;
}

function renderEmptyState() {
  return `<tr class="empty-row">
    <td colspan="6">
      <div class="empty-state">
        <div class="empty-icon">📭</div>
        <p class="empty-title">No tasks yet</p>
        <p class="empty-sub">Submit your first prompt above to get started.</p>
      </div>
    </td>
  </tr>`;
}

/* ── Task polling ───────────────────────────────────────────────────────────── */
async function fetchAndUpdateTasks() {
  if (!taskTbody) return;

  // Build query with any active status filter
  const params = new URLSearchParams(window.location.search);
  const filter = params.get('status_filter') || 'all';
  const url = `/api/tasks?limit=20${filter !== 'all' ? `&status_filter=${filter}` : ''}`;

  try {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) return;  // will retry next tick
    const { tasks } = await resp.json();

    // Update task count badges in header
    if ($('stat-queued'))   $('stat-queued').textContent   = tasks.filter(t=>t.status==='queued').length;
    if ($('stat-completed'))$('stat-completed').textContent = tasks.filter(t=>t.status==='completed').length;
    if ($('stat-failed'))   $('stat-failed').textContent   = tasks.filter(t=>t.status==='failed').length;

    const html = tasks.length > 0
      ? tasks.map(buildTaskRow).join('')
      : renderEmptyState();

    taskTbody.innerHTML = html;

    // Re-attach delete listeners after DOM replace
    attachDeleteListeners();
  } catch (_) {
    // Network error — silently retry next tick
  }
}

/* ── Claude status polling ──────────────────────────────────────────────────── */
async function fetchClaudeStatus() {
  try {
    const resp = await fetch('/api/status', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const data = await resp.json();
    const available = data.available;

    if (claudeLabel) {
      claudeLabel.textContent = available ? 'Claude Ready' : 'Rate Limited';
    }

    // Update pill class on the parent
    const pill = claudeLabel ? claudeLabel.closest('.status-pill') : null;
    if (pill) {
      pill.classList.toggle('status-pill--ok',   available);
      pill.classList.toggle('status-pill--warn', !available);
    }

    // Update requests today
    if ($('stat-requests')) {
      $('stat-requests').textContent = data.total_requests_today ?? 0;
    }
  } catch (_) {}
}

/* ── Delete flow ─────────────────────────────────────────────────────────────── */
function attachDeleteListeners() {
  document.querySelectorAll('.delete-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      pendingDelete = btn.dataset.id;
      const body = $('modal-body');
      if (body) body.textContent = `Delete task #${pendingDelete}? This cannot be undone.`;
      if (confirmModal) confirmModal.classList.remove('hidden');
    });
  });
}

function closeModal() {
  if (confirmModal) confirmModal.classList.add('hidden');
  pendingDelete = null;
}

async function executeDelete() {
  if (!pendingDelete) return;
  const id = pendingDelete;
  closeModal();

  try {
    const resp = await fetch(`/tasks/${id}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    if (resp.ok) {
      // Remove row immediately for snappy UX
      const row = document.querySelector(`.task-row[data-id="${id}"]`);
      if (row) {
        row.style.opacity = '0';
        row.style.transition = 'opacity 0.2s ease';
        setTimeout(() => row.remove(), 200);
      }
    }
  } catch (_) {
    // Silently fail — table will correct itself on next poll
  }
}

if (modalConfirmBtn) modalConfirmBtn.addEventListener('click', executeDelete);
if (modalCancelBtn)  modalCancelBtn.addEventListener('click', closeModal);
if (confirmModal) {
  confirmModal.addEventListener('click', e => {
    if (e.target === confirmModal) closeModal();
  });
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

/* ── Auto-refresh toggle ────────────────────────────────────────────────────── */
function startPolling() {
  stopPolling();
  taskPollTimer  = setInterval(fetchAndUpdateTasks, POLL_INTERVAL_MS);
  statusTimer    = setInterval(fetchClaudeStatus,   STATUS_POLL_MS);
  if (refreshIcon)  refreshIcon.style.animation  = 'spin 1.5s linear infinite';
  if (refreshLabel) refreshLabel.textContent = 'Live';
}

function stopPolling() {
  clearInterval(taskPollTimer);
  clearInterval(statusTimer);
  taskPollTimer = null;
  statusTimer   = null;
  if (refreshIcon)  refreshIcon.style.animation  = 'none';
  if (refreshLabel) refreshLabel.textContent = 'Paused';
}

if (refreshToggle) {
  refreshToggle.addEventListener('click', () => {
    autoRefresh = !autoRefresh;
    autoRefresh ? startPolling() : stopPolling();
  });
}

/* ── Form submit feedback ───────────────────────────────────────────────────── */
if (submitForm) {
  submitForm.addEventListener('submit', () => {
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Adding…';
    }
  });
}

/* ── Init ────────────────────────────────────────────────────────────────────── */
attachDeleteListeners();
startPolling();

// Immediately fetch fresh data on load
fetchAndUpdateTasks();
fetchClaudeStatus();
