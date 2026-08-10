import { el } from '../core/dom.js';

/** Badge(label, variant?) — variant: neutral | success | warning | destructive | info */
export function Badge(label, variant = 'neutral') {
  return el('span', { class: `badge badge-${variant}` }, label);
}

/** StatusDot(state) — state: ok | warn | bad | unknown */
export function StatusDot(state) {
  const cls = state === 'ok' ? 'dot-ok' : state === 'warn' ? 'dot-warn' : state === 'bad' ? 'dot-bad' : 'dot-unknown';
  return el('span', { class: `dot ${cls}` });
}
