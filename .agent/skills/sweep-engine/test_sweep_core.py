"""Unit tests for sweep_core. Run from this folder: python3 -m unittest test_sweep_core"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweep_core as c  # noqa: E402

ME = 'UME'
CFG = c.load_filter(None)
CFG['me'] = {'ids': [ME], 'emails': ['me@example.com'], 'names': ['Dita']}

def msg(key, actor, text, ago_h, **kw):
    return {'source': kw.pop('source', 'slack'), 'conversation_key': key, 'actor': actor,
            'actor_id': kw.pop('aid', actor), 'ts': time.time() - ago_h * 3600, 'text': text,
            'why_me': kw.pop('why', 'thread_participant'), **kw}

def verdict(items, cfg=CFG):
    convs = c.collapse(items, cfg)
    return {k: c.evaluate(v, cfg, None) for k, v in convs.items()}

class Rules(unittest.TestCase):
    def test_a_thread_collapses_into_one_item(self):
        v = verdict([msg('t1', 'A', 'can you check this?', 3), msg('t1', 'B', 'also the date?', 2)])
        self.assertEqual(list(v), ['t1'])
        self.assertEqual(v['t1'][0], 'candidate')

    def test_a_reply_by_the_user_closes_the_thread(self):
        v = verdict([msg('t1', 'A', 'can you check this?', 3), msg('t1', 'Me', 'done, pushed it', 1, aid=ME)])
        self.assertEqual(v['t1'][0], 'closed')

    def test_a_deferring_reply_is_a_promise_not_a_close(self):
        v = verdict([msg('t1', 'A', 'can you check this?', 3), msg('t1', 'Me', 'will check tomorrow', 1, aid=ME)])
        self.assertEqual(v['t1'][0], 'promised')

    def test_a_reply_tagging_one_person_leaves_the_others_question_open(self):
        v = verdict([msg('t1', 'UA', 'when do we launch?', 4, aid='UA'),
                     msg('t1', 'UB', 'can you share the doc?', 3, aid='UB'),
                     msg('t1', 'Me', '<@UA> next monday', 1, aid=ME)])
        self.assertEqual(v['t1'][0], 'candidate')
        self.assertIn('partial', v['t1'][1])

    def test_an_untagged_reply_answers_the_whole_thread(self):
        v = verdict([msg('t1', 'UA', 'when do we launch?', 4, aid='UA'),
                     msg('t1', 'UB', 'can you share the doc?', 3, aid='UB'),
                     msg('t1', 'Me', 'monday, doc is in the channel', 1, aid=ME)])
        self.assertEqual(v['t1'][0], 'closed')

    def test_a_new_ask_after_the_users_reply_reopens_it(self):
        v = verdict([msg('t1', 'Me', 'here it is', 3, aid=ME), msg('t1', 'A', 'and the price?', 1)])
        self.assertEqual(v['t1'][0], 'candidate')

    def test_a_thank_you_after_the_users_reply_is_noise(self):
        v = verdict([msg('t1', 'Me', 'here it is', 3, aid=ME), msg('t1', 'A', 'thanks a lot!', 1)])
        self.assertEqual(v['t1'], ('noise', 'ack'))

    def test_a_thank_you_with_an_ask_is_kept(self):
        v = verdict([msg('t1', 'A', 'thanks, can you also send the deck?', 1)])
        self.assertEqual(v['t1'][0], 'candidate')

    def test_a_reply_addressed_to_someone_else_is_noise(self):
        v = verdict([msg('t1', 'A', '<@UOTHER> please retest this build', 1)])
        self.assertEqual(v['t1'], ('noise', 'addressed_to_other'))

    def test_a_reply_that_tags_the_user_is_kept(self):
        v = verdict([msg('t1', 'A', f'<@UOTHER> <@{ME}> please retest this build', 1)])
        self.assertEqual(v['t1'][0], 'candidate')

    def test_a_quiet_thread_with_no_question_goes_stale(self):
        v = verdict([msg('t1', 'A', 'pushed the build to staging', 24 * 6)])
        self.assertEqual(v['t1'][0], 'noise')
        self.assertTrue(v['t1'][1].startswith('stale'))

    def test_an_unanswered_question_is_never_binned_as_stale(self):
        v = verdict([msg('t1', 'A', 'what about the API?', 24 * 6)])
        self.assertEqual(v['t1'][0], 'candidate')
        v = verdict([msg('t1', 'A', 'what about the API?', 24 * 9)])
        self.assertEqual(v['t1'][0], 'aging')

    def test_anything_three_weeks_unanswered_expires_with_a_reason(self):
        v = verdict([msg('d1', 'A', 'what about the API?', 24 * 22, why='dm')])
        self.assertEqual(v['d1'][0], 'noise')
        self.assertTrue(v['d1'][1].startswith('expired'))

    def test_an_old_dm_goes_to_the_older_list_not_the_bin(self):
        v = verdict([msg('d1', 'A', 'what about the API?', 24 * 10, why='dm')])
        self.assertEqual(v['d1'][0], 'aging')

    def test_a_notification_mail_joins_its_ticket(self):
        items = [msg('jira:AB-1', 'A', 'please confirm the CR', 2, why='mention', source='jira'),
                 {**msg('gmail:x', 'jira@x.atlassian.net', 'A mentioned you on AB-1', 2, why='mention'),
                  'collapse_into': 'jira:AB-1'}]
        self.assertEqual(list(verdict(items)), ['jira:AB-1'])

    def test_a_doc_named_by_title_and_by_id_is_one_item(self):
        items = [msg('gdoc:prd x', 'A', 'commented', 2, why='doc_comment'),
                 {**msg('gdoc:id:abc', 'A', 'commented', 1, why='doc_comment'), 'aliases': ['gdoc:prd x']}]
        self.assertEqual(list(verdict(items)), ['gdoc:id:abc'])

    def test_marketing_bulk_mail_is_noise_and_says_why(self):
        v = verdict([{**msg('g1', 'Figma', 'Join our session', 2, why='dm'),
                      'actor_id': 'community@figma.com', 'meta': {'list_unsubscribe': True}}])
        self.assertEqual(v['g1'][0], 'noise')
        self.assertIn('community@', v['g1'][1])

    def test_a_person_at_a_marketing_address_writing_directly_is_kept(self):
        v = verdict([{**msg('g1', 'Sara', 'can we meet thursday about the campaign?', 2, why='dm'),
                      'actor_id': 'marketing@client.com'}])
        self.assertEqual(v['g1'][0], 'candidate')

    def test_a_signature_request_survives_the_updates_tab(self):
        v = verdict([{**msg('g1', 'DocuSign', 'Please sign the NDA', 2, why='signature_request'),
                      'actor_id': 'dse@docusign.net', 'meta': {'category': 'updates', 'notification': True}}])
        self.assertEqual(v['g1'][0], 'candidate')

    def test_covered_by_source_rules_need_that_source_to_have_run(self):
        mail = {**msg('jira:AB-2', 'Jira', 'status changed', 1, why='watcher', source='jira'),
                'actor_id': 'jira@x.atlassian.net', 'meta': {'notification': True}}
        ok = dict(CFG, _healthy={'jira'})
        down = dict(CFG, _healthy={'gmail'})
        self.assertEqual(verdict([dict(mail)], ok)['jira:AB-2'][0], 'noise')
        self.assertEqual(verdict([dict(mail)], down)['jira:AB-2'][0], 'candidate')

    def test_the_allow_list_beats_the_deny_list(self):
        cfg = dict(CFG, allow_senders=['billing@vendor.com'])
        conv = c.collapse([{**msg('g1', 'Vendor', 'invoice overdue, pay today?', 2, why='dm'),
                            'actor_id': 'billing@vendor.com', 'meta': {'list_unsubscribe': True}}], cfg)
        self.assertEqual(c.evaluate(conv['g1'], cfg, None)[0], 'candidate')

class State(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.items = os.path.join(self.d, 'i.jsonl')
        self.state = os.path.join(self.d, 's.json')
        self.out = os.path.join(self.d, 'o')
        self.health = os.path.join(self.d, 'h.json')

    def call(self, *argv):
        sys.argv = ['x', *argv]
        with contextlib.redirect_stdout(io.StringIO()):
            c.main()

    def run_sweep(self, rows, health=None):
        with open(self.items, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(json.dumps(r) for r in rows))
        argv = ['run', '--items', self.items, '--state', self.state, '--out', self.out]
        if health is not None:
            with open(self.health, 'w', encoding='utf-8') as fh:
                json.dump(health, fh)
            argv += ['--health', self.health]
        self.call(*argv)
        with open(self.state, encoding='utf-8') as fh:
            return json.load(fh)['conversations']

    def report(self):
        with open(os.path.join(self.out, 'report.md'), encoding='utf-8') as fh:
            return fh.read()

    def test_dismiss_holds_until_something_new_arrives(self):
        first = [msg('t1', 'A', 'can you review?', 2)]
        self.assertEqual(self.run_sweep(first)['t1']['status'], 'open')
        self.call('dismiss', '--state', self.state, '--key', 't1')
        self.assertEqual(self.run_sweep(first)['t1']['status'], 'dismissed')
        self.assertEqual(self.run_sweep(first + [msg('t1', 'A', 'and the second file?', 0.5)])['t1']['status'], 'open')

    def test_an_item_the_window_no_longer_covers_is_carried_not_lost(self):
        self.run_sweep([msg('g1', 'A', 'can you approve the PO?', 30, why='dm', source='gmail')])
        health = [{'adapter': 'gmail', 'source': 'gmail', 'status': 'ok', 'coverage_days': 1, 'complete': True}]
        row = self.run_sweep([], health)['g1']
        self.assertEqual(row['status'], 'open')
        self.assertTrue(row['carried'])

    def test_an_item_a_clean_source_stops_reporting_closes(self):
        self.run_sweep([msg('g1', 'A', 'can you approve the PO?', 2, why='dm', source='gmail')])
        health = [{'adapter': 'gmail', 'source': 'gmail', 'status': 'ok', 'coverage_days': 3, 'complete': True}]
        self.assertEqual(self.run_sweep([], health)['g1']['status'], 'closed')

    def test_a_failed_source_never_closes_its_items(self):
        self.run_sweep([msg('g1', 'A', 'can you approve the PO?', 2, why='dm', source='gmail')])
        health = [{'adapter': 'gmail', 'source': 'gmail', 'status': 'failed', 'coverage_days': 3}]
        self.assertEqual(self.run_sweep([], health)['g1']['status'], 'open')

    def test_the_second_run_shows_only_what_changed(self):
        rows = [msg('t1', 'A', 'can you review?', 2)]
        self.run_sweep(rows)
        self.run_sweep(rows)
        self.assertIn('1 still open, nothing new', self.report())
        self.assertIn('**0 new or changed', self.report())

    def test_the_top_list_is_capped(self):
        self.run_sweep([msg(f't{i}', f'P{i}', 'can you review?', 2) for i in range(12)])
        rep = self.report()
        self.assertIn('More (5)', rep)

    def test_several_items_from_one_person_share_a_line(self):
        self.run_sweep([msg('t1', 'Lamya', 'can you review A?', 2), msg('t2', 'Lamya', 'and B?', 1)])
        self.assertIn('- **Lamya** (2):', self.report())

    def test_model_noise_on_a_dm_is_kept_as_fyi(self):
        self.run_sweep([msg('d1', 'A', 'status: done with the migration', 2, why='dm')])
        labels = os.path.join(self.d, 'l.json')
        with open(labels, 'w') as fh:
            json.dump({'d1': {'label': 'noise', 'reason': 'status only'}}, fh)
        self.call('label', '--state', self.state, '--labels', labels, '--out', self.out)
        with open(self.state) as fh:
            self.assertEqual(json.load(fh)['conversations']['d1']['label'], 'fyi')

    def test_an_item_the_labeller_skipped_stays_in_needs_you(self):
        self.run_sweep([msg('t1', 'A', 'can you review the doc?', 2),
                        msg('t2', 'Teammate', 'accepted it, let me know if still blocking', 1)])
        labels = os.path.join(self.d, 'l.json')
        with open(labels, 'w') as fh:
            json.dump({'t1': {'label': 'needs_reply', 'reason': 'A asks for a review'}}, fh)
        self.call('label', '--state', self.state, '--labels', labels, '--out', self.out)
        rep = self.report()
        needs = rep.split('## Needs you', 1)[1].split('<details>', 1)[0]
        self.assertIn('accepted it', needs)
        self.assertIn('skipped 1 item', needs)

    def test_a_cc_only_item_cannot_be_marked_as_needing_you(self):
        self.run_sweep([msg('g1', 'A', 'team, please send your numbers?', 2, why='cc', source='gmail')])
        labels = os.path.join(self.d, 'l.json')
        with open(labels, 'w') as fh:
            json.dump({'g1': {'label': 'needs_reply', 'reason': 'asks for numbers'}}, fh)
        self.call('label', '--state', self.state, '--labels', labels, '--out', self.out)
        with open(self.state) as fh:
            self.assertEqual(json.load(fh)['conversations']['g1']['label'], 'fyi')

    def test_repeated_dismissals_suggest_a_mute(self):
        for i in range(3):
            self.run_sweep([msg(f'n{i}', 'Spammy', 'check out our offer', 2, aid='spam@vendor.com', why='dm')])
            self.call('dismiss', '--state', self.state, '--key', f'n{i}')
        self.run_sweep([msg('t9', 'A', 'can you review?', 1)])
        self.assertIn('Suggested mutes', self.report())
        self.assertIn('spam@vendor.com', self.report())

if __name__ == '__main__':
    unittest.main()
