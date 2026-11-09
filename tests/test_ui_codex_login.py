"""Tests for the isolated, official Codex app-server login ceremony (no real network)."""
from __future__ import annotations

import os
import stat
import tempfile
import textwrap
import time
from pathlib import Path

from bananloop.ui_codex_login import CodexDeviceLogin, LoginUnavailable


def fake_binary(root: Path, *, invalid_url: bool = False, auto_complete: bool = False) -> str:
    binary = root / "fake-codex"
    binary.write_text(textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import json,sys,time,threading
        assert sys.argv[1:] == ['app-server']
        def send(o):
            sys.stdout.write(json.dumps(o) + '\\n')
            sys.stdout.flush()
        for line in sys.stdin:
            msg=json.loads(line)
            if 'id' not in msg: continue
            if msg['method']=='initialize':
                send({{'id':msg['id'],'result':{{'userAgent':'fake'}}}})
            elif msg['method']=='account/login/start':
                assert msg['params']=={{'type':'chatgptDeviceCode'}}
                send({{'id':msg['id'],'result':{{'type':'chatgptDeviceCode',
                    'loginId':'test-123','verificationUrl':{'"https://evil.example/device"' if invalid_url else '"https://auth.openai.com/codex/device"'},
                    'userCode':'ABCD-1234'}}}})
                if {auto_complete}:
                    time.sleep(.1)
                    send({{'method':'account/login/completed','params':{{'loginId':'test-123','success':True,'error':None}}}})
            elif msg['method']=='account/login/cancel':
                assert msg['params']=={{'loginId':'test-123'}}
                send({{'id':msg['id'],'result':{{}}}})
            else: raise AssertionError(msg['method'])
    '''))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return str(binary)


def test_device_login_and_completion():
    with tempfile.TemporaryDirectory() as tmp:
        login = CodexDeviceLogin(codex_path=fake_binary(Path(tmp), auto_complete=True), timeout=3)
        try:
            state = login.start()
            assert state['phase'] in ('pending','completed')
            if state['phase']=='pending':
                assert state['verification_url'] == 'https://auth.openai.com/codex/device'
                assert state['user_code'] == 'ABCD-1234'
            for _ in range(80):
                if login.status()['phase']=='completed': break
                time.sleep(.02)
            assert login.status()=={'phase':'completed','verification_url':None,'user_code':None}
        finally: login.close()


def test_device_login_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        login = CodexDeviceLogin(codex_path=fake_binary(Path(tmp)), timeout=3)
        try:
            assert login.start()['phase']=='pending'
            assert login.cancel()['phase']=='cancelled'
            assert login.status()['user_code'] is None
        finally: login.close()


def test_device_login_rejects_untrusted_url():
    with tempfile.TemporaryDirectory() as tmp:
        login = CodexDeviceLogin(codex_path=fake_binary(Path(tmp),invalid_url=True), timeout=3)
        try:
            try: login.start()
            except LoginUnavailable: pass
            else: raise AssertionError('Untrusted verification URL accepted')
            assert login.status()['phase']=='failed'
        finally: login.close()


def test_missing_binary():
    login = CodexDeviceLogin(codex_path='')
    try:
        try: login.start()
        except LoginUnavailable: pass
        else: raise AssertionError('No binary should not start')
    finally: login.close()