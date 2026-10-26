/* Private history and explicit GitHub OAuth; credentials stay on the server. */
let githubPending = null;
let timelineCursor = null;
let githubAttempt=0;
function lockGithubControls(){const controls=[...document.querySelectorAll('#github-start,#github-sync,#github-unbind')].map(button=>[button,button.disabled]);controls.forEach(([button])=>button.disabled=true);return ()=>controls.forEach(([button,disabled])=>{if(button.isConnected)button.disabled=disabled;});}
function clearGithubPending(){githubAttempt++;githubPending?.popup?.close();githubPending=null;}

async function enhanceProfileHistory(revision) {
  clearGithubPending();
  const state = await api('/github-binding');
  if (revision !== requestRevision) return;
  const binding = state.binding;
  view.insertAdjacentHTML('beforeend', `<article class="card growth-detail" id="github-binding" lang="${uiLocale}">
    <h2>${uiText("ph.title")}</h2><p>${projectFixed(state.scope)}</p><p>${projectFixed(state.notice)}</p>
    ${binding ? `<p>${uiText("ph.bound")}：${uiOriginal(binding.login)} · ${uiText("ph.lastSync")}：${binding.synced_at?projectMoment(binding.synced_at):uiText("ph.never")}</p>
      <button id="github-sync">${uiText("ph.sync")}</button><ul>${binding.repositories.map(repo => `<li><a href="${textValue(repo.url)}" target="_blank" rel="noopener noreferrer">${uiOriginal(repo.name)}</a></li>`).join('')}</ul>
      ${binding.possibly_truncated ? `<p>${uiText("ph.truncated")}</p>` : ''}` :
      `<button id="github-start" ${state.configured ? '' : 'disabled'}>${uiText("ph.start")}</button>`}
    ${state.configured ? '' : `<p>${uiText("ph.notConfigured")}</p>`}
    <details><summary>${uiText("ph.cancel")}</summary><p>${uiText("ph.cancelHelp")}</p>
      <button id="github-unbind" class="secondary">${uiText("ph.unbind")}</button>
      <a href="https://github.com/settings/applications" target="_blank" rel="noopener noreferrer">${uiText("ph.providerSettings")}</a></details>
    <p id="github-status" role="status"></p></article>
    <article class="card growth-detail" id="profile-timeline" lang="${uiLocale}"><h2>${uiText("ph.timeline")}</h2>
    <p>${uiText("ph.timelineHelp")}</p>
    <div id="timeline-events"></div><button id="timeline-more" class="secondary">${uiText("ph.viewTimeline")}</button></article>`);
  timelineCursor = null;
  uiFormFields(document.querySelector("#github-binding"));uiFormFields(document.querySelector("#profile-timeline"));
  await renderAccountSessions(revision);
}

function timelineCard(item) {
  const labels = {profile:uiText("ph.kind.profile"), plan:uiText("ph.kind.plan"), goal_history:uiText("ph.kind.goal_history"), goal_event:uiText("ph.kind.goal_event"), evidence:uiText("ph.kind.evidence"), revocation:uiText("ph.kind.revocation")};
  return `<details class="timeline-event"><summary>${textValue(labels[item.kind] || item.kind)} · ${projectMoment(item.at)} · v${item.version}</summary>
    <p>${uiOriginal(item.reason)}${item.revoked ? uiText("ph.revoked") : ''}</p>
    <p>${uiText("ph.source")}：${uiOriginal(item.goal_id || item.source_id)}</p>
    ${item.details ? `<pre>${uiOriginal(JSON.stringify(item.details, null, 2))}</pre>` : ''}
    ${item.changes ? Object.entries(item.changes).map(([field, change]) => `<details><summary>${uiOriginal(field)}</summary><p>${uiText("ph.before")}</p><pre>${uiOriginal(JSON.stringify(change.before, null, 2))}</pre><p>${uiText("ph.after")}</p><pre>${uiOriginal(JSON.stringify(change.after, null, 2))}</pre></details>`).join('') : ''}</details>`;
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || !['github-start','github-sync','github-unbind','timeline-more'].includes(button.id)) return;
  const revision = requestRevision, session = token,attempt=button.id==='timeline-more'?githubAttempt:++githubAttempt;
  const unlock=button.id==='timeline-more'?()=>{}:lockGithubControls();
  button.disabled = true;
  let popup;
  try {
    if (button.id === 'timeline-more') {
      const result = await api('/profile-timeline' + (timelineCursor ? '?cursor=' + encodeURIComponent(JSON.stringify(timelineCursor)) : ''));
      if (revision !== requestRevision) return;
      document.querySelector('#timeline-events').insertAdjacentHTML('beforeend', result.events.map(timelineCard).join('') || `<p>${uiText("ph.noTimeline")}</p>`);
      timelineCursor = result.next;
      button.hidden = !timelineCursor;
      button.textContent = uiText("ph.earlier");
      document.querySelector("#timeline-events .timeline-event:last-child summary")?.focus();
    } else if (button.id === 'github-start') {
      if (githubPending?.popup && !githubPending.popup.closed) githubPending.popup.close();
      popup = window.open('about:blank', '_blank', 'popup,width=700,height=800');
      if (!popup) throw new Error(uiText("ph.popupBlocked"));
      const result = await api('/github-binding/start', v1Json('POST', {confirmed:true}));
      if (revision !== requestRevision || token !== session || attempt!==githubAttempt) {popup.close(); return;}
      githubPending = {popup, state:result.state, session, revision,attempt};
      popup.location = result.authorization_url;
      document.querySelector('#github-status').textContent = uiText("ph.wait");
    } else {
      githubPending?.popup?.close();githubPending = null;
      const result = await api('/github-binding/' + (button.id === 'github-sync' ? 'sync' : 'unbind'), v1Json('POST', {confirmed:true}));
      if (revision !== requestRevision || token!==session || attempt!==githubAttempt) return;
      await render();
      if(requestRevision!==revision+1||token!==session)return;
      message(button.id === 'github-sync' ? uiText("ph.synced") : uiLocale==='en-US'?projectExplanations.get(result.message)||result.message:result.message);
    }
  } catch (error) {
    if (popup) popup.close();
    if (revision === requestRevision&&session===token&&attempt===githubAttempt) message(error.message, true);
  } finally {unlock();if (button.isConnected) button.disabled = false;}
});

