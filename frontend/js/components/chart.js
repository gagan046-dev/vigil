/* Dependency-free SVG step chart for value history, with a target line and hover readout. */
import { el, svg, clear } from "../dom.js";
import { money, parseTime, dateOnly, exact, clock } from "../format.js";

function compactInr(v) {
  const a = Math.abs(v);
  if (a >= 1e7) return "₹" + trim(v / 1e7) + " Cr";
  if (a >= 1e5) return "₹" + trim(v / 1e5) + " L";
  if (a >= 1e3) return "₹" + trim(v / 1e3) + "k";
  return "₹" + trim(v);
}
function trim(n) { return n.toFixed(2).replace(/\.?0+$/, ""); }

function niceTicks(min, max, count = 4) {
  if (min === max) { const pad = Math.abs(min) * 0.05 || 1; min -= pad; max += pad; }
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const err = step0 / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);
  return ticks;
}

/**
 * points: [{at, value, status}], target: number|null.
 * Returns a container that renders itself at its own width and re-renders on resize.
 */
export function historyChart(points, { target = null, height = 240, format = money } = {}) {
  const wrap = el("div", { class: "chart", style: "height:" + height + "px" });
  const readout = el("div", { class: "chart__readout", hidden: true });
  const data = points
    .map((p) => ({ t: parseTime(p.at), v: p.value === null || p.value === undefined ? null : Number(p.value), s: p.status, at: p.at }))
    .filter((p) => p.t)
    .sort((a, b) => a.t - b.t);

  const draw = () => {
    const W = Math.max(280, Math.floor(wrap.clientWidth));
    const H = height;
    const svgNode = wrap.querySelector("svg.chart__svg");
    if (svgNode) svgNode.remove();
    const vals = data.filter((p) => p.v !== null);
    const m = { l: 64, r: 16, t: 14, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;

    const root = svg("svg", { class: "chart__svg", width: W, height: H, viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": vals.length ? "Value history, " + vals.length + " checks" : "No values yet" });
    if (!vals.length) {
      root.append(svg("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "chart__empty", text: "No values recorded in this period" }));
      wrap.prepend(root);
      return;
    }
    let lo = Math.min(...vals.map((p) => p.v)), hi = Math.max(...vals.map((p) => p.v));
    if (target !== null && target !== undefined) { lo = Math.min(lo, target); hi = Math.max(hi, target); }
    const ticks = niceTicks(lo, hi);
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const t0 = data[0].t.getTime();
    const t1 = Math.max(data[data.length - 1].t.getTime(), t0 + 1);
    const x = (t) => m.l + ((t - t0) / (t1 - t0)) * iw;
    const y = (v) => m.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;

    const g = svg("g");
    for (const tv of ticks) {
      g.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(tv), y2: y(tv), class: "chart__grid" }));
      g.append(svg("text", { x: m.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "chart__tick", text: compactInr(tv) }));
    }
    // x labels: up to 5 evenly spaced dates/times
    const spanH = (t1 - t0) / 3600000;
    const nx = Math.min(5, Math.max(2, Math.floor(iw / 120)));
    for (let i = 0; i < nx; i++) {
      const tt = t0 + ((t1 - t0) * i) / (nx - 1);
      const label = spanH <= 36 ? clock(new Date(tt)).slice(0, 5) : dateOnly(new Date(tt));
      g.append(svg("text", { x: x(tt), y: H - 8, "text-anchor": i === 0 ? "start" : i === nx - 1 ? "end" : "middle", class: "chart__tick", text: label }));
    }
    root.append(g);

    // step path
    let d = "", area = "";
    vals.forEach((p, i) => {
      const px = x(p.t.getTime()), py = y(p.v);
      if (i === 0) { d = "M" + px + "," + py; area = "M" + px + "," + (m.t + ih) + "L" + px + "," + py; }
      else { d += "H" + px + "V" + py; area += "H" + px + "V" + py; }
    });
    const lastX = x(vals[vals.length - 1].t.getTime());
    area += "H" + lastX + "V" + (m.t + ih) + "Z";
    root.append(svg("path", { d: area, class: "chart__area" }));
    root.append(svg("path", { d, class: "chart__line" }));

    if (target !== null && target !== undefined) {
      root.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(target), y2: y(target), class: "chart__target" }));
      root.append(svg("text", { x: W - m.r, y: y(target) - 5, "text-anchor": "end", class: "chart__target-label", text: "Target " + format(target) }));
    }
    // error markers along the baseline
    for (const p of data) if (p.s === "error") {
      root.append(svg("line", { x1: x(p.t.getTime()), x2: x(p.t.getTime()), y1: m.t + ih - 5, y2: m.t + ih, class: "chart__err" }));
    }

    const cross = svg("line", { y1: m.t, y2: m.t + ih, class: "chart__cross", visibility: "hidden" });
    const dot = svg("circle", { r: 3.5, class: "chart__dot", visibility: "hidden" });
    root.append(cross, dot);
    const hit = svg("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    root.append(hit);

    const pick = (clientX) => {
      const r = root.getBoundingClientRect();
      const tx = t0 + ((clientX - r.left - m.l) / iw) * (t1 - t0);
      let best = vals[0];
      for (const p of vals) if (Math.abs(p.t - tx) < Math.abs(best.t - tx)) best = p;
      const px = x(best.t.getTime()), py = y(best.v);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
      clear(readout);
      readout.append(el("strong", { text: format(best.v) }), el("span", { text: exact(best.at) }));
      readout.hidden = false;
      const rw = readout.offsetWidth;
      readout.style.left = Math.min(Math.max(0, px - rw / 2), W - rw) + "px";
      readout.style.top = Math.max(0, py - 52) + "px";
    };
    hit.addEventListener("mousemove", (e) => pick(e.clientX));
    hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); readout.hidden = true; });
    wrap.prepend(root);
  };

  wrap.append(readout);
  let raf = 0;
  const ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); });
  ro.observe(wrap);
  return wrap;
}
