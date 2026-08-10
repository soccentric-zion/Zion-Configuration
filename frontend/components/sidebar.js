import { el } from '../core/dom.js';
import { icon } from '../core/icons.js';

/**
 * Sidebar({ navItems, onNavigate }) -> { element, setActive }
 * navItems: [{ id, label, icon, category }]
 */
export function Sidebar({ navItems, onNavigate }) {
  const links = new Map();

  const groupedItems = new Map();
  for (const item of navItems) {
    const category = item.category || 'Configuration';
    if (!groupedItems.has(category)) groupedItems.set(category, []);
    groupedItems.get(category).push(item);
  }

  const nav = el('nav', { class: 'sidebar-nav', 'aria-label': 'Main' },
    ...[...groupedItems].flatMap(([category, items]) => [
      el('div', { class: 'sidebar-section-label' }, category),
      ...items.map((item) => {
        // preventDefault: the real `href` is there for right-click/"open in new
        // tab"/accessibility, but a plain click must go through onNavigate()
        // so the router's unsaved-changes guard (core/dirty-guard.js) gets a
        // chance to run *before* the hash — and therefore the page — changes.
        const link = el('a', {
          href: `#/${item.id}`,
          class: 'sidebar-link',
          'data-route': item.id,
          onclick: (e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return; // let modified clicks open normally
            e.preventDefault();
            onNavigate && onNavigate(item.id);
          },
        }, icon(item.icon), el('span', { class: 'label' }, item.label));
        links.set(item.id, link);
        return link;
      }),
    ]));

  const element = el('aside', { class: 'sidebar', id: 'sidebar' }, nav);

  function setActive(routeId) {
    for (const [id, link] of links) {
      const isActive = id === routeId;
      link.classList.toggle('active', isActive);
      if (isActive) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
  }

  return { element, setActive };
}
