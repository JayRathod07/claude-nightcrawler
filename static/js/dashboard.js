/**
 * Claude Nightcrawler — Dashboard JavaScript
 *
 * Features:
 *   • Auto-refresh: polls /api/tasks every 8s and updates the table live
 *   • Claude status polling: updates the status pill without page reload
 *   • Character counter on the prompt textarea
 *   • Delete confirmation modal with keyboard (Escape) support
 *   • Success banner (shown when ?submitted=1 in URL), then URL cleaned
 *   • Live-refresh toggle (on/off)
 *   • LiquidGlass WebGL init for the topbar (deferred, respects reduced-motion)
 *   • markChanged() called after each task-table refresh so glass above sees
 *     updated content
 */

'use strict';

/* ── Config ─────────────────────────────────────────────────────────────────── */
const POLL_INTERVAL_MS = 8000;   // task table refresh interval
const STATUS_POLL_MS   = 15000;  // Claude status refresh interval
const MAX_CHARS        = 50000;

/* ── State ──────────────────────────────────────────────────────────────────── */
let autoRefresh    = true;
let taskPollTimer  = null;
let statusTimer    = null;
let pendingDelete  = null;   // task id waiting for modal confirm
let lgInstance     = null;   // LiquidGlass instance (topbar)

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

/* ════════════════════════════════════════════════════════════════════════════
   LIQUID GLASS — topbar WebGL initialisation
   ════════════════════════════════════════════════════════════════════════════ */

/**
 * Conditionally initialise one LiquidGlass WebGL instance for the topbar.
 * - Skipped entirely if user prefers reduced motion (no performance cost).
 * - Deferred via requestIdleCallback so it never blocks first paint.
 * - Only ONE instance total — no per-button WebGL contexts.
 */
function initLiquidGlass() {
  // Respect prefers-reduced-motion — skip WebGL entirely
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const root    = $('lg-root');
  const topbar  = $('topbar');
  if (!root || !topbar) return;

  // Configure the topbar glass element
  topbar.dataset.config = JSON.stringify({
    floating:      false,
    button:        false,
    blurAmount:    0.20,
    refraction:    0.55,
    chromAberration: 0.02,
    edgeHighlight: 0.12,
    specular:      0.08,
    fresnel:       0.80,
    distortion:    0.00,
    cornerRadius:  0,
    zRadius:       0,
    opacity:       1.00,
    saturation:    0.10,
    tintStrength:  0.00,
    brightness:    0.02,
    shadowOpacity: 0.00,
    bevelMode:     0,
  });

  // Dynamically import from CDN so it doesn't block the page
  import('https://cdn.jsdelivr.net/npm/@ybouane/liquidglass/dist/index.js')
    .then(({ LiquidGlass }) => {
      return LiquidGlass.init({
        root,
        glassElements: [topbar],
        defaults: {
          blurAmount:    0.20,
          refraction:    0.55,
          edgeHighlight: 0.12,
        },
      });
    })
    .then(instance => {
      lgInstance = instance;
      // Once we have an instance, mark it dirty after the first data load
      instance.markChanged();
    })
    .catch(() => {
      // CDN unavailable or WebGL not supported — CSS fallback is already in place
    });
}

// Defer LiquidGlass init until the browser is idle
if ('requestIdleCallback' in window) {
  requestIdleCallback(initLiquidGlass, { timeout: 3000 });
} else {
  setTimeout(initLiquidGlass, 500);
}

/* ════════════════════════════════════════════════════════════════════════════
   CHARACTER COUNTER
   ════════════════════════════════════════════════════════════════════════════ */
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

/* ════════════════════════════════════════════════════════════════════════════
   SUCCESS BANNER
   ════════════════════════════════════════════════════════════════════════════ */
(function checkSuccessBanner() {
  if (!successBanner) return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('submitted') === '1') {
    successBanner.classList.remove('hidden');
    history.replaceState({}, '', window.location.pathname);
    setTimeout(() => successBanner.classList.add('hidden'), 4000);
  }
})();

/* ════════════════════════════════════════════════════════════════════════════
   TASK TABLE RENDERING  (HTML builders)
   ════════════════════════════════════════════════════════════════════════════ */
