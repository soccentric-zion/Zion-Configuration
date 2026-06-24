// Minimal fetch wrapper: attaches the JWT, unwraps the error envelope.
const state = { token: localStorage.getItem("token") || null };

export function setToken(t) {
    state.token = t;
    if (t) localStorage.setItem("token", t);
    else localStorage.removeItem("token");
}
export function getToken() {
    return state.token;
}

async function handle(res) {
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("application/json")) {
        if (!res.ok) throw { code: "http_" + res.status, message: res.statusText };
        return res;
    }
    const body = await res.json();
    if (!res.ok) {
        const err = body.error || { code: "error", message: "Request failed" };
        err.status = res.status;
        throw err;
    }
    return body;
}

function authHeaders(extra = {}) {
    const h = { ...extra };
    if (state.token) h["Authorization"] = "Bearer " + state.token;
    return h;
}

export const api = {
    async get(url) {
        return handle(await fetch(url, { headers: authHeaders() }));
    },
    async post(url, body) {
        return handle(
            await fetch(url, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: body === undefined ? undefined : JSON.stringify(body),
            })
        );
    },
    async patch(url, body) {
        return handle(
            await fetch(url, {
                method: "PATCH",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify(body),
            })
        );
    },
    async del(url) {
        return handle(await fetch(url, { method: "DELETE", headers: authHeaders() }));
    },
    async upload(url, formData) {
        return handle(await fetch(url, { method: "POST", headers: authHeaders(), body: formData }));
    },
};
