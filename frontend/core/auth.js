// Authentication and first-boot flow. The shell only supplies presentation
// callbacks, so auth changes do not require editing application wiring.

import { api, setCsrf } from './api.js';
import { Store } from './store.js';

const REMEMBERED_USERNAME_KEY = 'webconfig.remembered-username';

function rememberedUsername() {
    try { return localStorage.getItem(REMEMBERED_USERNAME_KEY) || ''; } catch (_) { return ''; }
}

function saveRememberedUsername(form) {
    try {
        if (form.elements['remember-username'].checked) {
            localStorage.setItem(REMEMBERED_USERNAME_KEY, form.username.value);
        } else {
            localStorage.removeItem(REMEMBERED_USERNAME_KEY);
        }
    } catch (_) { /* Storage may be disabled by the browser. */ }
}

export function createAuthController({ authScreen, appShell, topbar, onRender }) {
    function showFirstBoot() {
        authScreen.hidden = false;
        appShell.hidden = true;
        document.getElementById('first-boot').hidden = false;
        document.getElementById('login').hidden = true;
    }

    function showLogin() {
        authScreen.hidden = false;
        appShell.hidden = true;
        document.getElementById('first-boot').hidden = true;
        document.getElementById('login').hidden = false;
        const form = document.getElementById('login-form');
        const username = rememberedUsername();
        if (username) {
            form.username.value = username;
            form.elements['remember-username'].checked = true;
            form.password.focus();
        }
    }

    async function enterApp() {
        authScreen.hidden = true;
        appShell.hidden = false;
        try {
            const manifest = await api.get('/api/v1/manifest');
            if (manifest && manifest.platform) topbar.setBadge(manifest.platform);
        } catch (_) { /* badge is cosmetic */ }
        onRender();
    }

    async function bootstrap() {
        try {
            const me = await api.get('/api/v1/auth/whoami');
            if (me && me.csrf) setCsrf(me.csrf);
            if (me && me.first_boot) return showFirstBoot();
            if (me && me.username) {
                Store.setAuth(me.username, me.role);
                return enterApp();
            }
            return showLogin();
        } catch (_) {
            return showLogin();
        }
    }

    async function logout() {
        try { await api.post('/api/v1/auth/logout', {}); } catch (_) { /* best-effort */ }
        Store.setAuth(null, null);
        showLogin();
    }

    wireAuthForm('first-boot-form', 'first-boot-error', async (form) => {
        await api.post('/api/v1/auth/first-boot', {
            username: form.username.value,
            password: form.password.value,
        });
        await bootstrap();
    });
    wireAuthForm('login-form', 'login-error', async (form) => {
        saveRememberedUsername(form);
        await api.post('/api/v1/auth/login', {
            username: form.username.value,
            password: form.password.value,
        });
        await bootstrap();
    });

    return { bootstrap, logout, showLogin };
}

function wireAuthForm(formId, errorId, submit) {
    document.getElementById(formId).addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = event.target;
        const error = document.getElementById(errorId);
        error.hidden = true;
        try {
            await submit(form);
        } catch (exception) {
            error.textContent = exception.message;
            error.hidden = false;
        }
    });
}