"""Focused independent read-model contract smoke without importing BananLoop Engine."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
from bananloop import ui_read_models as mod


def test_legacy_hypotheses_are_unconnected_and_not_automatically_verified():
    v=mod._v1_view({'campaign_id':'legacy','goal':'goal','revision':9,'status':'RUNNING',
                     'hypotheses':[{'ordinal':1,'title':'A','status':'COMPLETED'},
                                   {'ordinal':2,'title':'B','status':'FAILED'}]})
    assert v['source_schema']==1
    assert v['edges']==[] and v['groups']==[]
    assert v['nodes'][0]['status']=='COMPLETED'
    assert not v['capabilities']['engine_writes']


def test_v2_projection_edges_and_groups_are_safe():
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE campaigns(id INTEGER PRIMARY KEY,status TEXT);
    INSERT INTO campaigns VALUES(1,'RUNNING');
    CREATE TABLE research_edges(src_id TEXT,dst_id TEXT,edge_type TEXT,constraint_json TEXT);
    INSERT INTO research_edges VALUES('a','b','REQUIRES','SECRET-MUST-NOT-LEAK');
    CREATE TABLE graph_groups(group_id TEXT,owner_node_id TEXT,kind TEXT,readiness_policy TEXT);
    INSERT INTO graph_groups VALUES('group1','b','OR','verified_completion');
    CREATE TABLE graph_group_members(group_id TEXT,node_id TEXT,position INTEGER);
    INSERT INTO graph_group_members VALUES('group1','a',0);''')
    class Graph:
        conn=db
        def scheduling_snapshot(self):
            return {'graph_revision':8,'nodes':[{'node_id':'a','phase':'EXPLORATION','status':'COMPLETED','ready':False,'blockers':[], 'revision':2,'payload_json':'SECRET'},
                                                 {'node_id':'b','phase':'OPTIMIZATION','status':'PENDING','ready':False,'blockers':['or:group1'], 'revision':0,'payload_json':'SECRET'}]}
        def graph_revision(self):return 8
        def campaign_spec(self):return SimpleNamespace(goal='Public goal')
    class Ctx:
        def __enter__(self):return Graph(),'c-123'
        def __exit__(self,*args):pass
    data=mod._v2_view(SimpleNamespace(open_graph_store=lambda:Ctx()))
    assert data['nodes'][0]['status']=='COMPLETED'
    assert 'payload_json' not in str(data)
    assert 'SECRET' not in str(data)
    assert data['groups']==[{'id':'group1','owner':'b','kind':'OR','members':['a']}]
    assert data['edges']==[{'source':'a','target':'b','type':'REQUIRES'}]
    assert data['revision']==8
    db.close()


def test_codex_status_strictly_distinguishes_subscription_and_apikey():
    with patch.object(mod.shutil,'which',return_value='/usr/bin/codex'):
        with patch.object(mod.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Logged in using ChatGPT',stderr='')) as run:
            state=mod.codex_connection_status()
            assert state['chatgpt_subscription'] and state['auth_mode']=='chatgpt'
            assert run.call_args.args[0]==['/usr/bin/codex','login','status']
        with patch.object(mod.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Logged in using API key',stderr='')):
            state=mod.codex_connection_status()
            assert not state['chatgpt_subscription'] and state['auth_mode']=='api_key'
        with patch.object(mod.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='mysterious',stderr='')):
            state=mod.codex_connection_status()
            assert not state['chatgpt_subscription'] and state['auth_mode']=='unknown'
    with patch.object(mod.shutil,'which',return_value=None):
        assert not mod.codex_connection_status()['installed']


def test_campaign_id_rejects_path_injection():
    try:mod.campaign_ui_view('.', '../../etc/passwd')
    except ValueError:pass
    else:raise AssertionError('path traversal accepted')