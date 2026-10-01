"""Exercise the dashboard against a real disposable runtime, using explicit fixtures.

No live model or external account is used. Requires Playwright and Chromium.
Pass --screenshots DIR to capture the actual fixture-backed UI for visual review.
"""
import argparse
import json
from pathlib import Path
import os
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from playwright.sync_api import sync_playwright, expect
from test_runtime import RuntimeTests
from opendots.server import make_server

parser = argparse.ArgumentParser()
parser.add_argument('--screenshots', type=Path)
args = parser.parse_args()
case = RuntimeTests()
with patch.dict(os.environ, {'OPENDOTS_TEST_SANDBOX': 'trusted-local'}):
    case.setUp()
engine = case.engine
engine.ingest({**case.event('dashboard-deployment', type='github.issue.opened'), 'payload': {'repo': 'kubernetes/kubernetes', 'title': 'Deployment has zero replicas'}})
engine.ingest({**case.event('dashboard-accessibility', target='react', type='timer.scout'), 'payload': {'repo': 'facebook/react', 'title': 'Search button needs an accessible label'}})
engine.drain()
server = make_server(engine, port=0)
thread = threading.Thread(target=server.serve_forever)
thread.start()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1150}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_address[1]}')
            expect(page.locator('body')).to_have_attribute('data-connection', 'online')
            page.locator('#goal-select').select_option('react')
            expect(page.locator('#focus-event')).to_contain_text('Search button needs an accessible label')
            expect(page.locator('#focus-review .diff-add')).to_contain_text('aria-label')
            expect(page.locator('#focus-review')).to_contain_text('Approval required')
            # Polling must preserve selection, focused controls and the diff scroll position.
            approval = page.locator('#focus-review .approve')
            approval.focus()
            page.wait_for_timeout(1200)
            expect(approval).to_be_focused()
            expect(page.locator('#goal-select')).to_have_value('react')
            if args.screenshots:
                page.locator('h1').click()
                args.screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshots / 'dashboard-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#focus').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), 'Mobile overflow'
            if args.screenshots:
                page.screenshot(path=str(args.screenshots / 'dashboard-mobile.png'))
            page.locator('nav a[href="#approvals"]').click()
            expect(page.locator('nav a[href="#approvals"]')).to_have_attribute('aria-current', 'location')
            page.set_viewport_size({'width': 1440, 'height': 1150})
            page.locator('#goal-select').select_option('kubernetes')
            expect(page.locator('#focus-event')).to_contain_text('Deployment has zero replicas')
            page.locator('#goal-select').select_option('react')
            engine.store.pause('react', True)
            expect(page.locator('#goal-meta')).to_contain_text('Paused')
            engine.store.pause('react', False)
            expect(page.locator('#goal-meta')).to_contain_text('Configured agent')

            # Failed actions stay visible until dismissed; polling recovery clears only the connection warning.
            decision_url = '**/api/work/*/decision'
            held = []
            page.route(decision_url, lambda route: held.append(route))
            page.locator('#focus-review .reject').click()
            expect(page.locator('#focus-review .approve')).to_be_disabled()
            expect(page.locator('#focus-review .reject')).to_be_disabled()
            work_id = page.locator('#focus-review [data-work-id]').get_attribute('data-work-id')
            for button in page.locator(f'[data-decision-id="{work_id}"]').all():
                expect(button).to_be_disabled()
            page.locator('#focus-review .approve').evaluate('(button) => button.click()')
            assert len(held) == 1, 'Only one concurrent decision request is allowed'
            held[0].fulfill(status=409, content_type='application/json', body='{"error":"Approval changed; inspect the current action"}')
            expect(page.locator('#error')).to_contain_text('Approval changed')
            expect(page.locator('#focus-review .reject')).to_be_enabled()
            page.unroute(decision_url)
            page.route('**/api/state', lambda route: route.abort())
            expect(page.locator('#connection-warning')).to_be_visible()
            expect(page.locator('#focus-review .approve')).to_be_disabled()
            expect(page.locator('#event-form button[type=submit]')).to_be_disabled()
            page.unroute('**/api/state')
            expect(page.locator('#connection-warning')).to_be_hidden()
            expect(page.locator('#focus-review .approve')).to_be_enabled()
            expect(page.locator('#error')).to_be_visible()
            page.locator('#dismiss-error').click()
            expect(page.locator('#error')).to_be_hidden()

            # Approve one actual persisted proposal, preserving the exact token.
            work = next(w for w in engine.snapshot()['work'] if w['target_id'] == 'react')
            with page.expect_request(lambda r: r.url.endswith('/decision') and r.method == 'POST') as sent:
                page.locator('#focus-review .approve').click()
            assert sent.value.post_data_json == {'approved': True, 'approval_token': work['approval_token']}
            expect(page.locator('#focus-review')).to_contain_text('No decision waiting')
            engine.drain()
            expect(page.locator('#completed')).to_have_text('1')
            card = page.locator(f'#workflow-list [data-work-id="{work["id"]}"]')
            expect(card.locator('.check-evidence')).to_contain_text('accessibility')
            card.get_by_role('button', name='View retained patch').click()
            expect(page.locator('#evidence-dialog')).to_be_visible()
            expect(page.locator('#patch-content .diff-add')).to_contain_text('aria-label')
            page.locator('#close-evidence').click()
            # The other proposal can be rejected without executing its write.
            page.locator('#goal-select').select_option('kubernetes')
            page.locator('#focus-review .reject').click()
            expect(page.locator('#focus-review')).to_contain_text('No decision waiting')
            expect(page.locator('#waiting')).to_have_text('0')
            expect(page.locator('#review-list .review-card')).to_have_count(0)
            expect(page.locator('#review-list')).to_contain_text('No actions awaiting review')

            engine.ingest({**case.event('dashboard-untrusted', target='react', type='timer.scout'), 'payload': {'repo': 'facebook/react', 'title': '<img src=x onerror=alert(1)> Untrusted event'}})
            page.locator('#goal-select').select_option('react')
            expect(page.locator('#focus-event')).to_contain_text('<img src=x onerror=alert(1)>')
            assert page.locator('#focus-event img').count() == 0
            # Explicit empty snapshot: no fabricated agents, events or approval controls.
            empty = engine.snapshot()
            empty.update(targets=[], work=[], audit=[], counts={}, event_count=0, event_stream={'events': [], 'next_before': None})
            page.route('**/api/state', lambda route: route.fulfill(content_type='application/json', body=json.dumps(empty)))
            expect(page.locator('#goal-title')).to_have_text('Give your first agent a goal')
            expect(page.locator('#goal-select')).to_be_disabled()
            expect(page.locator('#event-form button[type=submit]')).to_be_disabled()
            expect(page.locator('#target-count')).to_have_text('0')
            expect(page.locator('#focus-review .approve')).to_have_count(0)
            assert not errors, errors
            print('Dashboard browser checks passed: live goals, stable focus, diff rendering, navigation, mobile, pause, errors/recovery, token approval, rejection, checks, retained patch and escaping.')
        finally:
            browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join()
    case.tearDown()
