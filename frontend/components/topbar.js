import { el } from '../core/dom.js';
import { icon } from '../core/icons.js';
import { Button } from './button.js';

/** Topbar({ onToggleSidebar, onOpenPalette, onLogout }) -> { element, setTitle, setBadge, setSession } */
export function Topbar({ onToggleSidebar, onOpenPalette, onLogout }) {
  const title = el('span', { class: 'page-title' }, '');
  const badge = el('span', { class: 'badge badge-info', hidden: true });
  const session = el('span', { class: 'muted text-sm' });
  const desktopToggle = Button({
    variant: 'ghost', size: 'sm', icon: 'menu', title: 'Collapse navigation',
    onClick: onToggleSidebar,
  });
  desktopToggle.classList.add('sidebar-desktop-toggle');
  const mobileToggle = Button({
    variant: 'ghost', size: 'sm', icon: 'menu', title: 'Open navigation',
    onClick: onToggleSidebar,
  });
  mobileToggle.classList.add('sidebar-mobile-toggle');

  const brand = el('div', { class: 'topbar-brand' },
    el('span', { class: 'logo-mark' }, 'W'),
    el('span', { class: 'brand-text' }, 'WebConfig'));

  const searchTrigger = el('button', {
    type: 'button', class: 'btn btn-outline search-trigger', onclick: onOpenPalette,
    'aria-label': 'Search pages and actions',
  }, el('span', { class: 'search-trigger-copy' }, icon('search', { size: 15 }), el('span', { class: 'search-trigger-label' }, 'Search pages and actions')),
    el('kbd', { class: 'cmdk-hint-kbd' }, 'Ctrl K'));
  const logoutButton = Button({
    variant: 'ghost', size: 'sm', icon: 'logout', title: 'Log out', onClick: onLogout,
  });
  logoutButton.classList.add('topbar-logout');

  const element = el('div', { class: 'topbar' },
    brand,
    desktopToggle,
    mobileToggle,
    el('div', { class: 'topbar-heading' }, title),
    badge,
    el('div', { class: 'spacer' }),
    searchTrigger,
    session,
    logoutButton);

  return {
    element,
    setTitle: (t) => { title.textContent = t; },
    setBadge: (t) => { if (t) { badge.textContent = t.toUpperCase(); badge.hidden = false; } else badge.hidden = true; },
    setSession: (username, role) => { session.textContent = username ? `${username} · ${role || ''}` : ''; },
  };
}
