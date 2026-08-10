import { el } from '../core/dom.js';
import { icon } from '../core/icons.js';

export function EmptyState({ iconName = 'inbox', title = 'Nothing here yet', description } = {}) {
  return el('div', { class: 'empty-state' },
    icon(iconName, { size: 32 }),
    el('div', {}, title),
    description ? el('div', { class: 'muted', style: 'margin-top:4px' }, description) : null);
}
