export function beginControlObservation(kind, fromStatus, requestId, now = Date.now(), timeoutMs = 30_000) {
  return { kind, fromStatus: fromStatus ?? null, requestId, deadline: now + timeoutMs, phase: 'pending' };
}

export function observeControl(track, status, now = Date.now()) {
  if (!track) return { track: null, notice: null };
  const late = track.phase === 'unknown';
  const prefix = late ? 'Late observation' : 'Observed';
  if (track.kind === 'pause') {
    if (status === 'PAUSED_USER') return { track: null, notice: `${prefix}: campaign is PAUSED_USER (request ${track.requestId}).` };
    if (['STOPPED', 'GOAL_MET', 'PAUSED_INFRA', 'PAUSED_NEEDS_INPUT'].includes(status || '')) {
      return { track: null, notice: `Pause did not reach PAUSED_USER; current status is ${status} (request ${track.requestId}).` };
    }
  } else if (status && status !== track.fromStatus) {
    return { track: null, notice: `${prefix}: status changed from ${track.fromStatus ?? 'UNKNOWN'} to ${status} after resume request ${track.requestId}; this does not confirm the campaign cycle completed.` };
  }
  if (!late && now >= track.deadline) {
    const unknownTrack = { ...track, phase: 'unknown' };
    return { track: unknownTrack, notice: `UNKNOWN: no resulting campaign status observed for ${track.kind} request ${track.requestId}; still watching for a late status change.` };
  }
  return { track, notice: null };
}
