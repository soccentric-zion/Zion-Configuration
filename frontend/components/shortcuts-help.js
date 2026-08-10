import { el } from '../core/dom.js';
import { infoDialog } from './modal.js';
import { listShortcuts } from '../core/shortcuts.js';

function kbd(text) { return el('span', { class: 'cmdk-hint-kbd' }, text); }

export function showShortcutsHelp() {
  const shortcuts = listShortcuts();
  const groups = new Map();
  for (const s of shortcuts) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }

  const rows = [
    { keys: ['⌘', 'K'], description: 'Open command palette' },
    { keys: ['?'], description: 'Show this cheat sheet' },
    ...[...groups.values()].flat().map(s => ({ keys: s.seq.map(k => k.toUpperCase()), description: s.description })),
  ];

  return infoDialog({
    title: 'Keyboard shortcuts',
    body: el('div', { class: 'stack' }, ...rows.map(r => el('div', { class: 'row', style: 'justify-content:space-between' },
      el('span', { class: 'muted text-sm' }, r.description),
      el('span', { class: 'row' }, ...r.keys.map(kbd))))),
    closeLabel: 'Got it',
  });
}
