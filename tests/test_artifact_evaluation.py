from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from bananloop.contracts import EvaluationResult, PairedComparison, BudgetSpec
from bananloop.engine import ControlledInterruption, Engine
from bananloop.policy import decide
from bananloop.report import build_report, render_markdown
from bananloop.research import agent_evaluation_view
from bananloop.store import CampaignStore
from test_engine import make_engine, git


EVALUATOR = '''import hashlib,json,os,random,sys
from pathlib import Path
root=Path(os.environ['BANANLOOP_ARTIFACT_DIR'])
tier=os.environ['BANANLOOP_EVALUATION_TIER']
q=float(Path('value.txt').read_text())
g=float(Path('guard.txt').read_text())
if tier=='validation' and q==1: q=0
cases=[]; artifacts=[]
for i in range(7):
 for rep in range(2):
  key=f'{tier}-private-{i}'
  seed=42000+i*2+rep
  metrics={'quality':q+random.Random(seed).random()/100,'regressions':g}
  path=root/f'{i}-{rep}.bin'; path.write_bytes(bytes([i,rep])+str(metrics).encode())
  cases.append({'case_id':key,'repetition':rep,'input_sha256':hashlib.sha256(str(seed).encode()).hexdigest(),'metrics':metrics})
  artifacts.append({'case_id':key,'repetition':rep,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
print(json.dumps({'metrics':{'quality':sum(c['metrics']['quality'] for c in cases)/14,'regressions':g},'checks':{'tests':True},'cases':cases,'protocol_sha256':hashlib.sha256(tier.encode()).hexdigest(),'artifact_manifest':artifacts,'resource_units':14,'diagnostics':{'approved':1,'secret':9}}))
'''


def setup(tmp_path, *, staged=False, units=140, experiments=3):
    old, _, repo, _ = make_engine(tmp_path)
    spec = old.spec()
    old.close()
    (repo/'evaluator.py').write_text(EVALUATOR)
    git(['add','.'], repo); git(['commit','-m','fixed evaluation'], repo)
    spec.base_commit = git(['rev-parse','HEAD'],repo)
    spec.paired_comparison = PairedComparison(case_count=7, repetitions=2, metric='quality', min_wins=4, worst_case_tolerance=.1,
        guardrails=[{'metric':'regressions','operator':'<=','bound':0}])
    spec.budget = BudgetSpec(max_evaluation_units=units,evaluation_units_per_call=14,max_experiments=experiments,max_attempts=6,max_agent_calls=12,max_evaluator_calls=10)
    spec.agent_visible_diagnostics=['approved']
    spec.agent_hidden_paths=['evaluator.py']
    if staged:
        spec.validation_command=spec.evaluation_command
        spec.holdout_command=spec.evaluation_command
    store=CampaignStore(tmp_path/'new-state'/'campaign.sqlite3'); store.initialize(spec)
    from bananloop.workspace import WorkspaceManager
    engine=Engine(store,WorkspaceManager(repo,tmp_path/'new-state'/'workspace'))
    return store,engine


def test_seven_case_campaign_resume_artifacts_and_rejected_champion(tmp_path,monkeypatch):
    store,engine=setup(tmp_path)
    monkeypatch.setenv('BANANLOOP_TEST_STOP_AFTER_STAGE','DECIDE')
    with pytest.raises(ControlledInterruption): engine.run()
    calls=store.campaign()['evaluator_calls']
    # All expensive work is durable; a fresh engine reuses it after the boundary.
    monkeypatch.delenv('BANANLOOP_TEST_STOP_AFTER_STAGE')
    engine=Engine(store,engine.workspace); engine.run(resume=True)
    exps=store.experiments()
    assert [e['status'] for e in exps]==['ACCEPTED','REJECTED','ACCEPTED']
    assert store.campaign()['champion_snapshot']==exps[2]['candidate_snapshot']
    assert store.campaign()['champion_snapshot']!=exps[1]['candidate_snapshot']
    assert calls==2 and store.campaign()['evaluator_calls']==4
    assert store.evaluation_work_reserved()==56
    for row in store.evaluations():
        result=json.loads(row['result_json'])
        assert len(result['cases'])==14 and len({c['case_id'] for c in result['cases']})==7
        for artifact in result['artifact_manifest']:
            assert artifact['experiment_id']==row['experiment_id']
            assert artifact['candidate_snapshot']==row['candidate_snapshot']
            assert hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()==artifact['sha256']
            assert artifact['metrics']
    report=build_report(store)
    assert len(report['measurements'])==4
    assert 'Input SHA-256' in render_markdown(report)
    assert report['resources']['evaluation_units_reported']==56
    task=json.dumps(engine._task_context('propose',exps[-1]['id']))
    for token in ['private-', 'input_sha256','artifact_manifest','protocol_sha256','secret','resource_units']:
        assert token not in task
    store.close()


def test_baseline_repeat_and_completed_action_replay(tmp_path):
    store,engine=setup(tmp_path,experiments=1)
    engine.workspace.ensure(engine.spec.base_commit)
    engine._baseline()
    first=store.campaign()['baseline_json']
    # Replay after losing only the subsequent baseline stage marker.
    store.update_campaign(baseline_json=None)
    engine._baseline()
    assert store.campaign()['baseline_json']==first
    assert store.campaign()['evaluator_calls']==1
    assert len(store.evaluations())==1
    # Independent fresh execution has identical measured quality and hashes.
    context=dict(engine.evaluator.context)
    context['BANANLOOP_ARTIFACT_DIR']=str(tmp_path/'repeat'); Path(context['BANANLOOP_ARTIFACT_DIR']).mkdir()
    engine.evaluator.context=context
    second=engine.evaluator.evaluate(engine.workspace.workspace,30)
    a=EvaluationResult.model_validate_json(first)
    assert a.cases==second.cases
    assert [x.sha256 for x in a.artifact_manifest]==[x.sha256 for x in second.artifact_manifest]
    store.close()


