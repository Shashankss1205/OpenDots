"""Keep the documented real-agent configuration executable and correctly routed."""
import json
from pathlib import Path
import tempfile
import unittest

from opendots.config import load_config
from opendots.engine import Engine
from opendots.sources import normalize_github


class GoalGuideTests(unittest.TestCase):
    def test_heartbeat_owner_and_jsonl_use_same_goal_and_required_check(self):
        template=Path(__file__).resolve().parents[1]/'examples/goal-agent.json'
        raw=json.loads(template.read_text())
        self.assertEqual(raw['backend'],'claude')
        self.assertNotIn('recipes',raw['targets'][0])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';(source/'opendots').mkdir(parents=True)
            (source/'opendots/__main__.py').write_text('print("real syntax check input")\n')
            raw['database']=str(root/'state.db'); raw['targets'][0]['workspace']=str(source)
            raw['sources'][0]['path']=str(root/'events.jsonl'); raw['sandbox']='trusted-local'
            path=root/'config.json';path.write_text(json.dumps(raw));config=load_config(path)
            observed=[]
            class PlanningProbe:
                def plan(self,target,event,state):
                    observed.append((target.objective,event['type']))
                    return {'summary':'Record observed goal','outcome':'complete',
                            'actions':[{'tool':'note','args':{'text':'Observed '+event['type']}}]}
            engine=Engine(config,agent=PlanningProbe())
            self.assertEqual(engine.ingest({'type':'owner.request','source':'local','target_id':'project'})['queued'],1)
            engine.tick_schedules(now=3600); engine.tick_schedules(now=3601); engine.tick_schedules(now=5400)
            (root/'events.jsonl').write_text(json.dumps({'id':'feedback-1','type':'feedback.received','source':'file','target_id':'project'})+'\n')
            engine.sources.poll_due(engine.ingest,now=100); engine.sources.poll_due(engine.ingest,now=106)
            self.assertEqual(engine.snapshot()['event_count'],4)
            final=engine.drain()
            self.assertEqual(final['counts'],{'completed':4})
            self.assertEqual([kind for _,kind in observed].count('timer.heartbeat'),2)
            self.assertTrue(all(goal==config.targets[0].objective for goal,_ in observed))
            for work in final['work']:
                checks=[r['result'] for r in engine.store.work_results(work['id']) if r['tool']=='run_check']
                self.assertEqual(len(checks),1);self.assertEqual(checks[0]['exit_code'],0)

    def test_github_rule_does_not_filter_out_heartbeats(self):
        raw=json.loads((Path(__file__).resolve().parents[1]/'examples/goal-agent.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            raw['database']=str(root/'state.db');raw['targets'][0]['workspace']=str(source)
            raw['targets'][0]['subscriptions'].append({'types':['github.issue.*','github.comment.*','github.pr.*'],
                'sources':['github'],'repos':['Shashankss1205/OpenDots']})
            path=root/'config.json';path.write_text(json.dumps(raw));engine=Engine(load_config(path))
            event=normalize_github('issues',{'action':'opened','issue':{'number':42}},'delivery','Shashankss1205/OpenDots')
            self.assertEqual(engine.ingest(event)['queued'],1)
            wrong=normalize_github('issues',{'action':'opened'},'other-delivery','different/repo')
            self.assertEqual(engine.ingest(wrong)['queued'],0)
            engine.tick_schedules(now=3600)
            self.assertEqual(engine.snapshot()['counts'],{'queued':2})
