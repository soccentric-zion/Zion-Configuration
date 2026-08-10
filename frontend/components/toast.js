import { el } from '../core/dom.js';
import { icon } from '../core/icons.js';

let region = null;
function ensureRegion() {
  if (!region) {
    region = document.getElementById('toast-root') || el('div', { id: 'toast-root', class: 'toast-region' });
    if (!region.isConnected) document.body.appendChild(region);
  }
  return region;
}

const MAX_VISIBLE = 4;

/** toast('Saved', 'success' | 'error' | undefined) — auto-dismisses after ~4s (errors linger a bit longer). */
export function toast(message, variant) {
  const r = ensureRegion();
  const iconName = variant === 'success' ? 'check' : variant === 'error' ? 'alertTriangle' : null;
  const dismiss = () => node.remove();
  const node = el('div', { class: ['toast', variant ? `toast-${variant}` : null].filter(Boolean).join(' '), role: 'status' },
    iconName ? icon(iconName, { size: 15 }) : null,
    el('span', { style: 'flex:1 1 auto' }, message),
    el('button', { type: 'button', class: 'toast-dismiss', 'aria-label': 'Dismiss', onclick: dismiss }, '×'));
  r.appendChild(node);

  // Cap how many stack up at once so a burst of failures doesn't fill the screen.
  while (r.children.length > MAX_VISIBLE) r.firstElementChild.remove();

  setTimeout(dismiss, variant === 'error' ? 5000 : 3200);
}
