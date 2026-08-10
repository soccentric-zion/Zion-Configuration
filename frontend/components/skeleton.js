import { el } from '../core/dom.js';

/** A pulsing placeholder block — use while a page's initial fetch is in flight. */
export function Skeleton({ width = '100%', height = '16px', class: extra } = {}) {
  return el('div', { class: ['skeleton', extra].filter(Boolean).join(' '), style: `width:${width};height:${height}` });
}

/** A stack of skeleton lines shaped roughly like a settings card. */
export function CardSkeleton() {
  return el('div', { class: 'card' },
    el('div', { class: 'card-header' }, Skeleton({ width: '120px', height: '14px' })),
    el('div', { class: 'card-body stack' },
      Skeleton({ height: '34px' }),
      Skeleton({ height: '34px' }),
      Skeleton({ width: '60%', height: '34px' })));
}

export function PageSkeleton() {
  return el('div', { class: 'stack' }, CardSkeleton(), CardSkeleton());
}
