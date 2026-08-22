#!/usr/bin/env python3
"""Local-only Web UI for obtaining Home Assistant HiLink configuration values."""

from __future__ import annotations

import argparse
import ipaddress
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tools.fetch_huawei_credentials import (
    HuaweiApiError,
    authorization_code_from_input,
    build_authorization_url,
    fetch_credentials,
)
from tools.match_hilink_credentials import build_client

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Huawei HiLink 凭据助手</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 920px; margin: 48px auto; padding: 0 20px; line-height: 1.55; }
    .card { border: 1px solid #8886; border-radius: 14px; padding: 20px; margin: 18px 0; }
    button, input { padding: 10px 15px; margin: 4px 8px 4px 0; }
    button { cursor: pointer; }
    input { min-width: min(520px, 80vw); }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { border-bottom: 1px solid #8885; padding: 9px; text-align: left; }
    code { overflow-wrap: anywhere; }
    .muted { opacity: .72; }
    .danger { color: #d33; }
    #results[hidden] { display: none; }
  </style>
</head>
<body>
  <h1>Huawei HiLink 凭据助手</h1>
  <p>这个页面只由本机 <code>127.0.0.1</code> 提供。登录在华为官方页面完成；
     OAuth token 不显示、不写入磁盘。</p>
  <div class="card">
    <button id="start">打开华为授权页面</button>
    <button id="clear">清除页面中的凭据</button>
    <p id="status">等待开始</p>
    <p class="muted">请确认登录页域名属于 <code>huawei.com</code>，并且授权范围只包含
      账号基础资料、智慧生活设备与技能。完成后可关闭弹出的授权浏览器。</p>
  </div>
  <section id="results" class="card" hidden>
    <h2>Home Assistant 需要填写的内容</h2>
    <p>从路由器复制灯具 IP，用空格或逗号分隔。匹配过程只读取状态，不控制灯。</p>
    <input id="hosts" autocomplete="off" placeholder="例如：192.168.x.10 192.168.x.11">
    <button id="match">只读匹配 IP</button>
    <table>
      <thead>
        <tr><th>名称</th><th>IP</th><th>型号</th><th>device ID</th><th>authCode</th></tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
    <button id="copy">复制 JSON</button>
    <p class="danger">这些值可以控制你的灯。复制后请放入密码管理器，不要发到 Issue 或聊天。</p>
  </section>
  <script>
    const statusNode = document.querySelector('#status');
    const results = document.querySelector('#results');
    const rows = document.querySelector('#rows');
    let latestDevices = [];
    async function api(path, options = {}) {
      const response = await fetch(path, {cache: 'no-store', ...options});
      if (!response.ok) throw new Error('request failed');
      return response.json();
    }
    function render(data) {
      statusNode.textContent = data.message;
      latestDevices = data.devices || [];
      rows.replaceChildren();
      for (const [index, device] of latestDevices.entries()) {
        const tr = document.createElement('tr');
        for (const value of [
          device.name || `灯 ${index + 1}`,
          device.host || '待匹配',
          device.model || '未知',
          device.device_id,
          device.auth_code,
        ]) {
          const td = document.createElement('td');
          const code = document.createElement('code');
          code.textContent = String(value);
          td.append(code);
          tr.append(td);
        }
        rows.append(tr);
      }
      results.hidden = latestDevices.length === 0;
    }
    document.querySelector('#start').addEventListener('click', async () => {
      render(await api('api/start', {method: 'POST'}));
    });
    document.querySelector('#clear').addEventListener('click', async () => {
      render(await api('api/clear', {method: 'POST'}));
    });
    document.querySelector('#match').addEventListener('click', async () => {
      const hosts = document.querySelector('#hosts').value.split(/[\\s,，;；]+/).filter(Boolean);
      render(await api('api/match', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hosts}),
      }));
    });
    document.querySelector('#copy').addEventListener('click', async () => {
      await navigator.clipboard.writeText(JSON.stringify({devices: latestDevices}, null, 2));
      statusNode.textContent = '已复制；请立即保存到密码管理器';
    });
    async function poll() {
      try { render(await api('api/status')); } catch (_) {}
      setTimeout(poll, 1000);
    }
    poll();
  </script>
