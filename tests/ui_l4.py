"""Guided mirror acceptance in a real browser, fake endpoints only."""

import base64
import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(32)).decode()
UUID = "11111111-1111-4111-8111-111111111111"


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/static/"):
            self.path = self.path.removeprefix("/static")
        return super().do_GET()


server = ThreadingHTTPServer(
    ("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "backup_steward/steward/static"))
)
Thread(target=server.serve_forever, daemon=True).start()
policy = dict(
    enabled=False,
    schedule_enabled=False,
    schedule_time="06:00",
    label="Lisboa",
    host="mirror.example.test",
    port=2222,
    host_key=KEY,
    destination_uuid=UUID,
    sources=[dict(source="/media/immich", name="immich", uuid=UUID)],
    max_delete_files=1000,
    min_free_gib=20,
    timeout_minutes=720,
)
local = dict(
    version="0.2.0",
    busy=False,
    can_cancel=False,
    mode="production",
    schedule_enabled=False,
    timezone="Europe/Madrid",
    checksum_frequency="monthly",
    daily_retention_days=3,
    verification_sources=["/media/NAS", "/media/immich", "/media/filebrowser"],
    state=dict(state="idle", needs_cleanup=False, events=[]),
)
posts = []
offline = False


def route(r):
    global policy
    path = r.request.url.split("/v1/")[1]
    if offline:
        return r.abort()
    if r.request.method != "GET":
        posts.append((path, r.request.post_data_json))
    if path == "status":
        return r.fulfill(json=local)
    if path == "config":
        return r.fulfill(
            json=dict(
                source_mount="/media/NAS",
                source_uuid=UUID,
                additional_sources=["/media/immich", "/media/filebrowser"],
            )
        )
    if path == "l4/config":
        if r.request.method == "PUT":
            policy = r.request.post_data_json
        return r.fulfill(json=policy)
    if path == "l4/status":
        return r.fulfill(json=dict(enabled=policy["enabled"], busy=False, status="idle"))
    if path == "l4/pair":
        return r.fulfill(
            json=dict(protocol="steward-mirror-v1", sources=[dict(name="immich", public_key=KEY)])
        )
    if path == "l4/storage":
        return r.fulfill(json={"sources": [{"source": "/media/immich", "uuid": UUID}]})
    return r.fulfill(json={"accepted": True})


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            **(
                {"executable_path": os.environ["STEWARD_TEST_BROWSER"]}
                if os.environ.get("STEWARD_TEST_BROWSER")
                else {}
            ),
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.add_init_script("window.testPollers=[];window.setInterval=fn=>window.testPollers.push(fn)")
        page.route("**/v1/**", route)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}")
        page.wait_for_function("!document.getElementById('l4-settings').disabled")
        assert page.locator("#l4-run").is_disabled()
        page.locator("#l4-settings").click()
        page.locator("#l4-dialog").wait_for(state="visible")
        assert page.locator(".mirror-source").count() == 3
        page.locator("#l4-pair").click()
        page.locator("#l4-pairing").wait_for(state="visible")
        assert "PRIVATE" not in page.locator("#l4-pairing").input_value()
        assert json.loads(page.locator("#l4-pairing").input_value())["sources"][0]["public_key"] == KEY
        page.locator("#l4-form input[name=enabled]").check()
        page.locator("#l4-save").click()
        page.wait_for_function("document.getElementById('l4-enabled').checked")
        assert policy["enabled"]
        previews = ROOT / "data/l4-previews"
        previews.mkdir(parents=True, exist_ok=True)
        page.locator("#l4-dialog").screenshot(path=str(previews / "sender-settings-desktop.png"))
        page.locator("#l4-close").click()
        page.locator(".mirror-card").screenshot(path=str(previews / "sender-card-desktop.png"))
        page.locator("#l4-run").click()
        assert not any(path == "l4/run" for path, _ in posts)
        page.locator("#l4-yes").click()
        page.wait_for_timeout(100)
        assert sum(path == "l4/run" for path, _ in posts) == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#l4-settings").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#l4-dialog").screenshot(path=str(previews / "sender-settings-mobile.png"))
        page.locator("#l4-close").click()
        page.locator("#l4-enabled").uncheck()
        page.wait_for_function("!document.getElementById('l4-enabled').checked")
        assert not policy["enabled"] and not policy["schedule_enabled"]
        offline = True
        page.evaluate("window.testPollers.forEach(fn=>fn())")
        page.wait_for_function("document.getElementById('l4-settings').disabled")
        for name in ("enabled", "test", "run", "save", "pair", "yes"):
            assert page.locator("#l4-" + name).is_disabled()
        assert not errors, errors
        browser.close()
        print(
            "L4 UI passed: guided setup, source selection, public pairing, toggle, confirmation, offline locks, desktop/mobile layout."
        )
finally:
    server.shutdown()
