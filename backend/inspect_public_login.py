"""Read public login field metadata only. Never enters identifiers, passwords or OTPs."""
import json
import re
import argparse
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, Error

from app.integrations.banks.profiles import HDFC


def describe_fields(page):
    results = []
    for frame in page.frames:
        origin = urlsplit(frame.url)
        if origin.hostname not in HDFC.domains:
            continue
        fields = frame.locator("input, button, [role=button], select, a").evaluate_all("""nodes => nodes
            .filter(n => n.getClientRects().length > 0).map(n => ({
                tag: n.tagName, id: n.id, name: n.getAttribute('name'), type: n.getAttribute('type'),
                placeholder: n.getAttribute('placeholder'), label: n.getAttribute('aria-label'),
                autocomplete: n.getAttribute('autocomplete'),
                text: ['BUTTON','A'].includes(n.tagName) ? n.innerText.slice(0, 100) : null
            }))""")
        # No input values, hrefs, query strings, cookies, page HTML or response bodies are collected.
        safe = json.loads(re.sub(r"\d{5,}", "[masked]", json.dumps(fields)))
        results.append({"frame_path": re.sub(r"\d{5,}", "[masked]", origin.path), "fields": safe})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible", action="store_true", help="User-controlled authentication and read-only selector inspection")
    options = parser.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=not options.visible)
        try:
            page = browser.new_page(ignore_https_errors=False)
            try:
                page.goto(HDFC.login_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
            except Error as exc:
                message = re.sub(r"https?://[^\s\"']+", "[bank URL]", str(exc).splitlines()[0])
                print(json.dumps({"navigation_error": type(exc).__name__, "message": message}))
            location = urlsplit(page.url)
            print(json.dumps({"origin": f"{location.scheme}://{location.hostname}", "path": location.path}))
            if location.hostname in HDFC.domains and not options.visible:
                print(json.dumps({"title": page.title(), "public_message": page.locator("body").inner_text()[:1000]}))
            last = None
            deadline = time.monotonic() + (600 if options.visible else 1)
            while not page.is_closed() and time.monotonic() < deadline:
                try:
                    fields = describe_fields(page)
                    if fields != last:
                        print(json.dumps({"controls": fields}), flush=True)
                        last = fields
                    if not options.visible:
                        break
                    page.wait_for_timeout(3000)
                except Error:
                    if page.is_closed():
                        break
        finally:
            browser.close()


if __name__ == "__main__":
    main()
