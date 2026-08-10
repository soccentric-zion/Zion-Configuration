// Tiny hyperscript-style DOM builder — the one primitive every component and
// page is built from. No virtual DOM, no diffing: pages are re-built wholesale
// on navigation, which is cheap enough for a config UI (spec §2: vanilla JS).

/**
 * el('div', { class: 'card', onclick: fn }, child1, child2, ...)
 * - `class` sets className directly.
 * - `html` sets innerHTML (only used where content is fully static/trusted).
 * - `on*` (function value) registers an event listener.
 * - boolean `true` sets a bare attribute; `false`/`null`/`undefined` skips it.
 * - everything else is set via setAttribute.
 * - children: strings become text nodes; arrays are flattened; nullish/false
 *   entries are skipped so `cond && el(...)` reads naturally.
 */
export function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, '');
      else if (v === false || v == null) { /* skip */ }
      else e.setAttribute(k, v);
    }
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    e.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
  return e;
}

/** Replace `container`'s children with `node` (or a plain string). */
export function mount(container, node) {
  container.replaceChildren(typeof node === 'string' ? document.createTextNode(node) : node);
}

export function clear(container) {
  container.replaceChildren();
}
