//! TLS listener and HTTP connection serving.
//!
//! Spec §4.8: the daemon is TLS-only by default. A separate plaintext HTTP
//! listener runs alongside the TLS listener and **redirects every request to
//! HTTPS**. An administrator may opt in to plain-HTTP serving via
//! `security.allow_plain_http` (a first-boot escape hatch) — when that flag is
//! true the plain listener serves the full app over HTTP instead of
//! redirecting. The flag is re-read on every request, so toggling it in the
//! Security page takes effect without a daemon restart.

use std::path::Path;

use axum::extract::State;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::Router;
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder as ConnBuilder;
use hyper_util::service::TowerToHyperService;
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;
use tower::ServiceExt as _;
use webconfig_engine::manifest::BoardManifest;

use crate::engine_bridge::EngineBridge;
use crate::tls;

/// State carried by the plaintext-HTTP listener's gate (spec §4.8).
#[derive(Clone)]
struct PlainGate {
    /// Used to re-read `security.allow_plain_http` per request.
    engine: EngineBridge,
    /// The fully-wired app, served verbatim when plain HTTP is opted in.
    app: Router,
    /// The TLS port the redirect points at.
    tls_port: u16,
}

/// Ensure the device certificate exists, then serve the application over TLS,
/// with a plaintext HTTP listener redirecting to HTTPS (spec §4.8).
pub(crate) async fn serve(
    app: Router,
    engine: EngineBridge,
    webconfig_dir: &Path,
    manifest: &BoardManifest,
) -> anyhow::Result<()> {
    let cert = webconfig_dir.join("cert.pem");
    let key = webconfig_dir.join("key.pem");
    let hostname = std::env::var("WEBCONFIG_HOSTNAME").ok();
    tls::ensure_self_signed(&cert, &key, hostname.as_deref())
        .map_err(|error| anyhow::anyhow!("tls init: {error}"))?;
    let acceptor =
        tls::acceptor(&cert, &key).map_err(|error| anyhow::anyhow!("tls acceptor: {error}"))?;
    let acceptor = TlsAcceptor::from(acceptor);

    let tls_port: u16 = std::env::var("WEBCONFIG_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(8443);
    let tls_addr = format!("0.0.0.0:{tls_port}");
    tracing::info!(
        "webconfigd listening on https://{tls_addr} (platform={})",
        manifest.platform
    );

    // Plaintext HTTP listener (spec §4.8): redirects to HTTPS unless an admin
    // has opted in via `security.allow_plain_http`. `WEBCONFIG_PLAIN_PORT`
    // selects the port (default 8080); set it to `0` to disable the listener.
    let plain_port: u16 = std::env::var("WEBCONFIG_PLAIN_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(8080);
    if plain_port != 0 {
        let plain_app = Router::new()
            .fallback(plain_http_gate)
            .with_state(PlainGate {
                engine: engine.clone(),
                app: app.clone(),
                tls_port,
            });
        let plain_addr = format!("0.0.0.0:{plain_port}");
        match TcpListener::bind(&plain_addr).await {
            Ok(plain_listener) => {
                tracing::info!(
                    "webconfigd plain HTTP on http://{plain_addr} (301 -> https://:{tls_port} unless security.allow_plain_http)"
                );
                tokio::spawn(async move {
                    loop {
                        let (stream, _peer) = match plain_listener.accept().await {
                            Ok(s) => s,
                            Err(e) => {
                                tracing::warn!(error = %e, "plain http accept failed");
                                continue;
                            }
                        };
                        let plain_app = plain_app.clone();
                        tokio::spawn(async move {
                            serve_plain_connection(plain_app, stream).await;
                        });
                    }
                });
            }
            Err(e) => {
                // A bound TLS listener is the critical path; a plain-HTTP
                // bind failure (port in use) must not take the daemon down —
                // the device is still reachable over HTTPS.
                tracing::warn!(error = %e, "plain http listener disabled (could not bind {plain_addr})");
            }
        }
    }

    let listener = TcpListener::bind(&tls_addr).await?;
    loop {
        let (stream, _peer) = listener.accept().await?;
        let acceptor = acceptor.clone();
        let app = app.clone();
        tokio::spawn(async move {
            let Ok(tls) = acceptor.accept(stream).await else {
                return;
            };
            serve_connection(app, tls).await;
        });
    }
}

/// Per-request gate for the plaintext listener (spec §4.8). Reads the current
/// `security.allow_plain_http` and either serves the app (opt-in) or emits a
/// permanent redirect to the HTTPS listener.
async fn plain_http_gate(State(gate): State<PlainGate>, req: axum::extract::Request) -> Response {
    let allow_plain = gate
        .engine
        .load()
        .map(|c| c.security.allow_plain_http)
        .unwrap_or(false);
    if allow_plain {
        // Explicit admin opt-in: serve the full app over plain HTTP.
        match gate.app.clone().oneshot(req).await {
            Ok(resp) => resp,
            // Router::oneshot is infallible; this arm is unreachable.
            Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        }
    } else {
        // Default: redirect every plain-HTTP request to HTTPS.
        let host = req
            .headers()
            .get(header::HOST)
            .and_then(|h| h.to_str().ok())
            .unwrap_or("localhost");
        let path_and_query = req
            .uri()
            .path_and_query()
            .map(|p| p.as_str())
            .unwrap_or("/");
        let location = https_redirect_location(host, path_and_query, gate.tls_port);
        Redirect::permanent(&location).into_response()
    }
}

/// Build the `Location` for the HTTP→HTTPS redirect, preserving the request
/// hostname (stripped of any explicit port) and appending the TLS port.
///
/// Exposed as a pure helper so the redirect target is unit-testable without
/// spinning up a listener.
fn https_redirect_location(host_header: &str, path_and_query: &str, tls_port: u16) -> String {
    // `Host:` may carry `host:port`; drop the port so we don't end up with
    // `host:oldport:tlsport`.
    let hostname = host_header
        .rsplit_once(':')
        .map(|(h, _)| h)
        .unwrap_or(host_header);
    format!("https://{}:{}{}", hostname, tls_port, path_and_query)
}

async fn serve_connection(
    app: Router,
    tls: tokio_rustls::server::TlsStream<tokio::net::TcpStream>,
) {
    let io = TokioIo::new(tls);
    let tower_service =
        app.map_request(|req: http::Request<hyper::body::Incoming>| req.map(axum::body::Body::new));
    let hyper_service = TowerToHyperService::new(tower_service);
    let _ = ConnBuilder::new(TokioExecutor::new())
        .serve_connection_with_upgrades(io, hyper_service)
        .await;
}

async fn serve_plain_connection(app: Router, stream: tokio::net::TcpStream) {
    let io = TokioIo::new(stream);
    let tower_service =
        app.map_request(|req: http::Request<hyper::body::Incoming>| req.map(axum::body::Body::new));
    let hyper_service = TowerToHyperService::new(tower_service);
    let _ = ConnBuilder::new(TokioExecutor::new())
        .serve_connection_with_upgrades(io, hyper_service)
        .await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redirect_preserves_hostname_and_path() {
        let loc = https_redirect_location("device.local:8080", "/api/v1/system", 8443);
        assert_eq!(loc, "https://device.local:8443/api/v1/system");
    }

    #[test]
    fn redirect_strips_explicit_plain_port_from_host() {
        // Host header with the plain-listener port must not leak into the
        // redirect target as `host:8080:8443`.
        let loc = https_redirect_location("10.0.0.5:8080", "/", 8443);
        assert_eq!(loc, "https://10.0.0.5:8443/");
    }

    #[test]
    fn redirect_keeps_query_string() {
        let loc = https_redirect_location("device.local", "/logs?unit=webconfig&limit=50", 9443);
        assert_eq!(
            loc,
            "https://device.local:9443/logs?unit=webconfig&limit=50"
        );
    }

    #[test]
    fn redirect_handles_bare_host_without_port() {
        let loc = https_redirect_location("device.local", "/", 8443);
        assert_eq!(loc, "https://device.local:8443/");
    }
}
