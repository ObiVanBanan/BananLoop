"""The two local Newton adapters forward the same public DEV inputs."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from benchmarks.autoresearchexam import public_dev_cli, sketched_newton_runner


def test_newton_direct_and_full_forward_same_scoring_inputs(tmp_path, monkeypatch):
    cli = public_dev_cli
    nr = sketched_newton_runner
    task_id = nr.TASK_ID
    instruction = b'public instruction'
    task_toml = b'public task'
    source = tmp_path / task_id
    source.mkdir()
    (source / 'instruction.md').write_bytes(instruction)
    (source / 'task.toml').write_bytes(task_toml)
    workspace = tmp_path / 'workspace'
    candidate = workspace / nr.CANDIDATE_PATH
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b'def estimate(): pass\n')
    monkeypatch.chdir(workspace)
    real_read_text = Path.read_text
    def catalogue_or_file(path, *args, **kwargs):
        if path.name == 'task-catalogue.json':
            return json.dumps({'tasks':[{'task_id':task_id,'adapter':{'candidate_path':nr.CANDIDATE_PATH},
                'instruction_sha256':hashlib.sha256(instruction).hexdigest(),
                'task_sha256':hashlib.sha256(task_toml).hexdigest(),
                'public_dev_image':nr.AREPublicSketchedNewtonEvaluatorRunner.default_dev_image,
                'public_dev_image_id':nr.AREPublicSketchedNewtonEvaluatorRunner.default_dev_image_id}]})
        return real_read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path,'read_text',catalogue_or_file)
    calls=[]; captured={}
    def score(data, **kwargs):
        kwargs.setdefault('docker_binary','docker'); calls.append((data,kwargs))
        return {'status':'GRADED','metric':-0.125,'panel_count':1}
    def init(_self, **kwargs): captured.update(kwargs)
    with patch.object(nr.newton_dev,'verify_public_newton_inputs',return_value={}), \
         patch.object(nr.newton_dev,'run_public_newton_dev',side_effect=score), \
         patch.object(nr.AREPublicEvaluatorRunner,'__init__',init):
        runner=nr.AREPublicSketchedNewtonEvaluatorRunner(source_task_root=source,
            dev_image=nr.AREPublicSketchedNewtonEvaluatorRunner.default_dev_image,
            dev_image_id=nr.AREPublicSketchedNewtonEvaluatorRunner.default_dev_image_id)
        full=captured['evaluate_dev'](candidate.read_bytes(),3600)
        direct=cli.score(task_id=task_id,candidate_path=Path(nr.CANDIDATE_PATH),source_task=source,
            image=runner.default_dev_image,image_id=runner.default_dev_image_id,timeout_seconds=3600)
    assert calls[0] == calls[1]
    assert full['dev_score'] == direct['dev_score']
    assert full['dev_cells'] == direct['dev_cells']
