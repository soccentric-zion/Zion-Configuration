// App-wide UI state: per-page timers/cleanups, the commit/confirm banner
// (spec §4.2 flagship rollback feature), and the reconnect-after-reboot
// overlay. Session identity (username/role) also lives here since both the
// sidebar and individual pages need to read it.

import { api } from './api.js';
import { toast } from '../components/toast.js';

const timers = new Set();
const cleanups = [];

export const Store = {
  role: null,
  username: null,
  pendingConfirm: null,
  _onSessionChange: null,

  registerTimer(handle) { timers.add(handle); return handle; },
  registerCleanup(fn) { cleanups.push(fn); },
  clearTimers() { for (const t of timers) clearInterval(t); timers.clear(); },
  runCleanups() { for (const fn of cleanups) { try { fn(); } catch (_) { /* noop */ } } cleanups.length = 0; },

  toast,

  onSessionChange(fn) { this._onSessionChange = fn; },
  setAuth(username, role) {
    this.username = username;
    this.role = role;
    if (this._onSessionChange) this._onSessionChange(username, role);
  },

  showConfirm(secs) {
    this.pendingConfirm = secs;
    const banner = document.getElementById('confirm-banner');
    const span = document.getElementById('confirm-seconds');
    banner.hidden = false;
    span.textContent = String(secs);
    if (this._confirmTimer) clearInterval(this._confirmTimer);
    this._confirmTimer = setInterval(() => {
      this.pendingConfirm -= 1;
      span.textContent = String(Math.max(0, this.pendingConfirm));
      if (this.pendingConfirm <= 0) this.hideConfirm();
    }, 1000);
  },
  hideConfirm() {
    document.getElementById('confirm-banner').hidden = true;
    if (this._confirmTimer) { clearInterval(this._confirmTimer); this._confirmTimer = null; }
    this.pendingConfirm = null;
  },
  async confirmNow() {
    try { await api.post('/api/v1/network/confirm', {}); } catch (e) { toast(e.message, 'error'); }
    this.hideConfirm();
  },
  async rollbackNow() {
    try { await api.post('/api/v1/network/rollback', {}); } catch (e) { toast(e.message, 'error'); }
    this.hideConfirm();
  },

  // Reconnect overlay: poll whoami until the daemon answers back (after a
  // reboot / factory-reset / update-apply action).
  _reconnecting: false,
  showReconnect() {
    if (this._reconnecting) return;
    this._reconnecting = true;
    document.getElementById('reconnect').hidden = false;
    const h = setInterval(async () => {
      try {
        await api.get('/api/v1/auth/whoami');
        document.getElementById('reconnect').hidden = true;
        clearInterval(h);
        this._reconnecting = false;
        if (this._onReconnected) this._onReconnected();
      } catch (_) { /* still down */ }
    }, 2000);
    this.registerTimer(h);
  },
  onReconnected(fn) { this._onReconnected = fn; },

  // Re-render the current page in place (e.g. after a mutation the page
  // doesn't otherwise reflect locally, like deleting a row from a list
  // fetched on mount). Wired to the router in core/app.js — store.js can't
  // import router.js directly since router.js already imports this module.
  _onReload: null,
  onReload(fn) { this._onReload = fn; },
  reload() { if (this._onReload) this._onReload(); },
};
