"""Observed access signals, not guesses about account validity or root cause."""
from urllib.parse import urlsplit
from pathlib import Path

AMAZON_ACCESS_SCRIPT = Path(__file__).with_name("amazon_access.js").read_text(encoding="utf-8")


def classify_access(*, url='', status=None, challenge=False, verification_failed=False, login=False):
    if challenge or '/_____tmd_____/' in urlsplit(url).path:
        return {'state': 'verification_required', 'reason': 'verification_failed' if verification_failed else 'challenge',
                'evidence': ['visible_challenge' if challenge else 'challenge_path']}
    if status in {401, 403, 429}:
        return {'state': 'access_denied', 'reason': 'http_restricted', 'evidence': [f'http_{status}']}
    if type(status) is int and status >= 400:
        return {'state': 'network_error', 'reason': 'http_error', 'evidence': [f'http_{status}']}
    if login:
        return {'state': 'login_required', 'reason': 'login_redirect', 'evidence': ['login_page']}
    return {'state': 'unknown', 'reason': 'unrecognized_page', 'evidence': []}


async def inspect_access(page, *, site, status=None, include_password=False):
    url = page.url
    host = urlsplit(url).hostname
    if host not in site.get('domains', []):
        return {'state': 'unknown', 'reason': 'unexpected_host', 'evidence': ['unexpected_host']}
    challenge = failed = False
    for frame in page.frames:
        for selector in site.get('challenge_selectors', []):
            group = frame.locator(selector)
            locators = [group.first] if await group.first.is_visible() else (await group.all())[:20]
            for locator in locators:
                if await locator.is_visible():
                    challenge = True
                    failed = failed or await frame.get_by_text('验证失败', exact=False).first.is_visible()
    body = await page.locator('body').inner_text()
    body = body[:5000] if isinstance(body, str) else ''
    challenge = challenge or any(text in body for text in ['请拖动下方滑块', '请完成安全验证', '验证失败，请点击'])
    login = host in site.get('login_hosts', [])
    if include_password:
        login = login or await page.locator('input[type="password"]').first.is_visible()
    result = classify_access(url=url, status=status, challenge=challenge, verification_failed=failed, login=login)
    if result['state'] == 'unknown' and '访问过于频繁' in body:
        return {'state': 'access_denied', 'reason': 'rate_limited', 'evidence': ['rate_limit_text']}
    return result
