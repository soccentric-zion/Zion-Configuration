import { el } from '../core/dom.js';

/**
 * Table({ columns: ['Unit', 'Active', ...], caption }, rows) where each row is
 * an array of cell nodes/strings in column order. `caption` is visually
 * hidden (the visible heading already names the table for sighted users) but
 * gives assistive tech a table-level label; pass the same text as the
 * surrounding card/page heading. Returns { element, tbody } so callers can
 * append/replace rows after the initial render.
 */
export function Table({ columns = [], caption } = {}, rows = []) {
  const captionEl = caption ? el('caption', { class: 'sr-only' }, caption) : null;
  const thead = el('thead', {}, el('tr', {}, ...columns.map(c => el('th', { scope: 'col' }, c))));
  const tbody = el('tbody', {}, ...rows.map(r => el('tr', {}, ...r.map(c => el('td', {}, c)))));
  const table = el('table', { class: 'table' }, captionEl, thead, tbody);
  return { element: el('div', { class: 'table-wrap' }, table), tbody, table };
}

export function row(...cells) {
  return el('tr', {}, ...cells.map(c => el('td', {}, c)));
}
