// Hash-based router. Each route lazily imports its page module (native
// dynamic import — no bundler needed) and mounts the returned node into
// #view. Errors are caught and rendered inline rather than left as a stuck
// loading state.

import { el, mount } from './dom.js';
import { api } from './api.js';
import { Store } from './store.js';
import { isDirty, watchForEdits, setSaveHandler, saveDirtyPage } from './dirty-guard.js';
import { startProgress, finishProgress } from '../components/progress-bar.js';
import { choiceDialog } from '../components/modal.js';
import { PageSkeleton } from '../components/skeleton.js';
import { EmptyState } from '../components/empty-state.js';
import { NAV, ROUTE_IDS, routeMeta } from '../pages/registry.js';

let sidebarApi = null;
let topbarApi = null;
let current = null;
// The last hash we actually rendered — lets the hashchange guard below revert
// browser back/forward or a manual hash edit if the current page is dirty,
// the same way `go()` already guards programmatic navigation.
let lastHash = null;
// Set just before we change `location.hash` ourselves (confirmed navigation,
// or reverting an unconfirmed one) so the resulting hashchange event is
// treated as "just render", not re-guarded — otherwise the guard would fire
// on its own revert.
let suppressGuard = false;

export function initRouter({ sidebar, topbar }) {
  sidebarApi = sidebar;
  topbarApi = topbar;
  lastHash = location.hash;
  window.addEventListener('hashchange', handleHashChange);
}

/** Returns the route id, or `null` if the hash names a segment that isn't a
 * known route (an empty hash is the index route, not a 404 — only a
 * non-empty, unrecognized segment counts as "not found"). */
function parse() {
  const h = location.hash.replace(/^#\/?/, '').split('/')[0];
  if (!h) return 'overview';
  return ROUTE_IDS.includes(h) ? h : null;
}

/** Ask "discard unsaved changes?" and act on the answer. Returns true if the
 * caller should proceed with navigation (nothing dirty, or the user chose to
 * discard/save-and-continue), false if it should stay put (cancelled, or the
 * save itself failed). */
async function confirmDiscardIfDirty() {
  if (!isDirty()) return true;
  const choice = await choiceDialog({
    title: 'Discard unsaved changes?',
    description: 'You have edits on this page that haven’t been saved.',
    choices: [
      { value: 'cancel', label: 'Cancel', variant: 'outline' },
      { value: 'discard', label: 'Discard', variant: 'destructive' },
      { value: 'save', label: 'Save and continue' },
    ],
  });
  if (choice === 'cancel' || choice == null) return false;
  if (choice === 'save') {
    try {
      await saveDirtyPage();
    } catch (e) {
      console.error('[WebConfig] unable to save before navigation', e);
      return false;
    }
  }
  return true;
}

/** Programmatic navigation (sidebar, command palette). Guards unsaved edits. */
export async function go(route) {
  if (route === current) { renderCurrent(); return; }
  if (!(await confirmDiscardIfDirty())) return;
  suppressGuard = true;
  location.hash = `/${route}`;
}

/** hashchange fires for browser back/forward AND manual hash edits/plain
 * links — none of which go through `go()`, so this is the only place that
 * can catch them. The hash has already changed by the time this runs, so an
 * unconfirmed navigation is handled by reverting to `lastHash` first (to
 * keep the address bar/history in sync with what's actually rendered), then
 * re-applying the target hash only if the user confirms.
 */
async function handleHashChange() {
  if (suppressGuard) {
    suppressGuard = false;
    lastHash = location.hash;
    renderCurrent();
    return;
  }
  if (!isDirty()) {
    lastHash = location.hash;
    renderCurrent();
    return;
  }
  const targetHash = location.hash;
  const previousHash = lastHash;
  suppressGuard = true;
  location.hash = previousHash; // revert while we ask; resolves via the branch above

  if (!(await confirmDiscardIfDirty())) return; // stays reverted
  suppressGuard = true;
  location.hash = targetHash;
}

export async function renderCurrent() {
  const route = parse();
  current = route;
  if (sidebarApi) sidebarApi.setActive(route);

  Store.clearTimers();
  Store.runCleanups();

  const view = document.getElementById('view');
  view.hidden = false;

  if (route == null) {
    if (topbarApi) topbarApi.setTitle('Not found');
    document.title = 'Not found — SoCcentric WebConfig';
    mount(view, EmptyState({
      iconName: 'alertTriangle',
      title: 'Page not found',
      description: `There's no page at "${location.hash.replace(/^#\/?/, '') || '/'}".`,
    }));
    return;
  }

  const meta = routeMeta(route);
  if (topbarApi) topbarApi.setTitle(meta ? meta.label : route);
  document.title = meta ? `${meta.label} — SoCcentric WebConfig` : 'SoCcentric WebConfig';

  mount(view, el('div', { class: 'page-loading' }, PageSkeleton()));
  startProgress();

  if (meta && meta.adminOnly && Store.role !== 'admin') {
    mount(view, EmptyState({ iconName: 'shield', title: 'Admin role required', description: 'Sign in as an administrator to view this page.' }));
    finishProgress();
    return;
  }

  try {
    const mod = await meta.loader();
    const node = await mod.render(api, Store);
    node.classList.add('page-surface');
    mount(view, node);
    view.classList.remove('page-enter');
    void view.offsetWidth; // restart the animation on every navigation
    view.classList.add('page-enter');
    watchForEdits(view);
    const saveButton = [...view.querySelectorAll('button')].find(button => /^save(?:\b|\s)/i.test(button.textContent.trim()) || /^apply settings$/i.test(button.textContent.trim()));
    if (saveButton && saveButton.run) setSaveHandler(() => saveButton.run());
    // Move focus to the new page for screen-reader/keyboard users — a bare
    // hash change otherwise leaves focus stranded on the sidebar link.
    view.setAttribute('tabindex', '-1');
    view.focus({ preventScroll: true });
  } catch (e) {
    mount(view, EmptyState({ iconName: 'alertTriangle', title: 'Couldn’t load this page', description: e.message }));
  } finally {
    finishProgress();
  }
}

export { NAV };
