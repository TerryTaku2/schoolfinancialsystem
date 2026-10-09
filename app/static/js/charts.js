// Minimal dependency-free SVG charts with hover tooltips.
// Marks: columns <= 24px with 4px rounded tops, 2px lines, >= 8px markers, hairline grid.
import { esc } from "./ui.js";

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

// Column path with 4px rounded top corners, square at the baseline.
function colPath(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function mount(el, draw) {
  el.classList.add("chart");
  const render = () => {
    const w = Math.max(el.clientWidth || 600, 260);
    el.innerHTML = draw(w);
    wireTips(el);
  };
  render();
  if (window.ResizeObserver) {
    let last = el.clientWidth;
    new ResizeObserver(() => { if (Math.abs(el.clientWidth - last) > 4) { last = el.clientWidth; render(); } }).observe(el);
  }
}

function wireTips(el) {
  const tip = document.createElement("div");
  tip.className = "chart-tip hidden";
  el.appendChild(tip);
  el.querySelectorAll("[data-tip]").forEach((node) => {
    node.addEventListener("mouseenter", () => {
      tip.innerHTML = node.dataset.tip;
      tip.classList.remove("hidden");
      tip.style.left = node.dataset.x + "px";
      tip.style.top = node.dataset.y + "px";
      el.querySelectorAll(`[data-mark="${node.dataset.idx}"]`).forEach((m) => m.setAttribute("opacity", "0.8"));
    });
    node.addEventListener("mouseleave", () => {
      tip.classList.add("hidden");
      el.querySelectorAll("[data-mark]").forEach((m) => m.removeAttribute("opacity"));
    });
  });
}

function yAxis(max, h, pad, w, fmt) {
  const ticks = 4;
  let out = "";
  for (let i = 0; i <= ticks; i++) {
    const v = (max / ticks) * i;
    const y = pad.t + h - (h * i) / ticks;
    out += `<line class="grid-line" x1="${pad.l}" x2="${w - pad.r}" y1="${y}" y2="${y}"/>
            <text class="axis-text" x="${pad.l - 8}" y="${y + 4}" text-anchor="end">${esc(fmt(v))}</text>`;
  }
  return out;
}

/** Grouped or single-series column chart. series: [{name, color, values:[]}] */
export function columnChart(el, { labels, series, format = (v) => v, axisFormat = format, height = 240 }) {
  mount(el, (w) => {
    const pad = { t: 16, r: 12, b: 28, l: 56 };
    const h = height - pad.t - pad.b;
    const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values.map((v) => v || 0))));
    const band = (w - pad.l - pad.r) / labels.length;
    const n = series.length;
    const colW = Math.min(24, (band * 0.7 - (n - 1) * 2) / n);
    const groupW = colW * n + (n - 1) * 2;
    let marks = "", hits = "", xl = "";
    // Label every n-th column when they'd collide (e.g. 12 months on a phone).
    const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor((w - pad.l - pad.r) / 46))));
    labels.forEach((lab, i) => {
      const gx = pad.l + band * i + (band - groupW) / 2;
      series.forEach((s, k) => {
        const v = s.values[i] || 0;
        const ch = (v / max) * h;
        marks += `<path data-mark="${i}" d="${colPath(gx + k * (colW + 2), pad.t + h - ch, colW, ch)}" style="fill:${s.color}"/>`;
      });
      const tipRows = series.map((s) => `${n > 1 ? `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${s.color};margin-right:6px"></span>${esc(s.name)}: ` : ""}${esc(format(s.values[i] || 0))}`).join("<br>");
      const top = pad.t + h - (Math.max(...series.map((s) => s.values[i] || 0)) / max) * h;
      hits += `<rect class="hit" x="${pad.l + band * i}" y="${pad.t}" width="${band}" height="${h}" data-idx="${i}" data-x="${pad.l + band * (i + 0.5)}" data-y="${top}" data-tip="${esc(`<b>${esc(lab)}</b>${tipRows}`)}"/>`;
      if ((labels.length - 1 - i) % every === 0) xl += `<text class="axis-text" x="${pad.l + band * (i + 0.5)}" y="${height - 8}" text-anchor="middle">${esc(lab)}</text>`;
    });
    const legend = n > 1 ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
    return `${legend}<svg width="${w}" height="${height}" viewBox="0 0 ${w} ${height}" role="img" aria-label="${esc(series.map((s) => s.name).join(", "))} by period">
      ${yAxis(max, h, pad, w, axisFormat)}<line class="grid-line" x1="${pad.l}" x2="${w - pad.r}" y1="${pad.t + h}" y2="${pad.t + h}" style="stroke:var(--border)"/>
      ${marks}${xl}${hits}</svg>`;
  });
}