function fmtDate(iso) {
  if (!iso) return '—';
  return String(iso).slice(0, 16).replace('T', ' ');
}
function statusLabel(s) {
  return { queued:'Queued', running:'Running', completed:'Completed',
           waiting_limit:'Waiting', failed:'Failed' }[s] || s;
}
function statusClass(s) {
  return { queued:'status-queued', running:'status-running',
           completed:'status-completed', waiting_limit:'status-waiting',
           failed:'status-failed' }[s] || '';
}
function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function buildTaskRow(t) {
  const preview = escHtml(t.prompt || '').slice(0, 120)
    + (t.prompt && t.prompt.length > 120 ? '…' : '');
  const errorHint = t.error_message
    ? `<div class="error-hint" title="${escHtml(t.error_message)}">
         ⚠ ${escHtml(t.error_message.slice(0, 60))}${t.error_message.length > 60 ? '…' : ''}
       </div>` : '';
  const spinner   = t.status === 'running'
    ? '<span class="spinner" aria-label="Processing…"></span>' : '';
  const dlBtn     = (t.status === 'completed' && t.result_path)
    ? `<a href="/results/${t.id}" class="btn btn-ghost btn-xs"
          title="Download result for task #${t.id}"
          aria-label="Download result for task #${t.id}" download>
         <span aria-hidden="true">↓</span> Result
       </a>` : '';
  const doneTime  = t.completed_at
    ? `<div class="time-sub">✓ ${fmtDate(t.completed_at)}</div>` : '';
  const startTime = (!t.completed_at && t.started_at)
    ? `<div class="time-sub">▶ ${fmtDate(t.started_at)}</div>` : '';

  return `
    <tr class="task-row" data-id="${t.id}" data-status="${t.status}">
      <td class="col-id"><span class="task-id">${t.id}</span></td>
      <td class="col-status">
        <span class="badge ${statusClass(t.status)}">${statusLabel(t.status)}</span>
        ${spinner}
      </td>
      <td class="col-prompt">
        <span class="prompt-preview" title="${escHtml(t.prompt)}">${preview}</span>
        ${errorHint}
      </td>
      <td class="col-priority" style="text-align:center">
        <span class="priority-badge ${t.priority > 0 ? 'priority-high' : ''}">${t.priority}</span>
      </td>
      <td class="col-time">
        <time class="time-cell" datetime="${t.created_at || ''}">${fmtDate(t.created_at)}</time>
        ${doneTime}${startTime}
      </td>
      <td class="col-actions">
        ${dlBtn}
        <button class="btn btn-danger btn-xs delete-btn"
                data-id="${t.id}"
                title="Delete task #${t.id}"
                aria-label="Delete task #${t.id}">✕</button>
      </td>
    </tr>`;
}

function renderEmptyState() {
  return `<tr class="empty-row">
    <td colspan="6">
      <div class="empty-state">
        <div class="empty-icon" aria-hidden="true">📭</div>
        <p class="empty-title">No tasks yet</p>
        <p class="empty-sub">Submit your first prompt above to get started.</p>
      </div>
    </td>
  </tr>`;
}

/* ════════════════════════════════════════════════════════════════════════════
   TASK POLLING
   ════════════════════════════════════════════════════════════════════════════ */
async function fetchAndUpdateTasks() {
  if (!taskTbody) return;

  const params = new URLSearchParams(window.location.search);
  const filter = params.get('status_filter') || 'all';
  const url    = `/api/tasks?limit=20${filter !== 'all' ? `&status_filter=${filter}` : ''}`;

  try {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) return;
    const { tasks } = await resp.json();

    // Update header stat chips
    if ($('stat-queued'))   $('stat-queued').textContent    = tasks.filter(t => t.status === 'queued').length;
    if ($('stat-completed'))$('stat-completed').textContent = tasks.filter(t => t.status === 'completed').length;
    if ($('stat-failed'))   $('stat-failed').textContent    = tasks.filter(t => t.status === 'failed').length;

    taskTbody.innerHTML = tasks.length > 0
      ? tasks.map(buildTaskRow).join('')
      : renderEmptyState();

    attachDeleteListeners();

    // Notify LiquidGlass that content under the glass has changed
    if (lgInstance) lgInstance.markChanged();

  } catch (_) { /* network error — retry on next tick */ }
}

