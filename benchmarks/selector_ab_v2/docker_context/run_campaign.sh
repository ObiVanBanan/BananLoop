#!/bin/bash
set -u
label="$1"; arm="$2"; repo=/work/repo; state=/work/state
mkdir -p "$repo" "$state"
cp -a /opt/bananloop/examples/calibration/mini_matcher/. "$repo/"
git -C "$repo" init -q
git -C "$repo" add TASK.md README.md evaluator.py matcher.py
git -C "$repo" -c user.name='BananLoop Selector A-B' -c user.email='selector-ab@localhost' commit -qm 'freeze mini matcher fixture'
start=$(date +%s.%N)
python3 -m bananloop.cli init --repo "$repo" --state-dir "$state" --goal 'Read TASK.md and improve analog matching on DEV' --eval-cmd 'python evaluator.py' --metric quality --direction maximize --target 0.99 --min-improvement 0.02 --check cases_valid --editable matcher.py --protect evaluator.py --eval-asset evaluator.py --agent-hide evaluator.py --agent codex --model gpt-6-luna --agent-profiles /opt/agent-profiles.toml --codex-arg=--ignore-user-config --codex-arg=--disable --codex-arg=multi_agent --selector-mode "$arm" --frozen-portfolio /opt/frozen_portfolio.json --replicate-label "$label" --max-experiments 1 --max-attempts 2 --max-agent-calls 2 --max-evaluator-calls 2 --evaluator-timeout-seconds 10 --deadline-seconds 1200 --plateau 1 >/work/init.log 2>&1
init_status=$?
if [ "$init_status" -ne 0 ]; then
  printf '{"seed":"%s","arm":"%s","init_exit":%s,"run_exit":null,"report_exit":null}\n' "$label" "$arm" "$init_status" >/work/execution.json
  cat /work/init.log
  mkdir -p /export; cp -a /work/. /export/; exit "$init_status"
fi
python3 -m bananloop.cli run --repo "$repo" --state-dir "$state" --agent-timeout-seconds 60 >/work/run.log 2>&1
run_status=$?
python3 -m bananloop.cli report --repo "$repo" --state-dir "$state" --output /work/campaign-report.md >/work/report.log 2>&1
report_status=$?
end=$(date +%s.%N)
python3 - "$label" "$arm" "$init_status" "$run_status" "$report_status" "$start" "$end" <<'PY'
import json,sys,platform,subprocess
label,arm,init_status,run_status,report_status,start,end=sys.argv[1:]
try: codex=subprocess.check_output(['codex','--version'],text=True).strip()
except Exception: codex='unavailable'
print(json.dumps({'seed':label,'arm':arm,'init_exit':int(init_status),'run_exit':int(run_status),'report_exit':int(report_status),'duration_seconds':float(end)-float(start),'python_version':platform.python_version(),'platform':platform.platform(),'codex_cli':codex,'campaign_report_file':'campaign-report.md'},separators=(',',':')))
PY
status=$?
tail -n 40 /work/run.log
mkdir -p /export
cp -a /work/. /export/
if [ "$status" -ne 0 ]; then exit "$status"; fi
exit "$run_status"