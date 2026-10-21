/* Tiny DOM helpers. Every piece of text from the API or the LLM goes through
   textContent / text nodes, never innerHTML. */

const SVG_NS = "http://www.w3.org/2000/svg";

/** el("a", {class, text, href, on: {click}}, ...children) */
export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  append(node, children);
  return node;
}

export function svg(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") node.textContent = String(v);
    else node.setAttribute(k, String(v));
  }
  append(node, children);
  return node;
}

function setAttrs(node, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = String(v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    else if (k === "style") node.style.cssText = String(v); // CSSOM: allowed by the strict CSP
    else if (k === "value") node.value = v;
    else if (k === "checked") node.checked = !!v;
    else if (k === "href" && !/^(#|https?:\/\/|\/(?!\/))/i.test(String(v).trim())) continue; // no javascript: or data: links
    else node.setAttribute(k, v === true ? "" : String(v));
  }
}

function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Replace a node's content. */
export function mount(node, ...children) {
  clear(node);
  append(node, children);
  return node;
}

/** Inline icon from the sprite in index.html. */
export function icon(name, cls) {
  const s = svg("svg", { class: "icon" + (cls ? " " + cls : ""), "aria-hidden": "true", focusable: "false" });
  s.append(svg("use", { href: "#i-" + name }));
  return s;
}

export function spinner() { return el("span", { class: "spinner", "aria-hidden": "true" }); }
