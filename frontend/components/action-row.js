import { el } from '../core/dom.js';

/** ActionRow(title, description, control) keeps an action and its consequence together. */
export function ActionRow(title, description, control) {
    return el('div', { class: 'action-row' },
        el('div', { class: 'action-row-copy' },
            el('h3', {}, title),
            el('p', {}, description)),
        control);
}

export function ActionList(...rows) {
    return el('div', { class: 'action-list' }, ...rows.filter(Boolean));
}
