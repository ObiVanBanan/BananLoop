import assert from 'node:assert/strict';
import { beginControlObservation, observeControl } from './control-observation.mjs';

const timeoutMs = 100;
const resumeFrom = 'PAUSED_USER';
const resumeTrack = beginControlObservation('resume', resumeFrom, 'resume-fast', 1000, timeoutMs);
// Simulate a fast process transition immediately after launch; the tracked
// starting status must remain the value captured before the launch call.
let snapshotStatus = resumeFrom;
snapshotStatus = 'RUNNING';
const started = observeControl(resumeTrack, snapshotStatus, 1001);
assert.equal(started.track, null);
assert.match(started.notice, /PAUSED_USER to RUNNING/);

let pause = beginControlObservation('pause', 'RUNNING', 'pause-slow', 2000, timeoutMs);
const timedOut = observeControl(pause, 'RUNNING', 2100);
pause = timedOut.track;
assert.equal(pause.phase, 'unknown');
assert.match(timedOut.notice, /^UNKNOWN:/);
const latePause = observeControl(pause, 'PAUSED_USER', 5000);
assert.equal(latePause.track, null);
assert.match(latePause.notice, /^Late observation:/);

let delayedResume = beginControlObservation('resume', 'PAUSED_INFRA', 'resume-late', 3000, timeoutMs);
const unknownResume = observeControl(delayedResume, 'PAUSED_INFRA', 3100);
delayedResume = unknownResume.track;
assert.equal(delayedResume.phase, 'unknown');
assert.match(unknownResume.notice, /^UNKNOWN:/);
const lateResume = observeControl(delayedResume, 'RUNNING', 6000);
assert.equal(lateResume.track, null);
assert.match(lateResume.notice, /^Late observation:/);
assert.match(lateResume.notice, /does not confirm the campaign cycle completed/);

console.log('operator-ui control observation: OK');
