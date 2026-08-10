import { el } from '../core/dom.js';

/**
 * Card({ title, subtitle, actions, footer, class }, ...bodyChildren)
 * Any of title/subtitle/actions/footer may be omitted.
 */
export function Card(opts = {}, ...body) {
  const { title, subtitle, actions, footer, class: extraClass } = opts;
  const parts = [];
  if (title) {
    parts.push(el('div', { class: 'card-header' },
      el('div', {},
        el('h3', {}, title),
        subtitle ? el('div', { class: 'card-header-sub' }, subtitle) : null),
      actions ? el('div', { class: 'row' }, ...[].concat(actions)) : null));
  }
  parts.push(el('div', { class: 'card-body' }, ...body));
  if (footer) parts.push(el('div', { class: 'card-footer' }, ...[].concat(footer)));
  return el('div', { class: ['card', extraClass].filter(Boolean).join(' ') }, ...parts);
}
