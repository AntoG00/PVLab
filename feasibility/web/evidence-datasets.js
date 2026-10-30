/** Standalone Evidence Datasets Upload Component (PVGIS v6/v5.3 & Google Solar) */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else {
    root.EvidenceDatasets = factory();
    if (typeof document !== 'undefined') {
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => root.EvidenceDatasets.init());
      else root.EvidenceDatasets.init();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  let isUploading = false, maxFileBytes = 67108864; // 64 MiB

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'], i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function formatSource(s) {
    return s === 'pvgis6' ? 'PVGIS 6' : s === 'pvgis53' ? 'PVGIS 5.3' : s === 'google' ? 'Google Solar' : (s || '-');
  }

  function setupTabs(article) {
    const tabs = Array.from(article.querySelectorAll('[data-dataset-tab]'));
    const panels = { pvgis6: article.querySelector('#datasetPanelV6'), pvgis53: article.querySelector('#datasetPanelV53') };
    function selectTab(target) {
      tabs.forEach(t => {
        const isSel = t === target;
        t.setAttribute('aria-selected', isSel ? 'true' : 'false');
        t.tabIndex = isSel ? 0 : -1;
        t.classList.toggle('active', isSel);
        const panel = panels[t.getAttribute('data-dataset-tab')];
        if (panel) panel.hidden = !isSel;
      });
    }
    tabs.forEach((tab, idx) => {
      tab.addEventListener('click', () => selectTab(tab));
      tab.addEventListener('keydown', e => {
        let nIdx = null;
        if (e.key === 'ArrowRight') nIdx = (idx + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') nIdx = (idx - 1 + tabs.length) % tabs.length;
        else if (e.key === 'Home') nIdx = 0;
        else if (e.key === 'End') nIdx = tabs.length - 1;
        if (nIdx !== null) { e.preventDefault(); tabs[nIdx].focus(); selectTab(tabs[nIdx]); }
      });
    });
    const initial = tabs.find(t => t.getAttribute('aria-selected') === 'true' || t.classList.contains('active')) || tabs[0];
    if (initial) selectTab(initial);
  }

  function renderTable(tableBody, datasets, onSelection) {
    tableBody.textContent = '';
    if (!datasets || datasets.length === 0) {
      const row = document.createElement('tr'), cell = document.createElement('td');
      cell.colSpan = 5; cell.className = 'evidence-empty-msg'; cell.textContent = 'No datasets uploaded yet.';
      row.appendChild(cell); return tableBody.appendChild(row);
    }
    datasets.forEach(ds => {
      const row = document.createElement('tr'), sum = ds.summary || {};

      // 1. Source + reversible coverage preview toggle for PVGIS hourly
      const c1 = document.createElement('td');
      if ((ds.source === 'pvgis6' || ds.source === 'pvgis53') && sum.kind === 'hourly') {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'evidence-toggle-coverage';
        cb.checked = ds.included !== false;
        cb.setAttribute('aria-label', `Include in preview coverage (non-calculator input): ${ds.filename || ''}`);
        if (typeof onSelection === 'function') cb.addEventListener('change', () => onSelection(ds, cb));
        c1.appendChild(cb);
        c1.appendChild(document.createTextNode(' '));
      }
      const srcText = document.createElement('span');
      srcText.textContent = formatSource(ds.source);
      c1.appendChild(srcText);
      row.appendChild(c1);

      // 2. File
      const c2 = document.createElement('td'), fn = document.createElement('div'), fm = document.createElement('div');
      fn.className = 'evidence-filename'; fn.textContent = ds.filename || '-';
      fm.className = 'evidence-meta';
      const meta = [ds.size_bytes != null ? formatBytes(ds.size_bytes) : null, ds.uploaded_at ? new Date(ds.uploaded_at).toLocaleDateString() : null].filter(Boolean);
      fm.textContent = meta.join(' • ');
      c2.append(fn, fm); row.appendChild(c2);

      // 3. Series
      const c3 = document.createElement('td'); c3.textContent = ds.series_label || '-'; row.appendChild(c3);

      // 4. Coverage
      const c4 = document.createElement('td'), isMon = sum.kind === 'monthly_summary';
      const range = (sum.start && sum.end) ? `${sum.start} → ${sum.end} UTC` : (sum.start ? `From ${sum.start} UTC` : '');
      const rows = sum.row_count != null ? `${sum.row_count} rows` : '';
      c4.textContent = isMon ? 'Monthly summary (reference only)' : ([range, rows].filter(Boolean).join(' • ') || (sum.provider === 'Raster' ? 'Raster layer (reference only)' : 'Reference evidence; no hourly coverage'));
      row.appendChild(c4);

      // 5. Validation
      const c5 = document.createElement('td'), valFmt = document.createElement('div');
      valFmt.textContent = sum.format ? String(sum.format).toUpperCase() : 'VALID';
      c5.appendChild(valFmt);
      if (Array.isArray(sum.warnings) && sum.warnings.length > 0) {
        const warn = document.createElement('div'); warn.className = 'evidence-warning'; warn.textContent = `⚠ ${sum.warnings.join('; ')}`;
        c5.appendChild(warn);
      }
      row.appendChild(c5); tableBody.appendChild(row);
    });
  }

  function renderCoverage(container, coverageList) {
    container.textContent = '';
    if (!coverageList || coverageList.length === 0) {
      const empty = document.createElement('p'); empty.className = 'evidence-meta'; empty.textContent = 'No coverage data available.';
      return container.appendChild(empty);
    }
    coverageList.forEach(cov => {
      const card = document.createElement('div'), header = document.createElement('div');
      card.className = 'evidence-cov-card'; header.className = 'evidence-cov-header';
      const title = document.createElement('strong'); title.textContent = `Series: ${cov.series_label || 'Unnamed'}`;
      const badge = document.createElement('span'); badge.className = `evidence-badge evidence-badge-${cov.status === 'ready' ? 'ready' : 'conflict'}`;
      badge.textContent = cov.status ? cov.status.toUpperCase() : 'UNKNOWN';
      header.append(title, badge); card.appendChild(header);

      const metrics = document.createElement('div'); metrics.className = 'evidence-meta';
      metrics.textContent = `PVGIS v6: ${cov.v6_hours ?? 0}h (priority) | v5.3: ${cov.v53_hours ?? 0}h (fallback) | Total: ${cov.total_hours ?? 0}h | Overlap: ${cov.overlap_hours ?? 0}h`;
      card.appendChild(metrics);

      if (cov.start || cov.end) {
        const range = document.createElement('div'); range.className = 'evidence-meta';
        range.textContent = `Range: ${cov.start || 'N/A'} → ${cov.end || 'N/A'}`; card.appendChild(range);
      }
      if (cov.status === 'conflict' || cov.error) {
        const alert = document.createElement('div'); alert.className = 'evidence-error';
        alert.textContent = `Conflict/Error: ${cov.error || 'Discrepancy detected between v6 and v5.3 ranges.'}`; card.appendChild(alert);
      }
      if (Array.isArray(cov.warnings) && cov.warnings.length > 0) {
        const list = document.createElement('ul'); list.className = 'evidence-warning-list';
        cov.warnings.forEach(w => { const li = document.createElement('li'); li.textContent = w; list.appendChild(li); });
        card.appendChild(list);
      }
      container.appendChild(card);
    });
  }

  async function loadData(article, isPostUpload = false) {
    const tableBody = article.querySelector('#datasetRows'), coverageEl = article.querySelector('#datasetCoverage');
    const statusEl = article.querySelector('#datasetUploadStatus');
    try {
      const res = await fetch('/api/evidence/datasets');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (Number.isFinite(data.max_file_bytes)) maxFileBytes = data.max_file_bytes;
      if (tableBody) {
        renderTable(tableBody, data.datasets || [], async (ds, cb) => {
          const nextChecked = cb.checked;
          cb.disabled = true;
          try {
            const pRes = await fetch('/api/evidence/datasets/selection', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: ds.id, included: nextChecked })
            });
            if (!pRes.ok) throw new Error((await pRes.text()) || `HTTP ${pRes.status}`);
            if (statusEl) {
              statusEl.className = 'status-msg info';
              statusEl.textContent = 'Coverage selection changes the preview only; stored files and active calculations are retained.';
            }
            await loadData(article, true);
          } catch (err) {
            cb.checked = !nextChecked;
            if (statusEl) {
              statusEl.className = 'status-msg error';
              statusEl.textContent = `Failed updating selection for ${ds.filename}: ${err.message}`;
            }
          } finally {
            cb.disabled = false;
          }
        });
      }
      if (coverageEl) renderCoverage(coverageEl, data.coverage || []);
    } catch (err) {
      if (statusEl) {
        const loadErr = `(Catalog refresh failed: ${err.message})`;
        if (isPostUpload && statusEl.textContent) statusEl.textContent = `${statusEl.textContent} ${loadErr}`;
        else { statusEl.className = 'status-msg error'; statusEl.textContent = `Failed loading evidence datasets: ${err.message}`; }
      }
    }
  }

  async function handleUpload(article) {
    if (isUploading) return;
    const btn = article.querySelector('#datasetUpload'), statusEl = article.querySelector('#datasetUploadStatus');
    const seriesInput = article.querySelector('#datasetSeriesLabel');
    const v6Input = article.querySelector('#datasetFilesV6'), v53Input = article.querySelector('#datasetFilesV53'), googleInput = article.querySelector('#datasetFilesGoogle');
    const seriesLabel = (seriesInput ? seriesInput.value : '').trim();
    const v6Files = Array.from(v6Input?.files || []), v53Files = Array.from(v53Input?.files || []), googleFiles = Array.from(googleInput?.files || []);

    if (seriesLabel.length > 80) {
      if (statusEl) { statusEl.className = 'status-msg error'; statusEl.textContent = 'Series label must not exceed 80 characters.'; }
      return;
    }
    if ((v6Files.length > 0 || v53Files.length > 0) && !seriesLabel) {
      if (statusEl) { statusEl.className = 'status-msg error'; statusEl.textContent = 'Series label is required when uploading PVGIS datasets.'; }
      return;
    }
    const queue = [...v6Files.map(f => ({ file: f, source: 'pvgis6' })), ...v53Files.map(f => ({ file: f, source: 'pvgis53' })), ...googleFiles.map(f => ({ file: f, source: 'google' }))];
    if (queue.length === 0) {
      if (statusEl) { statusEl.className = 'status-msg warning'; statusEl.textContent = 'No files selected to upload.'; }
      return;
    }

    isUploading = true;
    if (btn) btn.disabled = true;
    let successCount = 0;
    const errors = [];
    try {
      for (let i = 0; i < queue.length; i++) {
        const item = queue[i];
        if (statusEl) { statusEl.className = 'status-msg info'; statusEl.textContent = `Uploading ${i + 1}/${queue.length}: ${item.file.name}...`; }
        if (item.file.size > maxFileBytes) { errors.push(`${item.file.name}: exceeds limit (${formatBytes(maxFileBytes)})`); continue; }
        try {
          const res = await fetch('/api/evidence/datasets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream', 'X-Dataset-Source': item.source, 'X-Dataset-Filename': encodeURIComponent(item.file.name), 'X-Dataset-Series': encodeURIComponent(seriesLabel) },
            body: item.file
          });
          if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
          successCount++;
        } catch (postErr) { errors.push(`${item.file.name}: ${postErr.message}`); }
      }
      if (errors.length === 0) {
        if (v6Input) v6Input.value = ''; if (v53Input) v53Input.value = ''; if (googleInput) googleInput.value = '';
        if (statusEl) { statusEl.className = 'status-msg success'; statusEl.textContent = `Successfully uploaded all ${successCount} file(s).`; }
      } else if (statusEl) {
        statusEl.className = 'status-msg error'; statusEl.textContent = `Uploaded ${successCount}/${queue.length}. Errors: ${errors.join('; ')}`;
      }
      await loadData(article, true);
    } finally {
      isUploading = false;
      if (btn) btn.disabled = false;
    }
  }

  function init() {
    const article = document.getElementById('datasetEvidence');
    if (!article) return false;
    if (article.dataset.evidenceDatasetsInit === 'true') { loadData(article); return true; }
    article.dataset.evidenceDatasetsInit = 'true';

    const seriesInput = article.querySelector('#datasetSeriesLabel');
    if (seriesInput && !seriesInput.hasAttribute('maxlength')) seriesInput.setAttribute('maxlength', '80');

    setupTabs(article);
    const uploadBtn = article.querySelector('#datasetUpload');
    if (uploadBtn) uploadBtn.addEventListener('click', () => handleUpload(article));
    loadData(article);
    return true;
  }

  return { init, handleUpload, loadData, renderTable, renderCoverage, formatBytes, formatSource };
});