</body>
</html>
"""


@dataclass(slots=True)
class CredentialState:
    """Credential results held only in process memory."""

    status: str = "idle"
    message: str = "等待开始"
    devices: list[dict[str, str]] = field(default_factory=list)
    generation: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        """Return a copy safe for JSON serialization."""
        with self.lock:
            return {
                "status": self.status,
                "message": self.message,
                "devices": [dict(device) for device in self.devices],
            }

    def update(
        self,
        status: str,
        message: str,
        devices: list[dict[str, str]] | None = None,
    ) -> None:
        """Atomically update user-visible state."""
        with self.lock:
            self.status = status
            self.message = message
            if devices is not None:
                self.devices = [dict(device) for device in devices]

    def begin(
        self,
        status: str,
        message: str,
        devices: list[dict[str, str]] | None = None,
    ) -> int:
        """Start a new operation and invalidate older workers."""
        with self.lock:
            self.generation += 1
            self.status = status
            self.message = message
            if devices is not None:
                self.devices = [dict(device) for device in devices]
            return self.generation

    def update_if_current(
        self,
        generation: int,
        status: str,
        message: str,
        devices: list[dict[str, str]] | None = None,
    ) -> bool:
        """Update only if the user has not cleared or restarted the flow."""
        with self.lock:
            if generation != self.generation:
                return False
            self.status = status
            self.message = message
            if devices is not None:
                self.devices = [dict(device) for device in devices]
            return True

    def clear(self) -> None:
        """Discard all credential material from application state."""
        self.begin("idle", "页面中的凭据已清除", [])


def capture_callback_with_playwright(
    authorization_url: str,
    *,
    channel: str | None,
    timeout_seconds: int,
    on_opened: Callable[[], None],
) -> str:
    """Open an interactive browser and capture its custom-scheme redirect."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright-not-installed") from exc

    callback_urls: list[str] = []

    def observe_url(url: str) -> None:
        if url.startswith("hms://redirect_url") and not callback_urls:
            callback_urls.append(url)

    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": False}
        if channel:
            launch_options["channel"] = channel
        browser = playwright.chromium.launch(**launch_options)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.on("request", lambda request: observe_url(request.url))
            page.on("requestfailed", lambda request: observe_url(request.url))
            page.on("framenavigated", lambda frame: observe_url(frame.url))
            devtools = context.new_cdp_session(page)
            devtools.send("Page.enable")
            devtools.send("Network.enable")
            devtools.on(
                "Page.frameRequestedNavigation",
                lambda event: observe_url(str(event.get("url", ""))),
            )
            devtools.on(
                "Network.requestWillBeSent",
                lambda event: observe_url(str(event.get("request", {}).get("url", ""))),
            )
            try:
                page.goto(authorization_url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightError:
                if not callback_urls:
                    raise
            on_opened()
            deadline = time.monotonic() + timeout_seconds
            while not callback_urls and time.monotonic() < deadline:
                try:
                    page.wait_for_timeout(250)
                except PlaywrightError:
                    if callback_urls:
                        break
                    raise
            if not callback_urls:
                raise TimeoutError("authorization-timeout")
            return callback_urls[0]
        finally:
            browser.close()


def authorization_worker(
    state: CredentialState,
    *,
    generation: int,
    browser_channel: str | None,
    timeout_seconds: int,
) -> None:
    """Complete interactive authorization and fetch credentials."""
    try:
        authorization_url, expected_state = build_authorization_url()
        callback_url = capture_callback_with_playwright(
            authorization_url,
            channel=browser_channel,
            timeout_seconds=timeout_seconds,
            on_opened=lambda: state.update_if_current(
                generation, "authorizing", "请在弹出的华为官方页面完成登录与授权"
            ),
        )
        one_time_code = authorization_code_from_input(callback_url, expected_state)
        if not state.update_if_current(
            generation, "fetching", "授权成功，正在读取设备与本地控制凭据"
        ):
            return
        devices, home_count, device_count = fetch_credentials(one_time_code, include_names=True)
        state.update_if_current(
            generation,
            "ready",
            f"完成：找到 {home_count} 个家庭、{device_count} 台设备、{len(devices)} 条本地凭据",
            devices,
        )
    except RuntimeError as exc:
        if str(exc) == "playwright-not-installed":
            message = "缺少 Playwright；请按文档安装浏览器运行库后重试"
        else:
            message = "浏览器授权流程失败；请查看文档中的兼容性排错"
        state.update_if_current(generation, "error", message, [])
    except TimeoutError:
        state.update_if_current(generation, "error", "授权等待超时，请重新开始", [])
    except (HuaweiApiError, ValueError):
        state.update_if_current(
            generation, "error", "授权或华为云接口返回异常；未保存任何 token", []
        )
    except Exception:
        state.update_if_current(
            generation, "error", "凭据助手发生未分类错误；敏感响应未写入日志", []
        )


def match_worker(
    state: CredentialState,
    hosts: list[str],
    *,
    generation: int,
    adb_serial: str | None,
    adb_path: str,
) -> None:
    """Read state to prove each host/credential pairing without controlling a lamp."""
    devices = state.snapshot()["devices"]
    matched = 0
    used_indices = {index for index, device in enumerate(devices) if device.get("host")}
    for host in hosts:
        for index, device in enumerate(devices):
            if index in used_indices:
                continue
            try:
                client = build_client(host, device, 2.5, adb_serial, adb_path)
                client.create_session()
                client.read_state()
            except Exception:
                continue
            device["host"] = host
            used_indices.add(index)
            matched += 1
            break
    state.update_if_current(
        generation,
        "ready",
        f"只读匹配完成：本次匹配 {matched} 台，共 {len(used_indices)}/{len(devices)} 台已有 IP",
        devices,
    )


def make_handler(
    state: CredentialState,
    route_secret: str,
    browser_channel: str | None,
    timeout_seconds: int,
    adb_serial: str | None,
    adb_path: str,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one unguessable local route."""
    prefix = f"/{route_secret}/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_common_headers(self, content_type: str) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'",
            )

        def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_common_headers("application/json;charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == prefix:
                body = PAGE.encode()
                self.send_response(HTTPStatus.OK)
                self.send_common_headers("text/html;charset=UTF-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == f"{prefix}api/status":
                self.send_json(state.snapshot())
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == f"{prefix}api/start":
                current = state.snapshot()["status"]
                if current in {"opening", "authorizing", "fetching", "matching"}:
                    self.send_json(state.snapshot(), HTTPStatus.CONFLICT)
                    return
                generation = state.begin("opening", "正在启动独立的授权浏览器", [])
                thread = threading.Thread(
                    target=authorization_worker,
                    kwargs={
                        "state": state,
                        "generation": generation,
                        "browser_channel": browser_channel,
                        "timeout_seconds": timeout_seconds,
                    },
                    daemon=True,
                )
                thread.start()
                self.send_json(state.snapshot())
                return
            if path == f"{prefix}api/match":
                current = state.snapshot()
                if current["status"] != "ready" or not current["devices"]:
                    self.send_json(current, HTTPStatus.CONFLICT)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(length).decode())
                    raw_hosts = payload.get("hosts") if isinstance(payload, dict) else None
                    if not isinstance(raw_hosts, list):
                        raise ValueError
                    hosts = list(
                        dict.fromkeys(str(ipaddress.IPv4Address(host)) for host in raw_hosts)
                    )
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    self.send_json(current, HTTPStatus.BAD_REQUEST)
                    return
                generation = state.begin(
                    "matching", "正在执行只读 IP 与凭据匹配", current["devices"]
                )
                thread = threading.Thread(
                    target=match_worker,
                    kwargs={
                        "state": state,
                        "hosts": hosts,
                        "generation": generation,
                        "adb_serial": adb_serial,
                        "adb_path": adb_path,
                    },
                    daemon=True,
                )
                thread.start()
                self.send_json(state.snapshot())
                return
            if path == f"{prefix}api/clear":
                state.clear()
                self.send_json(state.snapshot())
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    return Handler


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=0, help="loopback port; default chooses a free port"
    )
    parser.add_argument(
        "--browser-channel",
        choices=("chrome", "msedge"),
        help="use an installed Chrome/Edge instead of Playwright Chromium",
    )
    parser.add_argument("--timeout", type=int, default=600, help="authorization timeout in seconds")
    parser.add_argument(
        "--adb-serial", help="optional Android wireless-debug serial for IP matching"
    )
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--no-open", action="store_true", help="do not open the local UI")
    return parser.parse_args()


def main() -> int:
    """Serve the loopback-only credential UI until interrupted."""
    args = parse_args()
    route_secret = secrets.token_urlsafe(24)
    state = CredentialState()
    handler = make_handler(
        state,
        route_secret,
        args.browser_channel,
        args.timeout,
        args.adb_serial,
        args.adb_path,
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{server.server_port}/{route_secret}/"
    print("Local credential UI:")
    print(url)
    print("Press Ctrl-C to clear in-memory credentials and stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.clear()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