window.addEventListener('message', async event => {
  const pending = githubPending, data = event.data;
  if (!pending || event.origin !== location.origin || event.source !== pending.popup ||
      !data || data.type !== 'careerpilot-github' || data.state !== pending.state ||
      pending.session !== token || pending.revision !== requestRevision || pending.attempt!==githubAttempt) return;
  githubPending = null;
  pending.popup.close();
  const unlock=lockGithubControls();
  try {
    if (data.error) {
      await api('/github-binding/unbind', v1Json('POST', {confirmed:true}));
      throw new Error(uiText("ph.incomplete"));
    }
    await api('/github-binding/finish', v1Json('POST', {state:data.state, code:data.code, confirmed:true}));
    if (pending.revision !== requestRevision || pending.session !== token || pending.attempt!==githubAttempt) return;
    await render(); if(requestRevision===pending.revision+1&&pending.session===token)message(uiText("ph.connected"));
  } catch (error) {if (pending.revision === requestRevision&&pending.session===token&&pending.attempt===githubAttempt) message(error.message, true);}finally{unlock();}
});


async function renderAccountSessions(revision) {
  const sessions = await api('/account/sessions');
  if (revision !== requestRevision) return;
  view.insertAdjacentHTML('beforeend', `<article class="card growth-detail" id="account-sessions" lang="${uiLocale}"><h2>${uiText("ph.sessions")}</h2>
    <p>${uiText("ph.sessionHelp")}</p>
    <p>${uiText("ph.sessionSafety")}</p>
    <ul>${sessions.map(item => `<li>${item.current ? uiText("ph.current") : uiText("ph.other")} · ${sessionDevice(item.device_label)}<br>${uiText("ph.loginAt")}：${item.created_at?projectMoment(item.created_at):uiText("ph.legacy")}<br>${item.risk_notice ? `<strong>${projectFixed(item.risk_notice)}</strong><br>` : ''}${uiText("ph.expires")}：${projectMoment(new Date(item.expires_at*1000))}
    <button data-revoke-session="${textValue(item.id)}" data-current-session="${item.current}">${uiText(item.current?"ph.revokeCurrent":"ph.revokeOther")}</button></li>`).join('')}</ul></article>`);
  uiFormFields(document.querySelector('#account-sessions'));
}
function sessionDevice(value){if(value==='未知设备')return uiText('ph.unknownDevice');return value.split(' / ').map(part=>part==='未知浏览器'?uiText('ph.unknownBrowser'):part==='未知系统'?uiText('ph.unknownSystem'):uiOriginal(part)).join(' / ');}

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-revoke-session]');
  if (!button) return;
  const revision = requestRevision, session = token;
  button.disabled = true;
  try {
    await api('/account/sessions/' + encodeURIComponent(button.dataset.revokeSession), {method:'DELETE'});
    if (session !== token) return;
    if (button.dataset.currentSession === 'true') {
      token = '';
      clearGithubPending();document.querySelector('#connect').textContent = uiText('connect');
    }else if(revision!==requestRevision)return;
    const refreshRevision=requestRevision;await render();
    if(requestRevision===refreshRevision+1)message(uiText("ph.revokedSession"));
  } catch (error) {if (revision === requestRevision) message(error.message, true);}
  finally {if (button.isConnected) button.disabled = false;}
});
