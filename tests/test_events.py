"""Event visibility and goal relevance before any task action."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import quote
from unittest.mock import patch

from opendots.agents import ClaudeAgent, CodexAgent, validate_relevance
from opendots.config import Config, Target
from opendots.engine import Engine
from opendots.server import make_server
from opendots.terminal import Client, Session


class Assessor:
    def __init__(self, result): self.result=result;self.assessments=0;self.plans=0
    def assess_relevance(self, target, event):
        self.assessments+=1
        if isinstance(self.result,Exception):raise self.result
        return self.result
    def plan(self, target, event, state):
        self.plans+=1
        return {'summary':'Record relevant information','actions':[{'tool':'note','args':{'text':'Observed the event'}}]}


class EventTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir();(self.source/'file.txt').write_text('Real project content')
        self.target=Target('project','Project','Improve checkout reliability',self.source,
            ({'types':['input.*'],'sources':['local','file']},),{'note':'auto'},write_paths=(),
            relevance={'mode':'model','minimum_confidence':0.7})
        self.config=Config((self.target,),self.root/'runtime/state.db',sandbox='trusted-local')
    def tearDown(self):self.temp.cleanup()
    def engine(self,decision='relevant',confidence=0.9):
        self.provider=Assessor({'decision':decision,'confidence':confidence,'reason':'Evidence relates to checkout reliability'})
        return Engine(self.config,agent=self.provider)
    def emit(self,engine,id='delivery/1'):
        return engine.ingest({'id':id,'type':'input.failure','source':'local','payload':{'title':'Checkout failed'}})
    def test_relevant_assessed_once_before_planning_and_deduplicated(self):
        engine=self.engine();self.emit(engine);self.assertTrue(self.emit(engine)['duplicate'])
        self.assertEqual(self.provider.assessments,0)
        self.assertEqual(engine.store.event_detail('delivery/1')['decisions'][0]['status'],'pending')
        engine.drain();self.assertEqual(self.provider.assessments,1);self.assertEqual(self.provider.plans,1)
        d=engine.store.event_detail('delivery/1')['decisions'][0]
        self.assertEqual(d['status'],'relevant');self.assertEqual(d['confidence'],0.9)
        self.assertEqual(d['work_status'],'completed')
        self.assertEqual(engine.snapshot()['metrics']['planning_calls_today'][0]['calls'],2)
    def test_irrelevant_and_uncertain_do_not_prepare_or_plan(self):
        for i,(decision,confidence,status) in enumerate([('irrelevant',.95,'ignored'),('relevant',.3,'blocked'),('uncertain',.9,'blocked')]):
            with self.subTest(decision=decision,confidence=confidence):
                engine=self.engine(decision,confidence);self.emit(engine,str(i))
                with patch.object(engine.workspaces,'prepare',side_effect=AssertionError('Must not create workspace')):
                    engine.drain()
                self.assertEqual(engine.store.event_detail(str(i))['decisions'][0]['work_status'],status)
                self.assertEqual(self.provider.plans,0)
    def test_invalid_result_provider_failure_and_budget_stop_actions(self):
        cases=[{'decision':'relevant','confidence':True,'reason':'bad'},RuntimeError('Provider unavailable')]
        for i,result in enumerate(cases):
            engine=self.engine();self.provider.result=result;self.emit(engine,'error'+str(i));engine.drain()
            d=engine.store.event_detail('error'+str(i))['decisions'][0]
            self.assertEqual(d['status'],'error');self.assertIsNone(d['confidence']);self.assertEqual(self.provider.plans,0)
        engine=self.engine();engine.store.reserve_model_call('project',100,1000)
        engine.config=replace(engine.config,max_model_calls_per_day=1)
        self.emit(engine,'budget');engine.drain()
        self.assertEqual(engine.store.event_detail('budget')['decisions'][0]['status'],'error')
        self.assertEqual(self.provider.assessments,0)
    def test_filter_reason_and_priority_are_not_model_confidence(self):
        engine=self.engine()
        engine.ingest({'id':'unmatched','type':'other.event','payload':{}})
        engine.ingest({'id':'quiet','type':'input.changed','priority':0,'payload':{}})
        engine.drain();self.assertEqual(self.provider.assessments,0)
        events=engine.store.events()['events'];self.assertEqual(len(events),2)
        for event in events:
            d=event['decisions'][0];self.assertEqual(d['status'],'filtered');self.assertIsNone(d['confidence'])
        self.assertIn('Priority',events[0]['decisions'][0]['reason'])
    def test_history_cursor_survives_restart_and_includes_unmatched(self):
        engine=self.engine()
        for i in range(5):engine.ingest({'id':str(i),'type':'unmatched','payload':{'title':'message'}})
        first=engine.store.events(limit=2);self.assertEqual([x['id'] for x in first['events']],['4','3'])
        reopened=Engine(self.config,agent=self.provider)
        next_page=reopened.store.events(first['next_before'],limit=2)
        self.assertEqual([x['id'] for x in next_page['events']],['2','1'])
        self.assertEqual(reopened.store.events(query='unmatched',target='project')['events'][0]['decisions'][0]['status'],'filtered')
    def test_jsonl_producer_subscription_relevance_and_cursor(self):
        inbox=self.root/'inbox.jsonl';inbox.write_text(json.dumps({'id':'from-file','type':'input.failure','payload':{'title':'Checkout error'}})+'\n')
        self.config=replace(self.config,sources=({'id':'inbox','kind':'jsonl','path':str(inbox),'interval_seconds':1},))
        engine=self.engine();engine.sources.poll_due(engine.ingest,now=10);engine.drain()
        self.assertEqual(engine.store.event_detail('from-file')['event']['source'],'file')
        engine.sources.poll_due(engine.ingest,now=12);self.assertEqual(engine.store.events()['events'][0]['id'],'from-file')
        self.assertEqual(len(engine.store.events()['events']),1)
        listeners=engine.listeners();self.assertEqual(listeners['sources'][0]['health']['last_event_count'],0)
    def test_http_and_tui_expose_event_payload_reasons_and_listeners(self):
        engine=self.engine();server=make_server(engine,port=0)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            client=Client('http://127.0.0.1:'+str(server.server_address[1]));session=Session(client);session.refresh()
            answer=session.submit('/emit '+json.dumps({'id':'with/slash','type':'input.failure','payload':{'title':'Actual error'}}))
            self.assertIn('with/slash',answer)
            self.assertIn('pending',session.submit('/events'))
            self.assertIn('Actual error',session.submit('/event with/slash'))
            self.assertIn('input.*',session.submit('/listeners'))
            engine.drain();self.assertIn('90%',session.submit('/events'))
            self.assertIn('HTTP',session.submit('/connect'))
            with self.assertRaises(ValueError):client.request('/api/events?limit=0')
            with self.assertRaises(ValueError):session.submit('/emit []')
        finally:server.shutdown();server.server_close();thread.join()
    def test_provider_assessment_is_schema_bounded_and_no_project_context(self):
        for provider in (ClaudeAgent(),CodexAgent()):
            result={'decision':'uncertain','confidence':.6,'reason':'More information needed'}
            def respond(target,prompt,schema):
                self.assertNotEqual(target.workspace,self.source)
                self.assertNotIn('workspace_snapshot',prompt)
                self.assertIn('untrusted',prompt)
                self.assertEqual(schema['properties']['confidence']['maximum'],1)
                return result
            with patch.object(provider,'respond',side_effect=respond):
                self.assertEqual(provider.assess_relevance(self.target,{'type':'input.changed'}),result)
        for score in (True,float('nan'),float('inf'),-1,1.1):
            with self.assertRaises(ValueError):validate_relevance({'decision':'relevant','confidence':score,'reason':'Reason'})
    def test_disabled_mode_is_explicit_and_has_no_fake_score(self):
        self.config=replace(self.config,targets=(replace(self.target,relevance={'mode':'off'}),))
        engine=self.engine();self.emit(engine);engine.drain()
        d=engine.store.event_detail('delivery/1')['decisions'][0]
        self.assertEqual(d['status'],'disabled');self.assertIsNone(d['confidence']);self.assertEqual(self.provider.assessments,0)

    def test_approval_resume_reuses_assessment(self):
        self.config=replace(self.config,targets=(replace(self.target,policy={'note':'approval'}),))
        engine=self.engine();self.emit(engine);engine.drain()
        work=engine.snapshot()['work'][0]
        self.assertEqual(work['status'],'waiting_approval')
        engine.store.decide(work['id'],True,work['approval_token']);engine.drain()
        self.assertEqual(self.provider.assessments,1)
        self.assertEqual(engine.snapshot()['work'][0]['status'],'completed')

    def test_one_event_assesses_each_goal_independently(self):
        targets=[self.target]
        for i in range(1,4):
            source=self.root/('source'+str(i));source.mkdir();(source/'file').write_text('content')
            targets.append(replace(self.target,id='p'+str(i),workspace=source,objective='Goal '+str(i)))
        self.config=replace(self.config,targets=tuple(targets))
        engine=self.engine()
        def assess(target,event):
            return {'decision':'relevant' if target.id=='project' else 'irrelevant', 'confidence':.9, 'reason':target.objective}
        self.provider.assess_relevance=assess
        self.emit(engine);engine.drain()
        decisions=engine.store.event_detail('delivery/1')['decisions']
        self.assertEqual(len(decisions),4)
        self.assertEqual(sum(d['work_status']=='completed' for d in decisions),1)
        self.assertEqual(sum(d['work_status']=='ignored' for d in decisions),3)
