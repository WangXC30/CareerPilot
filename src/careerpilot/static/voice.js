/* A capture belongs to one rendered page and one explicit recording consent. */
let voiceCapture = null;
const voicePlaybackUrls = new Set();

function disposeVoiceCapture() {
  const capture = voiceCapture;
  voiceCapture = null;
  if (capture) {
    capture.cancelled = true;
    clearTimeout(capture.timer);
    if (capture.recorder?.state === 'recording') capture.recorder.stop();
    capture.stream?.getTracks().forEach(track => track.stop());
    capture.chunks = [];
  }
  for (const url of voicePlaybackUrls) URL.revokeObjectURL(url);
  voicePlaybackUrls.clear();
}

function voiceButtons() {
  const start = document.querySelector('#voice-start');
  const stop = document.querySelector('#voice-stop');
  const cancel = document.querySelector('#voice-cancel');
  if (start) start.disabled = !!voiceCapture;
  if (stop) stop.disabled = !voiceCapture?.recorder || voiceCapture.saving;
  if (cancel) cancel.disabled = !voiceCapture;
}

async function startVoiceCapture(revision) {
  if (voiceCapture) throw new Error(uiText("r.368"));
  if (!document.querySelector('#voice-consent')?.checked) throw new Error(uiText("r.369"));
  const interviewId = document.querySelector('#voice-interview')?.value;
  if (!interviewId) throw new Error(uiText("r.370"));
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined' ||
      !MediaRecorder.isTypeSupported('audio/webm')) throw new Error(uiText("r.371"));
  const capture = {revision, interviewId, chunks: [], cancelled: false, saving: false};
  voiceCapture = capture;
  voiceButtons();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    if (voiceCapture !== capture || revision !== requestRevision || capture.cancelled) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    capture.stream = stream;
    capture.recorder = new MediaRecorder(stream, {mimeType: 'audio/webm'});
    capture.recorder.ondataavailable = event => {
      if (!capture.cancelled && event.data.size) capture.chunks.push(event.data);
    };
    capture.recorder.onerror = () => {
      if (voiceCapture !== capture) return;
      disposeVoiceCapture(); voiceButtons(); message(uiText("r.372"), true);
    };
    capture.recorder.start(250);
    capture.timer = setTimeout(() => finishVoiceCapture().catch(error => {
      if (revision === requestRevision) message(error.message, true);
    }), 30000);
    toolStatus(uiText("r.373"));
  } catch (error) {
    if (voiceCapture === capture) disposeVoiceCapture();
    if (revision === requestRevision) throw error;
  } finally { voiceButtons(); }
}

async function finishVoiceCapture() {
  const capture = voiceCapture;
  if (!capture?.recorder || capture.saving || capture.recorder.state !== 'recording') return;
  capture.saving = true;
  clearTimeout(capture.timer);
  voiceButtons();
  try {
    await new Promise(resolve => {
      capture.recorder.addEventListener('stop', resolve, {once: true});
      capture.recorder.stop();
    });
    capture.stream.getTracks().forEach(track => track.stop());
    if (voiceCapture !== capture || capture.cancelled || capture.revision !== requestRevision) return;
    const file = new Blob(capture.chunks, {type: 'audio/webm'});
    if (!file.size || file.size > 2 * 1024 * 1024) throw new Error(uiText("r.374"));
    const audio = await toolFileBase64(file);
    if (voiceCapture !== capture || capture.cancelled || capture.revision !== requestRevision) return;
    await api('/voice-records', v1Json('POST', {interview_id: capture.interviewId,
      audio_base64: audio, recording_consent: true, retention_days: 1, confirmed: true}));
    if (capture.revision === requestRevision) {
      disposeVoiceCapture(); if(!await remainingRefresh(capture.revision,'local-tools'))return; toolStatus(uiText("r.375"));
    }
  } finally {
    if (voiceCapture === capture) disposeVoiceCapture();
    voiceButtons();
  }
}

async function playVoiceRecording(button, revision) {
  const response = await fetch('/api/v1/voice-records/' + button.dataset.voicePlay + '/audio', {
    headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'
  });
  if (!response.ok) throw new Error(uiText("r.376"));
  const blob = await response.blob();
  if (revision !== requestRevision) return;
  const url = URL.createObjectURL(blob);
  voicePlaybackUrls.add(url);
  const audio = document.createElement('audio');
  audio.controls = true; audio.src = url; audio.setAttribute('aria-label', uiText("r.377"));
  button.replaceWith(audio);
}

document.addEventListener('change', event => {
  if (event.target.id === 'voice-consent' && !event.target.checked) {
    disposeVoiceCapture(); voiceButtons(); toolStatus(uiText("r.352"));
  }
});
window.addEventListener('pagehide', disposeVoiceCapture);
