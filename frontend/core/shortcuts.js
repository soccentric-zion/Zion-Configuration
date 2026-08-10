// Global keyboard shortcuts — GitHub-style chords (`g` then a letter) plus
// Cmd/Ctrl+K for the command palette and `?` for the cheat sheet. Disabled
// while focus is inside a form control so typing never gets hijacked.

const registry = []; // { seq: ['g','s'], run, description, group }
let buffer = [];
let bufferTimer = null;
let onOpenPalette = null;
let onOpenHelp = null;

function isTypingContext(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/** registerShortcut(['g','s'], fn, 'Go to System', 'Navigation') */
export function registerShortcut(seq, run, description, group = 'General') {
  registry.push({ seq: seq.map(k => k.toLowerCase()), run, description, group });
}

export function listShortcuts() {
  return registry.slice();
}

export function initShortcuts({ openPalette, openHelp }) {
  onOpenPalette = openPalette;
  onOpenHelp = openHelp;
  document.addEventListener('keydown', (e) => {
    if (isTypingContext(e.target)) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (onOpenPalette) onOpenPalette();
      return;
    }
    if (e.key === '?' && onOpenHelp) { onOpenHelp(); return; }
    if (e.metaKey || e.ctrlKey || e.altKey) return; // don't swallow browser/OS chords

    buffer.push(e.key.toLowerCase());
    if (buffer.length > 3) buffer.shift();
    clearTimeout(bufferTimer);
    bufferTimer = setTimeout(() => { buffer = []; }, 700);

    for (const item of registry) {
      const tail = buffer.slice(-item.seq.length);
      if (tail.length === item.seq.length && tail.every((k, i) => k === item.seq[i])) {
        item.run();
        buffer = [];
        return;
      }
    }
  });
}
