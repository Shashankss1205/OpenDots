"""Check goal-overview signals against persisted runtime relevance decisions.

Uses disposable workspaces and a controlled assessor, never a live model.
Requires the development-only Playwright package and Chromium.
"""
from dataclasses import replace
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from playwright.sync_api import sync_playwright, expect
from test_events import EventTests
from opendots.server import make_server

case = EventTests()
case.setUp()
for name in ('other', 'rules'):
    (case.root / name).mkdir()
other = replace(case.target, id='other', name='Other goal', workspace=case.root / 'other')
rules = replace(case.target, id='rules', name='Rules-only goal', workspace=case.root / 'rules',
                subscriptions=({'types': ['rules.*']},), relevance={'mode': 'off'})
case.config = replace(case.config, targets=(case.target, other, rules))
engine = case.engine()


def assess(target, event):
    decision = event['payload'].get('decisions', {}).get(target.id, 'relevant')
    if decision == 'error':
        raise RuntimeError('Controlled assessment failure')
    return {'decision': decision, 'confidence': event['payload'].get('confidence', .9),
            'reason': 'Controlled assessment for ' + target.id}


case.provider.assess_relevance = assess


def emit(event_id, *, decision='relevant', confidence=.9, target='project', drain=True, event_type='input.failure', decisions=None):
    event = {'id': event_id, 'type': event_type, 'source': 'local',
             'payload': {'title': event_id, 'confidence': confidence,
                         'decisions': decisions or {'project': decision}}}
    if target is not None:
        event['target_id'] = target
    engine.ingest(event)
    if drain:
        engine.drain()


emit('Rejected-only event', decision='irrelevant')
server = make_server(engine, port=0)
thread = threading.Thread(target=server.serve_forever)
thread.start()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_address[1]}')
            expect(page.locator('body')).to_have_attribute('data-connection', 'online')
            page.locator('#goal-select').select_option('project')
            expect(page.locator('#received-list')).to_contain_text('Rejected-only event')
            expect(page.locator('#focus-event')).to_contain_text('No matching event')

            emit('Accepted checkout issue')
            expect(page.locator('#focus-event')).to_contain_text('Accepted checkout issue')
            for name, decision, confidence in (
                ('Irrelevant signal', 'irrelevant', .9),
                ('Uncertain signal', 'uncertain', .9),
                ('Low-confidence signal', 'relevant', .2),
                ('Failed assessment', 'error', .9),
            ):
                emit(name, decision=decision, confidence=confidence)
                expect(page.locator('#received-list')).to_contain_text(name)
                expect(page.locator('#focus-event')).to_contain_text('Accepted checkout issue')

            emit('Awaiting assessment', drain=False)
            expect(page.locator('#received-list')).to_contain_text('Awaiting assessment')
            expect(page.locator('#focus-event')).to_contain_text('Accepted checkout issue')
            engine.drain()
            expect(page.locator('#focus-event')).to_contain_text('Awaiting assessment')
            emit('Filtered signal', event_type='unsubscribed.news')
            expect(page.locator('#received-list')).to_contain_text('Filtered signal')
            expect(page.locator('#focus-event')).to_contain_text('Awaiting assessment')

            # Older databases have work rows without persisted relevance decisions.
            emit('Legacy unassessed signal')
            with engine.store.connect() as db:
                db.execute('DELETE FROM event_decisions WHERE event_id=?', ('Legacy unassessed signal',))
            expect(page.locator('#received-list')).to_contain_text('legacy_unassessed')
            expect(page.locator('#focus-event')).to_contain_text('Awaiting assessment')

            emit('Different outcomes by goal', target=None,
                 decisions={'project': 'irrelevant', 'other': 'relevant'})
            expect(page.locator('#received-list')).to_contain_text('Different outcomes by goal')
            expect(page.locator('#focus-event')).to_contain_text('Awaiting assessment')
            page.locator('#goal-select').select_option('other')
            expect(page.locator('#focus-event')).to_contain_text('Different outcomes by goal')

            emit('Accepted rules-only signal', target='rules', event_type='rules.changed')
            page.locator('#goal-select').select_option('rules')
            expect(page.locator('#focus-event')).to_contain_text('Accepted rules-only signal')
            rules_decision = next(d for d in engine.store.event_detail('Accepted rules-only signal')['decisions'] if d['target_id'] == 'rules')
            assert rules_decision['status'] == 'disabled'
            assert not errors, errors
            print('Goal relevance browser checks passed: empty state, accepted signals, irrelevant/uncertain/error/pending/legacy exclusion, assessment completion, per-goal decisions and rules-only compatibility.')
        finally:
            browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join()
    case.tearDown()
