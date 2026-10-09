import copy
import unittest

from opendots.terminal import Session


class TerminalReviewTests(unittest.TestCase):
    def session(self, evidence):
        action = {'tool': 'replace_text', 'args': {'path': 'src/app.py'}}
        state = {'targets': [], 'work': [{'id': 1, 'target_id': 'project',
            'status': 'waiting_approval', 'approval_token': 'exact-action',
            'plan': {'summary': 'Fix app behavior', 'actions': [action]}, 'approval_index': 0}],
            'audit': [{'work_id': 1, 'kind': 'approval_requested',
                       'detail': {'preview': {'diff': '--- src/app.py\n+++ src/app.py\n-old\n+new\n'}}}, *evidence]}
        decisions = []
        class FakeClient:
            def request(self, path, body=None):
                if body is not None:
                    decisions.append((path, body))
                    return {}
                return state
        return Session(FakeClient()), state, decisions

    def test_review_summarizes_large_reads_and_checks_without_altering_evidence(self):
        content = 'PRIVATE_FILE_CONTENT\n' + 'é'*64000
        evidence = [
            {'work_id': 1, 'kind': 'action_completed', 'detail': {'tool': 'run_check',
             'result': {'name': 'unit', 'exit_code': 0, 'output': 'PASS\n' + 'verbose'*10000}}},
            {'work_id': 1, 'kind': 'action_completed', 'detail': {'tool': 'read_file',
             'result': {'path': 'src/app.py', 'content': content, 'sha256': 'a'*64}}}]
        session, state, decisions = self.session(evidence)
        original = copy.deepcopy(state)
        review = session.submit('/review 1')
        self.assertIn('+new', review)
        self.assertLess(review.index('+new'), review.index('Evidence:'))
        self.assertTrue(f'{len(content.encode())} bytes' in review, 'Missing UTF-8 byte count')
        self.assertIn('a'*64, review)
        self.assertTrue('unit: passed (exit 0)' in review, 'Missing check verdict')
        self.assertNotIn('PRIVATE_FILE_CONTENT', review)
        self.assertLess(len(review), 1600)
        self.assertEqual(state, original)
        self.assertEqual(decisions, [])
        session.submit('/approve 1')
        session.submit('approve 1')
        self.assertEqual(decisions[0][1], {'approved': True, 'approval_token': 'exact-action'})

    def test_review_keeps_failures_and_unknown_plugin_results_compact(self):
        evidence = [
            {'work_id': 1, 'kind': 'action_completed', 'detail': {'tool': 'plugin.inspect',
             'result': {'nested': {'content': 'PLUGIN_PRIVATE_DATA'*10000}}}},
            {'work_id': 1, 'kind': 'action_failed', 'detail': {'tool': 'run_check',
             'result': {'name': 'lint', 'exit_code': 3, 'output': 'Invalid syntax\n' + 'log'*10000}}},
            {'work_id': 1, 'kind': 'action_failed', 'detail': {'tool': 'plugin.deploy',
             'result': {'error': 'Connection refused\n' + 'detail'*10000}}}]
        session, _, _ = self.session(evidence)
        review = session.submit('/review 1')
        self.assertIn('plugin.inspect', review)
        self.assertTrue('lint: failed (exit 3)' in review, 'Missing failed-check verdict')
        self.assertIn('Invalid syntax', review)
        self.assertIn('Connection refused', review)
        self.assertNotIn('PLUGIN_PRIVATE_DATA', review)
        self.assertLess(len(review), 1800)