/* ════════════════════════════════════════════════════════════════════════════
   CLAUDE STATUS POLLING
   ════════════════════════════════════════════════════════════════════════════ */
async function fetchClaudeStatus() {
  try {
    const resp = await fetch('/api/status', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const data = await resp.json();

    if (claudeLabel) claudeLabel.textContent = data.available ? 'Claude Ready' : 'Rate Limited';

    const pill = claudeLabel ? claudeLabel.closest('.status-pill') : null;
    if (pill) {
      pill.classList.toggle('status-pill--ok',   !!data.available);
      pill.classList.toggle('status-pill--warn', !data.available);
    }
    if ($('stat-requests')) $('stat-requests').textContent = data.total_requests_today ?? 0;
  } catch (_) {}
}

/* ════════════════════════════════════════════════════════════════════════════
   DELETE FLOW
   ════════════════════════════════════════════════════════════════════════════ */
function attachDeleteListeners() {
  document.querySelectorAll('.delete-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      pendingDelete = btn.dataset.id;
      const body = $('modal-body');
      if (body) body.textContent = `Delete task #${pendingDelete}? This cannot be undone.`;
      openModal();
    });
  });
}

function openModal() {
  if (!confirmModal) return;
  confirmModal.classList.remove('hidden');
  // Move focus into modal for keyboard users
  const firstBtn = confirmModal.querySelector('button');
  if (firstBtn) firstBtn.focus();
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
      const row = document.querySelector(`.task-row[data-id="${id}"]`);
      if (row) {
        row.style.transition = 'opacity 0.18s ease, transform 0.18s ease';
        row.style.opacity    = '0';
        row.style.transform  = 'translateX(8px)';
        setTimeout(() => row.remove(), 190);
      }
      if (lgInstance) lgInstance.markChanged();
    }
  } catch (_) { /* silently fail — next poll will correct */ }
}

if (modalConfirmBtn) modalConfirmBtn.addEventListener('click', executeDelete);
if (modalCancelBtn)  modalCancelBtn.addEventListener('click', closeModal);
if (confirmModal) {
  confirmModal.addEventListener('click', e => { if (e.target === confirmModal) closeModal(); });
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

/* ════════════════════════════════════════════════════════════════════════════
   AUTO-REFRESH TOGGLE
   ════════════════════════════════════════════════════════════════════════════ */
function startPolling() {
  stopPolling();
  taskPollTimer = setInterval(fetchAndUpdateTasks, POLL_INTERVAL_MS);
  statusTimer   = setInterval(fetchClaudeStatus,   STATUS_POLL_MS);
  if (refreshIcon)  { refreshIcon.style.animation  = 'spin 1.8s linear infinite'; }
  if (refreshLabel) { refreshLabel.textContent = 'Live'; }
  if (refreshToggle){ refreshToggle.setAttribute('aria-pressed', 'true'); }
}

function stopPolling() {
  clearInterval(taskPollTimer);
  clearInterval(statusTimer);
  taskPollTimer = statusTimer = null;
  if (refreshIcon)  { refreshIcon.style.animation  = 'none'; }
  if (refreshLabel) { refreshLabel.textContent = 'Paused'; }
  if (refreshToggle){ refreshToggle.setAttribute('aria-pressed', 'false'); }
}

if (refreshToggle) {
  refreshToggle.addEventListener('click', () => {
    autoRefresh = !autoRefresh;
    autoRefresh ? startPolling() : stopPolling();
  });
}

/* ════════════════════════════════════════════════════════════════════════════
   FORM SUBMIT FEEDBACK
   ════════════════════════════════════════════════════════════════════════════ */
if (submitForm) {
  submitForm.addEventListener('submit', () => {
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span aria-hidden="true">⏳</span> Adding…';
    }
  });
}

/* ════════════════════════════════════════════════════════════════════════════
   INIT — kick everything off
   ════════════════════════════════════════════════════════════════════════════ */
attachDeleteListeners();
startPolling();

// Immediate first fetch so the table is fresh on load
fetchAndUpdateTasks();
fetchClaudeStatus();
