"""Shared private failure evidence; business association stays in callers."""
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from .snapshot import SNAPSHOT_SCRIPT, safe_url
from .state import write_private_bytes, write_private_json

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MASK_SELECTOR = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable],#nav-link-accountList"

async def capture_page_files(browser, directory, metadata):
    if browser is None:
        metadata["capture_errors"].append("browser_unavailable")
        return
    navigation = getattr(browser, "navigation_info", {})
    if isinstance(navigation, dict):
        metadata["requested_url"] = safe_url(navigation.get("requested_url"))
        metadata["http_status"] = navigation.get("http_status") if type(navigation.get("http_status")) is int else None
        metadata["redirects"] = [safe_url(url) for url in navigation.get("redirects", []) if safe_url(url)]
    try:
        data = await browser.evaluate(SNAPSHOT_SCRIPT)
        metadata.update(url=safe_url(data.get("url")), title=data.get("title"))
        html = data.get("html", "").encode()
        metadata["html_truncated"] = len(html) > MAX_HTML_BYTES
        write_private_bytes(directory / "page.html", html[:MAX_HTML_BYTES])
    except Exception as failure:
        metadata["capture_errors"].append("html_" + type(failure).__name__)
    try:
        page = getattr(browser, "page", browser if hasattr(browser, "screenshot") else None)
        if page is not None:
            image = await page.screenshot(type="png", full_page=False, timeout=5000, animations="disabled", mask=[page.locator(MASK_SELECTOR)])
        else:
            # Edge 诊断入口走原调试连接；不读取会话存储或请求头。
            await browser.evaluate("""(() => {const s=document.createElement('style');s.id='selection-diagnostic-mask';s.textContent='input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable],#nav-link-accountList{visibility:hidden!important}';document.head.append(s)})()""")
            try:
                response = await browser.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                image = base64.b64decode(response["data"])
            finally:
                await browser.evaluate("document.querySelector('#selection-diagnostic-mask')?.remove()")
        if len(image) > MAX_IMAGE_BYTES:
            raise ValueError("image_too_large")
        write_private_bytes(directory / "page.png", image)
    except Exception as failure:
        metadata["capture_errors"].append("screenshot_" + type(failure).__name__)


def write_manifest(directory):
    names = {'metadata.json', 'reason.md', 'reason.json', 'page.html', 'page.png'}
    files = [{'path': p.name, 'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(directory.iterdir()) if p.name in names and p.is_file() and not p.is_symlink()]
    write_private_json(directory / 'manifest.json', {'format_version': 1, 'files': files})


async def capture_diagnostic(browser, root, reason):
    identifier = uuid4().hex
    directory = Path(root) / identifier
    metadata = {'format_version': 1, 'reason': reason, 'observed_at': time.time(), 'capture_errors': []}
    try:
        directory.mkdir(parents=True, mode=0o700)
        write_private_json(directory / 'reason.json', metadata)
        try:
            async with asyncio.timeout(8):
                await capture_page_files(browser, directory, metadata)
        except Exception as failure:
            metadata['capture_errors'].append(type(failure).__name__)
        write_private_json(directory / 'reason.json', metadata)
        write_manifest(directory)
        return identifier
    except Exception:
        return None