/** Single-series line with markers; null values break the line. */
export function lineChart(el, { labels, values, format = (v) => v, height = 220, max: fixedMax, color = "var(--series-1)", name = "" }) {
  mount(el, (w) => {
    const pad = { t: 16, r: 16, b: 28, l: 44 };
    const h = height - pad.t - pad.b;
    const max = fixedMax || niceMax(Math.max(0, ...values.filter((v) => v !== null)));
    const step = labels.length > 1 ? (w - pad.l - pad.r) / (labels.length - 1) : 0;
    const pts = values.map((v, i) => (v === null ? null : [pad.l + step * i, pad.t + h - (v / max) * h]));
    let d = "", pen = false;
    pts.forEach((p) => { if (!p) { pen = false; return; } d += (pen ? "L" : "M") + p[0] + "," + p[1]; pen = true; });
    const area = pts.filter(Boolean);
    const areaD = area.length > 1 ? `M${area[0][0]},${pad.t + h}` + area.map((p) => `L${p[0]},${p[1]}`).join("") + `L${area[area.length - 1][0]},${pad.t + h}Z` : "";
    const every = Math.ceil(labels.length / Math.max(2, Math.floor(w / 80)));
    let dots = "", hits = "", xl = "";
    pts.forEach((p, i) => {
      if (p) dots += `<circle data-mark="${i}" cx="${p[0]}" cy="${p[1]}" r="4" style="fill:${color};stroke:var(--surface);stroke-width:2"/>`;
      const x = pad.l + step * i;
      hits += `<rect class="hit" x="${x - step / 2}" y="${pad.t}" width="${Math.max(step, 16)}" height="${h}" data-idx="${i}" data-x="${x}" data-y="${p ? p[1] : pad.t + h}" data-tip="<b>${esc(labels[i])}</b>${esc(name ? name + ": " : "")}${values[i] === null ? "No data" : esc(format(values[i]))}"/>`;
      // Every n-th date, plus the last one; drop a regular label that would collide with the last.
      const lastIdx = labels.length - 1;
      if (i === lastIdx || (i % every === 0 && lastIdx - i >= Math.ceil(every * 0.75))) xl += `<text class="axis-text" x="${x}" y="${height - 8}" text-anchor="middle">${esc(labels[i])}</text>`;
    });
    const last = [...pts].reverse().find(Boolean);
    const lastV = [...values].reverse().find((v) => v !== null);
    const endLabel = last ? `<text class="value-text" x="${Math.min(last[0], w - pad.r - 4)}" y="${last[1] - 10}" text-anchor="end">${esc(format(lastV))}</text>` : "";
    return `<svg width="${w}" height="${height}" viewBox="0 0 ${w} ${height}" role="img" aria-label="${esc(name || "trend")}">
      ${yAxis(max, h, pad, w, format)}
      <path d="${areaD}" style="fill:${color};opacity:.1"/>
      <path d="${d}" style="fill:none;stroke:${color};stroke-width:2;stroke-linejoin:round;stroke-linecap:round"/>
      ${dots}${endLabel}${xl}${hits}</svg>`;
  });
}

/** Horizontal bars as HTML (labels never clip). rows: [{label, value}] */
export function hbarChart(el, { rows, format = (v) => v }) {
  if (!rows.length) { el.innerHTML = `<div class="empty">No data yet.</div>`; return; }
  const max = Math.max(...rows.map((r) => r.value), 1);
  el.innerHTML = `<div class="hbar">${rows.map((r) => `
    <span class="muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.label)}">${esc(r.label)}</span>
    <span class="track"><span class="fill" style="display:block;width:${(r.value / max) * 100}%" title="${esc(r.label)}: ${esc(format(r.value))}"></span></span>
    <span class="num">${esc(format(r.value))}</span>`).join("")}</div>`;
}
