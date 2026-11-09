from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("ui_studio", Path(__file__).with_name("ui_studio.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def plan():
    return {"goal":"Improve retrieval precision with a measured DEV evaluation", "mode":"full_auto",
            "budgets":mod.default_budget(), "request_id":"ui-test-12345678"}


def test_plan_validation_and_limits():
    assert mod.validate_plan(plan())==plan()
    for name, value in (("max_experiments",0),("max_agent_calls",100000),("deadline_seconds",False)):
        p=plan();p["budgets"][name]=value
        with pytest.raises(mod.StudioError):mod.validate_plan(p)
    for v in ("a", {"untrusted":"field"},None):
        p=plan();p["goal"]=v
        with pytest.raises(mod.StudioError):mod.validate_plan(p)
    p=plan();p["mode"]="execute-everything"
    with pytest.raises(mod.StudioError):mod.validate_plan(p)


def test_studio_scaffold_clean_and_non_destructive(tmp_path):
    root=tmp_path/"appdata"/"studio"
    mod.ensure_studio_repo(root)
    assert (root/".git").is_dir()
    assert (root/".bananloop-studio-owner").is_file()
    status=subprocess.run(["git","status","--porcelain"],cwd=root,capture_output=True,text=True,check=True)
    assert status.stdout==""
    assert mod.ensure_studio_repo(root)==root
    unrelated=tmp_path/"another";unrelated.mkdir();(unrelated/"private").write_text("do not touch")
    with pytest.raises(mod.StudioError):mod.ensure_studio_repo(unrelated)
    assert (unrelated/"private").read_text()=="do not touch"


def test_chat_validation_and_draft_invariants():
    v=mod._sanitize_draft({"goal":"Optimize a bounded benchmark", "budgets":{"max_experiments":7,"max_attempts":3,"max_agent_calls":False}, "ready_for_bootstrap":True, "missing":["evaluator"]})
    assert v["budgets"]["max_attempts"]==7
    assert v["budgets"]["max_agent_calls"]==18
    assert v["missing"]==["evaluator"]
    assert mod._sanitize_draft({"goal":"short"}) is None
    with pytest.raises(mod.StudioError):mod._validate_chat_body({"message":"hello","history":[],"shell":"rm"})


class FakeProcess:
    def __init__(self):self.returncode=None
    def poll(self):return self.returncode


def test_launcher_idempotency_and_supervised_prepare(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,"_campaign_id",lambda rid:"c-"+"1"*32)
    root=tmp_path/"studio";root.mkdir();(root/".bananloop-studio-owner").write_text("ok")
    calls=[]
    def spawn(argv,**kwargs):
        p=FakeProcess();calls.append((argv,kwargs,p));return p
    launcher=mod.StudioLauncher(root,process_factory=spawn)
    p=plan();p["mode"]="supervised"
    first=launcher.launch(p)
    assert first["phase"]=="PREPARING"
    assert "--prepare-only" in calls[0][0]
    assert not any("shell" in k for k in calls[0][1])
    assert launcher.launch(p)["request_id"]==p["request_id"]
    assert len(calls)==1
    with pytest.raises(mod.StudioError):launcher.launch({**p,"goal":"A different goal changes the request"})
    with pytest.raises(mod.StudioError):launcher.launch({"request_id":p["request_id"]},confirm=True)
    calls[0][2].returncode=0
    assert launcher.status(p["request_id"])["phase"]=="AWAITING_CONFIRMATION"
    second=launcher.launch({"request_id":p["request_id"]},confirm=True)
    assert second["phase"]=="BOOTSTRAPPING_OR_RUNNING"
    assert "--prepare-only" not in calls[1][0]
    assert len(calls)==2
    calls[1][2].returncode=3
    assert launcher.status(p["request_id"])["phase"]=="NEEDS_INPUT"


def test_chat_run_no_fallback(monkeypatch,tmp_path):
    monkeypatch.setattr(mod.shutil,"which",lambda name:"/usr/bin/node")
    worker=tmp_path/"worker.mjs";worker.write_text("fake worker")
    observed=[]
    def runner(argv,**kwargs):
        observed.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({"reply":"Что будем измерять?","draft":None}),stderr="")
    v=mod.discuss_via_codex({"message":"I want a better retrieval pipeline","history":[]},worker=worker,runner=runner)
    assert v["model_called"] is True
    assert v["reply"].startswith("Что")
    assert observed[0][0]==['/usr/bin/node',str(worker)]
    with pytest.raises(mod.StudioUnavailable):
        mod.discuss_via_codex({"message":"hey","history":[]},worker=worker,runner=lambda *a,**k:SimpleNamespace(returncode=2,stdout="",stderr=""))