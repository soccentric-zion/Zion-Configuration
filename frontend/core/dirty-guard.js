// Unsaved-changes guard. The router marks the page dirty on the first
// input/change inside #view; a successful mutating API call (the save
// action) clears it. Navigating away (hash change) or closing the tab while
// dirty prompts for confirmation instead of silently discarding edits.

let dirty = false;
let saveHandler = null;

export function markDirty() { dirty = true; }
export function clearDirty() { dirty = false; }
export function isDirty() { return dirty; }
export function setSaveHandler(handler) { saveHandler = handler; }
export async function saveDirtyPage() {
  if (!saveHandler) throw new Error('This page does not provide a save action');
  await saveHandler();
}

export function watchForEdits(container) {
  clearDirty();
  saveHandler = null;
  container.addEventListener('input', markDirty);
  container.addEventListener('change', markDirty);
}

export function installBeforeUnloadGuard() {
  window.addEventListener('beforeunload', (e) => {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = '';
  });
}