def test_staged_artifacts_never_enter_research(tmp_path):
    store,engine=setup(tmp_path,staged=True,experiments=1,units=70)
    engine.run()
    assert store.experiments()[0]['status']=='REJECTED'
    assert store.campaign()['champion_snapshot']==engine.spec.base_commit
    assert [x['tier'] for x in store.evaluations()]==['dev','validation','dev','validation','holdout']
    assert store.evaluation_work_reserved()==70
    state=json.dumps(store.research_state())
    assert 'DEV_IMPROVEMENT' in state
    assert 'validation' not in state and 'holdout' not in state
    task=json.dumps(engine._task_context('propose',1))
    assert 'validation-private' not in task and 'holdout-private' not in task
    store.close()


def test_work_budget_reserves_final_and_stops_before_new_attempt(tmp_path):
    store,engine=setup(tmp_path,staged=True,units=42)
    engine.run()
    assert not store.experiments()
    assert store.campaign()['stop_reason']=='BUDGET_EXHAUSTED'
    assert store.campaign()['evaluator_calls']==3
    assert store.evaluation_work_reserved()==42
    store.close()


def test_unknown_work_is_not_refunded(tmp_path):
    store,engine=setup(tmp_path,units=28)
    store.start_action('orphan',None,'BASELINE','evaluator','hash')
    store.restart_action('orphan')
    assert store.evaluation_work_reserved()==28
    assert any(r['status']=='UNKNOWN' for r in store.reservations())
    store.close()


@pytest.mark.parametrize('change',['seed','protocol','missing','duplicate','nan','one_win','worst','guard'])
def test_pairing_fail_closed(tmp_path,change):
    store,engine=setup(tmp_path)
    engine.workspace.ensure(engine.spec.base_commit); engine._baseline()
    base=EvaluationResult.model_validate_json(store.campaign()['baseline_json'])
    payload=base.model_dump(mode='json'); payload['metrics']['quality']['value']+=1
    for c in payload['cases']: c['metrics']['quality']+=1
    if change=='seed': payload['cases'][0]['input_sha256']='f'*64
    elif change=='protocol': payload['protocol_sha256']='f'*64
    elif change=='missing': payload['cases'].pop()
    elif change=='duplicate': payload['cases'].append(payload['cases'][0])
    elif change=='nan': payload['cases'][0]['metrics']['quality']=float('nan')
    elif change=='one_win':
        for i,c in enumerate(payload['cases']): c['metrics']['quality']=base.cases[i].metrics['quality']+(10 if i==0 else 0)
    elif change=='worst': payload['cases'][0]['metrics']['quality']=-1
    elif change=='guard': payload['cases'][0]['metrics']['regressions']=1
    try:
        decision=decide(engine.spec,base,EvaluationResult.model_validate(payload))
        assert not decision.accepted
    except ValueError:
        assert change in {'missing','duplicate','nan'}
    store.close()


def test_artifact_tamper_path_and_unit_overrun(tmp_path):
    from bananloop.artifacts import retain_artifacts
    from bananloop.evaluators.command import EvaluatorError
    store,engine=setup(tmp_path)
    engine.workspace.ensure(engine.spec.base_commit); engine._baseline()
    result=EvaluationResult.model_validate_json(store.campaign()['baseline_json'])
    root=Path(result.artifact_manifest[0].path).parent
    result.artifact_manifest[0].sha256='0'*64
    with pytest.raises(EvaluatorError,match='SHA-256'): retain_artifacts(result,root,None,'x')
    result.artifact_manifest[0].path=str(tmp_path/'outside')
    with pytest.raises(EvaluatorError,match='unique file'): retain_artifacts(result,root,None,'x')
    with pytest.raises(EvaluatorError,match='reserved limit'):
        engine._action(action_id='overrun',experiment_id=None,stage='EVALUATE',kind='evaluator',request={'snapshot':'x'},invoke=lambda:{'metrics':{'quality':0},'resource_units':15})
    assert store.action('overrun')['status']=='FAILED'
    assert store.evaluation_work_reserved()==28
    store.close()


def test_new_fields_absent_from_legacy_hash():
    spec=BudgetSpec()
    assert spec.max_evaluation_units is None
    with pytest.raises(ValidationError): BudgetSpec(max_evaluation_units=14)


def test_unrecognized_payload_private_by_default(tmp_path):
    store,engine=setup(tmp_path)
    payload={'metrics':{'quality':1},'checks':{},'future_private_field':'secret'}
    assert 'secret' not in json.dumps(agent_evaluation_view(engine.spec,json.dumps(payload)))
    store.close()


def test_generic_cli_flags():
    from bananloop.cli import parser
    args=parser().parse_args(['init','--paired-policy','policy.json','--max-evaluation-units','70','--evaluation-units-per-call','14'])
    assert args.paired_policy=='policy.json'
    assert args.max_evaluation_units==70 and args.evaluation_units_per_call==14


def test_completed_replay_fails_on_missing_artifact_without_call(tmp_path):
    from bananloop.evaluators.command import EvaluatorError
    store,engine=setup(tmp_path)
    engine.workspace.ensure(engine.spec.base_commit); engine._baseline()
    result=json.loads(store.campaign()['baseline_json'])
    Path(result['artifact_manifest'][0]['path']).unlink()
    store.update_campaign(baseline_json=None)
    with pytest.raises(Exception,match='unique file'): engine._baseline()
    assert store.campaign()['evaluator_calls']==1
    store.close()
