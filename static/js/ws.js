// Auto-reconnecting WebSocket with exponential backoff.
import { getToken } from "./api.js";

export class ReconnectingWS {
    constructor(path, { onMessage, onState } = {}) {
        this.path = path;
        this.onMessage = onMessage || (() => { });
        this.onState = onState || (() => { });
        this.backoff = 500;
        this.maxBackoff = 15000;
        this.closed = false;
        this.ws = null;
    }

    url() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const sep = this.path.includes("?") ? "&" : "?";
        return `${proto}://${location.host}${this.path}${sep}token=${encodeURIComponent(getToken() || "")}`;
    }

    connect() {
        this.closed = false;
        this.onState("connecting");
        try {
            this.ws = new WebSocket(this.url());
        } catch (e) {
            return this._retry();
        }
        this.ws.onopen = () => {
            this.backoff = 500;
            this.onState("open");
        };
        this.ws.onmessage = (ev) => {
            let data = ev.data;
            try {
                data = JSON.parse(ev.data);
            } catch (_) { }
            this.onMessage(data);
        };
        this.ws.onclose = () => {
            if (!this.closed) this._retry();
        };
        this.ws.onerror = () => {
            try { this.ws.close(); } catch (_) { }
        };
    }

    _retry() {
        this.onState("reconnecting");
        setTimeout(() => {
            if (!this.closed) this.connect();
        }, this.backoff);
        this.backoff = Math.min(this.backoff * 2, this.maxBackoff);
    }

    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(typeof data === "string" ? data : JSON.stringify(data));
        }
    }

    sendRaw(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(data);
    }

    close() {
        this.closed = true;
        if (this.ws) try { this.ws.close(); } catch (_) { }
    }
}
