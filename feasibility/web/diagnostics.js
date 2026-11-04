'use strict';
(function () {

const ALLOWLISTED_ERROR_CODES = new Set([
  'COMPUTE_EXCEPTION', 'COMPUTE_TIMEOUT', 'COMPUTE_MEMORY', 'PROCESS_EXIT',
  'INTERRUPTED', 'QUEUE_TIMEOUT', 'SOLAR_TIMEOUT', 'SOLAR_NETWORK_ERROR',
  'SOLAR_DATA_GAPS', 'SOLAR_PROVIDER_ERROR', 'JOB_FAILED',
  'SYNTHETIC_COMPLETION_REQUIRED'
]);

const ACTIONABLE_EXPLANATIONS = {
  'COMPUTE_EXCEPTION': 'Calculation engine encountered an internal mathematical or logic error. Review inputs or inspect the sanitized bug report.',
  'COMPUTE_TIMEOUT': 'Calculation timed out before completing. Try reducing the calculation scope or selected weather years.',
  'COMPUTE_MEMORY': 'Calculation exceeded worker memory limit. Try reducing weather years or scenario complexity.',
  'PROCESS_EXIT': 'Calculation worker process terminated unexpectedly. Review inputs or report this issue.',
  'INTERRUPTED': 'Calculation was interrupted by server reload or restart. Retry the calculation.',
  'QUEUE_TIMEOUT': 'Calculation expired while waiting in queue. Retry when server capacity is available.',
  'SOLAR_TIMEOUT': 'Solar data request timed out; retry later.',
  'SOLAR_NETWORK_ERROR': 'Solar data request network error; retry later.',
  'SOLAR_DATA_GAPS': 'PVGIS data gaps exceed the supported repair limits. Review the selected weather years; no years were omitted.',
  'SOLAR_PROVIDER_ERROR': 'Solar provider request failed; review inputs and retry.',
  'JOB_FAILED': 'Job failed; review inputs and retry.',
  'SYNTHETIC_COMPLETION_REQUIRED': 'Interval training data does not cover required month/hour cells; enable explicit synthetic completion or supply complete coverage.'
};

const ALLOWLISTED_OPERATIONS = new Set([
  'compute', 'evaluate', 'quote', 'loan', 'dayhour', 'summary-sensitivity', 'matrix', 'study', 'solar'
]);

const ALLOWLISTED_ERROR_NAMES = new Set([
  'ComputationError', 'WorkerProcessDied', 'WorkerMemoryExceeded',
  'WorkerDeadlineExceeded', 'QueueTimeout', 'ServerInterrupted',
  'TimeoutError', 'ConnectionError', 'SolarDataGapError',
  'ZeroDivisionError', 'ValueError', 'TypeError', 'KeyError',
  'IndexError', 'OverflowError', 'FloatingPointError', 'RuntimeError'
]);

function isJobFailure(e) {
  if (!e) return false;
  const msg = e.message || String(e);
  return Boolean(
    e.jobId ||
    (e.code && typeof e.code === 'string' && (e.code.startsWith('COMPUTE_') || ALLOWLISTED_ERROR_CODES.has(e.code))) ||
    msg.startsWith('JOB_FAILED:') ||
    (e.diagnostic && (e.diagnostic.job_id || (e.diagnostic.error_code && ALLOWLISTED_ERROR_CODES.has(e.diagnostic.error_code)) || e.diagnostic.error_name))
  );
}

function generateBugReport(info) {
  const diag = info?.diagnostic || {};
  const rawCode = info?.code || diag.error_code || '';
  const code = ALLOWLISTED_ERROR_CODES.has(rawCode) ? rawCode : 'JOB_FAILED';
  const rawOp = diag.operation || '';
  const operation = ALLOWLISTED_OPERATIONS.has(rawOp) ? rawOp : (rawCode.startsWith('SOLAR_') ? 'solar' : 'evaluate');
  const rawName = diag.error_name || '';
  const errorType = ALLOWLISTED_ERROR_NAMES.has(rawName) ? rawName : 'ComputationError';

  const rawVersion = String(info?.version || diag.version || '').trim();
  const version = /^[0-9]+(\.[0-9]+)*([a-zA-Z0-9_.-]+)?$/.test(rawVersion) ? rawVersion : 'unknown';

  const rawBuildId = String(info?.modelBuildId || diag.model_build_id || '').trim();
  const modelBuildId = /^[0-9a-fA-F]{8,64}$/.test(rawBuildId) ? rawBuildId : 'unknown';

  const rawJobId = String(info?.jobId || diag.job_id || '').trim();
  const jobId = /^[0-9a-zA-Z_-]{8,64}$/.test(rawJobId) ? rawJobId : 'unknown';

  const actionableMessage = ACTIONABLE_EXPLANATIONS[code] || (operation === 'solar'
    ? 'Solar setup operation failed. Review the error code and stack summary below.'
    : 'Calculation failed. Review the error code and stack summary below.');

  const lines = [
    '### PV Lab Bug Report',
    '',
    '- **Version:** ' + version,
    '- **Model Build ID:** ' + modelBuildId,
    '- **Job Correlation ID:** ' + jobId,
    '- **Operation:** ' + operation,
    '- **Error Code:** ' + code,
    '- **Error Type:** ' + errorType,
    '- **Actionable Message:** ' + actionableMessage,
    ''
  ];

  const rawRepo = String(info?.repoUrl || diag.repo_url || '').trim();
  if (/^https:\/\/github\.com\/[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(rawRepo)) {
    lines.push('- **Repository:** ' + rawRepo);
    lines.push('');
  }

  if (Array.isArray(diag.traceback) && diag.traceback.length > 0) {
    const safeFrames = [];
    for (const frame of diag.traceback.slice(0, 12)) {
      if (typeof frame === 'string' && /^feasibility\/(?:[A-Za-z_][A-Za-z0-9_]*\/)*[A-Za-z_][A-Za-z0-9_]*\.py:\d+ in [A-Za-z_<>][A-Za-z0-9_<>]*$/.test(frame)) {
        safeFrames.push(frame.slice(0, 120));
      }
    }
    if (safeFrames.length > 0) {
      lines.push('#### Stack Summary (repo-relative)');
      lines.push('```text');
      for (const frame of safeFrames) {
        lines.push(frame);
      }
      lines.push('```');
    }
  }
  return lines.join('\n');
}

function renderDiagnosticReport(e, { doc = (typeof document !== 'undefined' ? document : null), expandable = false, downloadFn = null } = {}) {
  if (!doc) return null;
  const diagContainer = doc.createElement(expandable ? 'details' : 'div');
  diagContainer.className = 'job-failure-diagnostic' + (expandable ? ' diagnostic-details' : '');

  const summary = doc.createElement(expandable ? 'summary' : 'p');
  summary.className = 'diagnostic-summary';
  summary.textContent = 'Sanitized diagnostic details (safe to share; no personal data, coordinates, or inputs):';
  diagContainer.appendChild(summary);

  const bugReportText = generateBugReport(e);

  const pre = doc.createElement('pre');
  pre.className = 'diagnostic-pre';
  pre.setAttribute('tabindex', '0');
  pre.setAttribute('aria-label', 'Sanitized diagnostic bug report');
  pre.textContent = bugReportText;
  diagContainer.appendChild(pre);

  const actionsDiv = doc.createElement('div');
  actionsDiv.className = 'diagnostic-actions';

  const copyBtn = doc.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'button btn-sm copy-btn';
  copyBtn.textContent = 'Copy bug report';

  const downloadBtn = doc.createElement('button');
  downloadBtn.type = 'button';
  downloadBtn.className = 'button btn-sm download-btn';
  downloadBtn.textContent = 'Download bug report';

  actionsDiv.appendChild(copyBtn);
  actionsDiv.appendChild(downloadBtn);

  const repoUrl = e?.repoUrl || e?.diagnostic?.repo_url;
  if (typeof repoUrl === 'string' && /^https:\/\/github\.com\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(repoUrl)) {
    const issueLink = doc.createElement('a');
    issueLink.href = repoUrl + '/issues/new?template=bug_report.md';
    issueLink.target = '_blank';
    issueLink.rel = 'noopener noreferrer';
    if (typeof issueLink.setAttribute === 'function') {
      issueLink.setAttribute('href', repoUrl + '/issues/new?template=bug_report.md');
      issueLink.setAttribute('target', '_blank');
      issueLink.setAttribute('rel', 'noopener noreferrer');
    }
    issueLink.className = 'button btn-sm issue-link';
    issueLink.textContent = 'Open GitHub issue →';
    actionsDiv.appendChild(issueLink);
  }

  const statusSpan = doc.createElement('span');
  statusSpan.className = 'copy-status muted';
  statusSpan.setAttribute('role', 'status');
  statusSpan.setAttribute('aria-live', 'polite');
  actionsDiv.appendChild(statusSpan);

  copyBtn.onclick = async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(bugReportText);
        statusSpan.textContent = 'Copied to clipboard';
      } else if (typeof doc !== 'undefined' && doc.execCommand) {
        const ta = doc.createElement('textarea');
        ta.value = bugReportText;
        (doc.body || doc.documentElement || doc).appendChild(ta);
        ta.select();
        const successful = doc.execCommand('copy');
        (doc.body || doc.documentElement || doc).removeChild(ta);
        if (successful === true) {
          statusSpan.textContent = 'Copied to clipboard';
        } else {
          statusSpan.textContent = 'Copy failed; select text above';
        }
      } else {
        statusSpan.textContent = 'Copy unavailable; select text above';
      }
    } catch (_) {
      statusSpan.textContent = 'Copy failed; select text above';
    }
  };

  downloadBtn.onclick = () => {
    const filename = 'pv-lab-bug-report.txt';
    const mime = 'text/plain; charset=utf-8';
    const BlobCtor = typeof Blob !== 'undefined' ? Blob : (typeof window !== 'undefined' && window.Blob ? window.Blob : (typeof globalThis !== 'undefined' ? globalThis.Blob : null));
    const URLObj = typeof URL !== 'undefined' ? URL : (typeof window !== 'undefined' && window.URL ? window.URL : (typeof globalThis !== 'undefined' ? globalThis.URL : null));
    if (typeof downloadFn === 'function') {
      downloadFn(bugReportText, filename, mime);
      statusSpan.textContent = 'Downloaded bug report';
    } else if (typeof globalThis.download === 'function') {
      globalThis.download(bugReportText, filename, mime);
      statusSpan.textContent = 'Downloaded bug report';
    } else if (typeof window !== 'undefined' && typeof window.download === 'function') {
      window.download(bugReportText, filename, mime);
      statusSpan.textContent = 'Downloaded bug report';
    } else if (BlobCtor && URLObj && typeof URLObj.createObjectURL === 'function' && typeof doc.createElement === 'function') {
      try {
        const blob = new BlobCtor([bugReportText], { type: mime });
        const url = URLObj.createObjectURL(blob);
        const a = doc.createElement('a');
        a.href = url;
        a.download = filename;
        if (a.style) a.style.display = 'none';
        (doc.body || doc.documentElement || doc).appendChild(a);
        a.click();
        (doc.body || doc.documentElement || doc).removeChild(a);
        setTimeout(() => {
          try { URLObj.revokeObjectURL(url); } catch (_) {}
        }, 1000);
        statusSpan.textContent = 'Downloaded bug report';
      } catch (_) {
        statusSpan.textContent = 'Download failed; copy report above';
      }
    } else {
      statusSpan.textContent = 'Download unavailable; copy report above';
    }
  };

  diagContainer.appendChild(actionsDiv);
  return diagContainer;
}

const Diagnostics = {
  ALLOWLISTED_ERROR_CODES,
  ALLOWLISTED_OPERATIONS,
  ALLOWLISTED_ERROR_NAMES,
  ACTIONABLE_EXPLANATIONS,
  isJobFailure,
  generateBugReport,
  renderDiagnosticReport
};

if (typeof module !== 'undefined' && module.exports) module.exports = Diagnostics;
if (typeof window !== 'undefined') window.Diagnostics = Diagnostics;
if (typeof globalThis !== 'undefined') globalThis.Diagnostics = Diagnostics;
})();
