(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) module.exports = factory();
  else root.ConsumptionCsv = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MAX_BYTES = 16 * 1024 * 1024;
  const MAX_ROWS = 250000;
  const NUM_REGEX = /^[0-9]+(?:,[0-9]+)?(?:[eE][+-]?[0-9]+)?$/;

  const INTERVALS = [], START_TIMES = [];
  for (let i = 0; i < 96; i++) {
    const sMin = i * 15, eMin = (i + 1) * 15;
    const sH = String(Math.floor(sMin / 60)).padStart(2, "0"), sM = String(sMin % 60).padStart(2, "0");
    const eH = String(Math.floor(eMin / 60) % 24).padStart(2, "0"), eM = String(eMin % 60).padStart(2, "0");
    INTERVALS.push(sH + ":" + sM + "-" + eH + ":" + eM);
    START_TIMES.push(sH + ":" + sM + ":00");
  }

  const dtfRome = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Rome",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hourCycle: "h23"
  });

  function getRomeMidnight(y, m, d) {
    for (const offset of [1, 2]) {
      const utcMs = Date.UTC(y, m - 1, d, -offset, 0, 0);
      const parts = {};
      for (const p of dtfRome.formatToParts(new Date(utcMs))) parts[p.type] = p.value;
      if (Number(parts.year) === y && Number(parts.month) === m && Number(parts.day) === d &&
          parts.hour === "00" && parts.minute === "00") {
        return { offset: "+0" + offset + ":00", utcMs };
      }
    }
    throw new Error("Unresolved Rome midnight for " + y + "-" + m + "-" + d);
  }

  function parseCsv(text, maxRows) {
    if (typeof text !== "string") throw new Error("CSV text required");
    const s = text.charCodeAt(0) === 0xFEFF ? text.slice(1) : text;
    const rows = [];
    let row = [], field = "", state = 0;
    const len = s.length;

    for (let i = 0; i < len; i++) {
      const ch = s[i];
      if (state === 0) {
        if (ch === "\"") { state = 2; }
        else if (ch === ";") { row.push(""); }
        else if (ch === "\r" || ch === "\n") {
          if (ch === "\r" && i + 1 < len && s[i + 1] === "\n") i++;
          row.push(""); rows.push(row); row = [];
          if (maxRows && rows.length >= maxRows) return rows;
        } else { field += ch; state = 1; }
      } else if (state === 1) {
        if (ch === "\"") throw new Error("Quote inside unquoted field");
        else if (ch === ";") { row.push(field); field = ""; state = 0; }
        else if (ch === "\r" || ch === "\n") {
          if (ch === "\r" && i + 1 < len && s[i + 1] === "\n") i++;
          row.push(field); field = ""; state = 0; rows.push(row); row = [];
          if (maxRows && rows.length >= maxRows) return rows;
        } else { field += ch; }
      } else if (state === 2) {
        if (ch === "\"") {
          if (i + 1 < len && s[i + 1] === "\"") { field += "\""; i++; }
          else { state = 3; }
        } else { field += ch; }
      } else if (state === 3) {
        if (ch === ";") { row.push(field); field = ""; state = 0; }
        else if (ch === "\r" || ch === "\n") {
          if (ch === "\r" && i + 1 < len && s[i + 1] === "\n") i++;
          row.push(field); field = ""; state = 0; rows.push(row); row = [];
          if (maxRows && rows.length >= maxRows) return rows;
        } else if (ch === " " || ch === "\t") {
          // allowable whitespace after close quote
        } else {
          throw new Error("Non-whitespace characters after closing quote");
        }
      }
    }
    if (state === 2) throw new Error("Unclosed quote in CSV");
    if (state === 1 || state === 3 || row.length > 0) {
      row.push(field);
      rows.push(row);
    }
    while (rows.length > 0 && rows[rows.length - 1].length === 1 && rows[rows.length - 1][0].trim() === "") {
      rows.pop();
    }
    return rows;
  }

  function detectEdistribuzione(text) {
    if (typeof text !== "string" || !text) return false;
    try {
      const rows = parseCsv(text, 1);
      if (!rows.length || rows[0].length < 2) return false;
      return rows[0][0].trim() === "Giorno" && rows[0][1].trim() === "00:00-00:15";
    } catch (_) {
      return false;
    }
  }

  function parseStrictDate(str) {
    const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec((str || "").trim());
    if (!m) return null;
    const day = Number(m[1]), month = Number(m[2]), year = Number(m[3]);
    if (year < 1900 || year > 2100 || month < 1 || month > 12) return null;
    if (day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null;
    return { year, month, day, iso: year + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0") };
  }

  function parseNum(str) {
    const t = typeof str === "string" ? str.trim() : "";
    if (!t || t.includes(".") || !NUM_REGEX.test(t)) return null;
    const v = Number(t.replace(",", "."));
    return Number.isFinite(v) && v >= 0 ? v : null;
  }

  function neumaierSum(arr) {
    let sum = 0, c = 0;
    for (let i = 0; i < arr.length; i++) {
      const x = arr[i], t = sum + x;
      c += Math.abs(sum) >= Math.abs(x) ? (sum - t) + x : (x - t) + sum;
      sum = t;
    }
    return sum + c;
  }

  async function convertEdistribuzione(files) {
    if (!Array.isArray(files) || files.length === 0) throw new Error("Empty file list");

    let wideBytes = 0;
    const parsedDays = [];
    const encoder = typeof TextEncoder !== "undefined" ? new TextEncoder() : null;
    const byteLen = s => (encoder ? encoder.encode(s).length : Buffer.byteLength(s, "utf8"));

    for (let f = 0; f < files.length; f++) {
      const file = files[f];
      if (!file || typeof file.text !== "function") throw new Error("Invalid file object at index " + f);
      if (typeof file.size === "number" && file.size > MAX_BYTES) throw new Error("File size exceeds 16MiB limit");

      const text = await file.text();
      wideBytes += byteLen(text);
      if (wideBytes > MAX_BYTES) throw new Error("Wide files combined size exceeds 16MiB");

      const rows = parseCsv(text);
      if (!rows.length) throw new Error("Empty CSV file: " + (file.name || f));

      let hdr = rows[0];
      if (hdr.length === 98 && hdr[97] === "") hdr = hdr.slice(0, 97);
      if (hdr.length !== 97 || hdr[0].trim() !== "Giorno") {
        throw new Error("Inconsistent/malformed header in " + (file.name || f));
      }
      for (let k = 0; k < 96; k++) {
        if (hdr[k + 1].trim() !== INTERVALS[k]) {
          throw new Error("Inconsistent header interval " + (k + 1) + " in " + (file.name || f));
        }
      }

      for (let r = 1; r < rows.length; r++) {
        let rCells = rows[r];
        if (rCells.length === 98 && rCells[97] === "") rCells = rCells.slice(0, 97);
        if (rCells.length !== 97) throw new Error("Malformed row " + (r + 1) + " in " + (file.name || f));

        const dateInfo = parseStrictDate(rCells[0]);
        if (!dateInfo) throw new Error("Invalid date in row " + (r + 1) + " of " + (file.name || f));

        const values = new Array(96);
        for (let k = 0; k < 96; k++) {
          const v = parseNum(rCells[k + 1]);
          if (v === null) throw new Error("Invalid value in row " + (r + 1) + " col " + (k + 2) + " of " + (file.name || f));
          values[k] = v;
        }
        parsedDays.push({ dateInfo, values });
      }
    }

    if (!parsedDays.length) throw new Error("No data rows found");
    parsedDays.sort((a, b) => a.dateInfo.iso.localeCompare(b.dateInfo.iso));

    for (let i = 0; i < parsedDays.length - 1; i++) {
      const cur = parsedDays[i].dateInfo, nxt = parsedDays[i + 1].dateInfo;
      if (cur.iso === nxt.iso) throw new Error("Duplicate date: " + cur.iso);
      const t1 = Date.UTC(cur.year, cur.month - 1, cur.day);
      const t2 = Date.UTC(nxt.year, nxt.month - 1, nxt.day);
      if (Math.round((t2 - t1) / 86400000) !== 1) throw new Error("Gap between " + cur.iso + " and " + nxt.iso);
    }

    const totalDays = parsedDays.length;
    if (totalDays > MAX_ROWS || totalDays * 96 > MAX_ROWS) throw new Error("Row limit exceeded");

    const csvLines = ["timestamp,energy_kwh"], allValues = [];
    const monthlyBuckets = Array.from({ length: 12 }, () => []);
    const dstDays = [];

    for (let d = 0; d < totalDays; d++) {
      const item = parsedDays[d], iso = item.dateInfo.iso, mIdx = item.dateInfo.month - 1;
      const m1 = getRomeMidnight(item.dateInfo.year, item.dateInfo.month, item.dateInfo.day).utcMs;
      const nextDate = new Date(Date.UTC(item.dateInfo.year, item.dateInfo.month - 1, item.dateInfo.day + 1));
      const m2 = getRomeMidnight(nextDate.getUTCFullYear(), nextDate.getUTCMonth() + 1, nextDate.getUTCDate()).utcMs;
      if (Math.round((m2 - m1) / 3600000) !== 24) dstDays.push(iso);

      for (let k = 0; k < 96; k++) {
        const v = item.values[k];
        allValues.push(v);
        monthlyBuckets[mIdx].push(v);
        csvLines.push(iso + "T" + START_TIMES[k] + "," + v);
      }
    }

    const convertedText = csvLines.join("\n") + "\n";
    if (wideBytes + byteLen(convertedText) > MAX_BYTES) throw new Error("Combined bytes exceed 16MiB");

    let fileBlob;
    if (typeof File !== "undefined") {
      try { fileBlob = new File([convertedText], "consumption.csv", { type: "text/csv" }); }
      catch (_) { fileBlob = new Blob([convertedText], { type: "text/csv" }); }
    } else {
      fileBlob = new Blob([convertedText], { type: "text/csv" });
    }
    if (fileBlob.name !== "consumption.csv") {
      try { fileBlob.name = "consumption.csv"; }
      catch (_) { Object.defineProperty(fileBlob, "name", { value: "consumption.csv", configurable: true }); }
    }

    const totalKwh = neumaierSum(allValues);
    if (!Number.isFinite(totalKwh)) throw new Error("Non-finite total energy");

    const monthlyTotals = new Array(12).fill(null), monthsPresent = [];
    for (let m = 0; m < 12; m++) {
      if (monthlyBuckets[m].length > 0) {
        monthlyTotals[m] = neumaierSum(monthlyBuckets[m]);
        monthsPresent.push(m + 1);
      }
    }

    const first = parsedDays[0].dateInfo, last = parsedDays[totalDays - 1].dateInfo;
    const startDate = first.iso;
    const nextUtc = new Date(Date.UTC(last.year, last.month - 1, last.day + 1));
    const endY = nextUtc.getUTCFullYear(), endM = nextUtc.getUTCMonth() + 1, endD = nextUtc.getUTCDate();
    const endDate = endY + "-" + String(endM).padStart(2, "0") + "-" + String(endD).padStart(2, "0");

    const coverageStart = startDate + "T00:00:00" + getRomeMidnight(first.year, first.month, first.day).offset;
    const coverageEnd = endDate + "T00:00:00" + getRomeMidnight(endY, endM, endD).offset;

    return { file: fileBlob, days: totalDays, totalKwh, monthlyTotals, monthsPresent, startDate, endDate, coverageStart, coverageEnd, dstDays };
  }

  function bindInput({ fields, container, form, onChange, reportError, getScope }) {
    const notify = typeof onChange === "function" ? onChange : () => {};
    const report = typeof reportError === "function" ? reportError : () => {};
    const scopeFn = typeof getScope === "function" ? getScope : () => "";
    let curSeq = 0, pending = null, cachedFiles = null, cachedScope = null, cachedReady = null, conv = null, banner = null;

    function abortErr() {
      const err = new Error("CSV selection changed");
      err.name = "AbortError";
      return err;
    }

    function clearBanner() {
      if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
      banner = null;
    }
    function reset() {
      curSeq++;
      pending = cachedFiles = cachedScope = cachedReady = conv = null;
      clearBanner();
    }
    if (form) form.addEventListener("reset", reset);

    function syncBounds(res) {
      if (!res) return;
      const fix = fields.fixed_clock && fields.fixed_clock.checked;
      if (fields.csv_start) fields.csv_start.value = fix ? res.startDate + "T00:00:00+01:00" : res.coverageStart;
      if (fields.csv_end) fields.csv_end.value = fix ? res.endDate + "T00:00:00+01:00" : res.coverageEnd;
    }
    if (fields.fixed_clock) {
      fields.fixed_clock.addEventListener("change", () => {
        if (conv) { syncBounds(conv); notify(fields.fixed_clock); }
      });
    }

    function showBanner(res) {
      clearBanner();
      const doc = (container && container.ownerDocument) || (typeof document !== "undefined" ? document : null);
      if (!container || !doc) return;
      banner = doc.createElement("div");
      banner.setAttribute("role", "status");
      banner.className = "csv-auto-confirmation";
      const addP = (txt) => { const p = doc.createElement("p"); p.textContent = txt; banner.appendChild(p); };
      const tot = Number.isInteger(res.totalKwh) ? res.totalKwh : Number(res.totalKwh.toFixed(6));
      addP("E-Distribuzione 15-minute matrix detected and automatically formatted (" + res.days + " days, " + tot + " total kWh).");
      if (res.days > 366) addP("Multi-year range detected; provide 12 annual monthly totals.");
      else if (res.monthsPresent.length < 12) addP("Partial coverage detected; provide all 12 monthly totals.");
      if (res.dstDays && res.dstDays.length) addP("96-slot days on clock-change dates require an explicit meter clock setting. Backend validation checks DST timestamps.");
      container.appendChild(banner);
    }

    function prepare() {
      const fl = fields.csv && fields.csv.files ? Array.from(fields.csv.files) : [];
      if (!fl.length) { reset(); return Promise.resolve(null); }
      let totSz = 0;
      for (let i = 0; i < fl.length; i++) totSz += fl[i].size || 0;
      if (totSz > MAX_BYTES) { reset(); return Promise.reject(new Error("Raw combined file size exceeds 16MiB limit")); }

      const scope = scopeFn();
      if (cachedScope === scope && cachedFiles && cachedFiles.length === fl.length && cachedFiles.every((f, i) => f === fl[i]) && cachedReady) return Promise.resolve(cachedReady);
      if (cachedScope === scope && pending && cachedFiles && cachedFiles.length === fl.length && cachedFiles.every((f, i) => f === fl[i])) return pending;

      cachedFiles = fl;
      cachedScope = scope;
      cachedReady = conv = null;
      clearBanner();

      const seq = ++curSeq;
      pending = (async () => {
        try {
          const first = fl[0];
          const head = await (first.slice ? first.slice(0, 4096).text() : first.text());
          if (!detectEdistribuzione(head)) {
            if (fl.length > 1) throw new Error("Multiple custom CSV files not supported");
            if (seq !== curSeq || scope !== scopeFn()) throw abortErr();
            clearBanner();
            cachedReady = first; conv = null;
            return first;
          }
          const res = await convertEdistribuzione(fl);
          if (seq !== curSeq || scope !== scopeFn()) throw abortErr();
          cachedReady = res.file; conv = res;
          const map = { timestamp: "timestamp", energy: "energy_kwh", fold: "", unit: "kWh", timestamp_convention: "start", delimiter: ",", decimal_separator: ".", duration_seconds: "900" };
          for (const k in map) if (fields[k]) fields[k].value = map[k];
          syncBounds(res);
          if (fields.coverage_start) fields.coverage_start.value = res.startDate;
          if (fields.coverage_end) fields.coverage_end.value = res.endDate;
          if (fields.totals) {
            fields.totals.value = res.days <= 366 ? res.monthlyTotals.map(v => (v === null ? "" : v)).join(",") : "";
            if (typeof fields.totals.oninput === "function") fields.totals.oninput();
          }
          showBanner(res);
          notify(fields.csv);
          return res.file;
        } catch (err) {
          if (seq !== curSeq || scope !== scopeFn()) throw abortErr();
          cachedReady = conv = null; clearBanner();
          throw err;
        } finally {
          if (seq === curSeq) pending = null;
        }
      })();
      return pending;
    }

    if (fields.csv) {
      fields.csv.addEventListener("change", async () => {
        try { await prepare(); } catch (err) { if (err.name !== "AbortError") report(err); }
      });
    }

    return { prepare, reset };
  }
  return { detectEdistribuzione, convertEdistribuzione, bindInput };
});
