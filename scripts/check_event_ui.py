"""Browser integration check using a controlled relevance provider, never a live model.

Requires the development-only playwright package and its Chromium browser.
"""
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from playwright.sync_api import sync_playwright
from test_events import EventTests
from opendots.server import make_server

case = EventTests()
case.setUp()
engine = case.engine()
engine.ingest({'id': 'ui-filtered', 'type': 'unsubscribed.news', 'payload': {'title': 'Unmatched message'}})
case.emit(engine, 'ui-relevant')
engine.drain()
server = make_server(engine, port=0)
thread = threading.Thread(target=server.serve_forever)
thread.start()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_address[1]}')
            page.wait_for_function("document.querySelector('#received-list').textContent.includes('90%')")
            assert page.locator('#sample').is_hidden(), 'Demo must be hidden in normal runtime'
            page.locator('#listeners details').click()
            page.get_by_text('Types: input.*', exact=False).wait_for()
            page.locator('#event-type').fill('input.feedback')
            page.locator('#event-title').fill('<img src=x onerror=alert(1)> User information')
            page.locator('#event-payload').fill('{"body":"Actual producer detail"}')
            page.locator('#preview-event').click()
            assert 'Actual producer detail' in page.locator('#event-preview').inner_text()
            page.locator('#event-form button[type=submit]').click()
            page.wait_for_function("document.querySelector('#received-list').textContent.includes('input.feedback')")
            assert page.locator('#received-list img').count() == 0, 'Event text must not become HTML'
            page.locator('#received-list button').first.click()
            page.locator('#event-dialog[open]').wait_for()
            assert 'Actual producer detail' in page.locator('#event-detail').inner_text()
            page.locator('#close-event').click()
            page.locator('#event-search').fill('unsubscribed.news')
            page.locator('#event-search-form button[type=submit]').click()
            page.wait_for_function("document.querySelectorAll('#received-list .event-card').length===1 && document.querySelector('#received-list').textContent.includes('filtered')")
            page.locator('#event-live').click()
            page.wait_for_function("document.querySelectorAll('#received-list .event-card').length===3")
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#event-stream').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), 'Mobile overflow'
            assert not errors, errors
            print('Browser event integration passed: listeners, preview/send, escaped payload, details, search/live, mobile width.')
        finally:
            browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join()
    case.tearDown()
