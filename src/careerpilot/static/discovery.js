"use strict";
let discoveryState = {preferences:{version:0},subscriptions:[],mode:"best",session:""};
const modeKeys=["best","stretch","entry","explore"];
const feedbackKeys=["like","not_interested","misclassified","applied"];
const lines=value=>value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
const clockLabel=(epoch,zone)=>epoch===null||epoch===undefined?uiText("neverChecked"):new Intl.DateTimeFormat(uiLocale,{timeZone:zone,year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",timeZoneName:"shortOffset"}).format(new Date(epoch*1000));
const weekdayName=index=>new Intl.DateTimeFormat(uiLocale,{timeZone:"UTC",weekday:"long"}).format(new Date(Date.UTC(2024,0,1+index)));
let recommendationRequest=0;
async function refreshDiscovery(revision,key,values={}){
  if(revision!==requestRevision)return false;
  await render();
  if(requestRevision!==revision+1)return false;
  if(key)message(uiFormat(key,values));
  return true;
}

function jobControls(item){
  const f=item.feedback;
  return `<div class="job-controls"><label class="job-choice"><input type="checkbox" name="compare_jobs" value="${textValue(item.job_id)}"> ${uiText("compareSelect")}</label><button class="secondary" data-favorite="${textValue(item.job_id)}">${uiText(item.favorite?"unfavoriteJob":"favoriteJob")}</button><details><summary>${uiText("recommendationFeedback")}${f?.active?` · ${uiText('feedback.'+f.label)}`:""}</summary><form class="job-feedback-form" data-job-id="${textValue(item.job_id)}" data-version="${f?.version||0}"><label>${uiText("feedbackLabel")}<select name="label" aria-label="${uiText("feedbackLabel")}" required><option value="" disabled ${!f?.active?"selected":""}>${uiText("chooseFeedback")}</option>${feedbackKeys.map(key=>`<option value="${key}" ${f?.active&&f.label===key?"selected":""}>${uiText('feedback.'+key)}</option>`).join("")}</select></label><label>${uiText("feedbackReason")}<input name="reason" lang="" dir="auto" maxlength="1000" value="${textValue(f?.active?f.reason:"")}"></label><p>${uiText("appliedFeedbackHelp")}</p><button type="submit">${uiText("saveJobFeedback")}</button>${f?.active?`<button type="button" class="secondary" data-undo-feedback="${textValue(item.job_id)}" data-version="${f.version}">${uiText("undoJobFeedback")}</button>`:""}</form>${f?`<button class="secondary" data-feedback-history="${textValue(item.job_id)}">${uiText("feedbackHistory")}</button><div class="feedback-history"></div>`:""}</details></div>`;
}

function subscriptionForm(zone){
  return `<article class="card growth-detail" lang="${uiLocale}"><h2 id="subscription-form-title" tabindex="-1">${uiText("subscribeLibrary")}</h2><p>${uiText("subscribeHelp")}</p><form id="subscription-form" class="form-grid"><label>${uiText("subscriptionName")}<input name="name" lang="" dir="auto" required maxlength="100"></label><label>${uiText("keyword")}<input name="q" lang="" dir="auto" maxlength="200"></label><label>${uiText("subscriptionLocation")}<input name="location" lang="" dir="auto" maxlength="100"></label><label>${uiText("filterEmployment")}<input name="employment_type" lang="" dir="auto" maxlength="40"></label><label>${uiText("filterCohort")}<input name="graduation_cohort" lang="" dir="auto" maxlength="20"></label><label>${uiText("subscriptionDuration")}<input name="duration" lang="" dir="auto" maxlength="100" placeholder="${uiText("durationExample")}"></label><label>${uiText("frequency")}<select name="frequency" aria-label="${uiText("frequency")}"><option value="weekly">${uiText("weekly")}</option><option value="daily">${uiText("daily")}</option></select></label><label>${uiText("weekday")}<select name="weekday" aria-label="${uiText("weekday")}">${[0,1,2,3,4,5,6].map(i=>`<option value="${i}">${weekdayName(i)}</option>`).join("")}</select></label><label>${uiText("scheduleHour")}<input name="hour" type="number" min="0" max="23" required value="9"></label><label>${uiText("profileTimezone")}<input name="timezone" required maxlength="100" value="${textValue(zone)}"></label><label>${uiText("quietStart")}<input name="quiet_start" type="number" min="0" max="23" required value="22"></label><label>${uiText("quietEnd")}<input name="quiet_end" type="number" min="0" max="23" required value="8"></label><label class="full"><input type="checkbox" name="favorites_only"> ${uiText("favoritesOnly")}</label><p class="full">${uiText("subscriptionHelp")}</p><button type="submit" id="save-subscription">${uiText("createSubscription")}</button><button type="button" id="cancel-subscription-edit" class="secondary" hidden>${uiText("cancelEdit")}</button></form></article>`;
}

function subscriptionCards(items){
  return items.map(s=>`<article class="card subscription-card" data-subscription="${textValue(s.id)}"><span class="tag">${uiText('subscription.'+s.status)}</span><h3>${uiOriginal(s.name)}</h3><p>${s.frequency==="weekly"?`${uiText("weekly")} · ${weekdayName(s.weekday)}`:uiText("daily")} ${String(s.hour).padStart(2,'0')}:00 · ${uiOriginal(s.timezone)}<br>${uiText("nextScheduled")}：${textValue(clockLabel(s.next_run_at,s.timezone))}<br>${uiText("lastChecked")}：${textValue(clockLabel(s.last_run_at,s.timezone))}</p><p>${matchText(s.scope,s._ui?.scope)}</p>${s.pending_task?`<p>${uiText("latestTask")}：${uiText('worker.'+s.pending_task.status)} · ${uiFormat("attempts",{count:s.pending_task.attempts})}${s.pending_task.status==="queued"?`<br>${uiText("earliestRun")}：${textValue(clockLabel(s.pending_task.available_at,s.timezone))}`:""}${s.pending_task.last_error_class?`<br>${uiText("errorClass")}：${uiOriginal(s.pending_task.last_error_class)}`:""}</p>`:""}<div class="discovery-actions">${s.status==="active"?`<button data-check-subscription="${s.id}" ${["queued","running"].includes(s.pending_task?.status)?"disabled":""}>${uiText("checkLibrary")}</button><button class="secondary" data-subscription-state="paused" data-subscription-id="${s.id}">${uiText("pauseSubscription")}</button><button class="secondary" data-subscription-state="cancelled" data-subscription-id="${s.id}">${uiText("cancelSubscription")}</button>`:`<button class="secondary" data-subscription-state="active" data-subscription-id="${s.id}">${uiText("resumeSubscription")}</button>`}<button class="secondary" data-edit-subscription="${s.id}">${uiText("editConfiguration")}</button><button class="secondary" data-subscription-runs="${s.id}">${uiText("viewRuns")}</button></div><div class="subscription-runs"></div></article>`).join("")||`<p>${uiText("noSubscriptions")}</p>`;
}

function notificationCards(items){
  return items.map(n=>`<article class="card notification-card"><span class="tag">${uiText(n.read?"read":"unread")}</span><h3>${uiOriginal(n.subscription_name)}</h3><p>${matchText(n.scope,n._ui?.scope)}</p>${n.entries.map(e=>`<div class="evidence-row"><strong>${uiOriginal(e.title)} · ${uiText('change.'+e.change)}</strong><p>${uiFormat("jobVersion",{version:e.job_version})} · ${e.fields.location.value?uiOriginal(e.fields.location.value):uiText("locationUnknown")}<br>${e.sources.map(s=>s.source_url?uiOriginal(s.source_url):uiText("pastedByUser")).join("; ")}</p><button class="secondary" data-job="${textValue(e.job_id)}">${uiText("viewJobEvidence")}</button></div>`).join("")}<p>${uiText("checkedAt")} ${textValue(clockLabel(n.checked_at,n.timezone||"Asia/Shanghai"))} · ${uiOriginal(n.timezone||"Asia/Shanghai")} · ${uiFormat("changesCount",{count:n.entries.length})}</p><button class="secondary" data-read-notification="${n.id}" data-version="${n.version}" data-read="${!n.read}">${uiText(n.read?"markUnread":"markRead")}</button></article>`).join("")||`<p>${uiText("noJobDigests")}</p>`;
}

async function enhanceV2Discovery(revision){
  if(!token)return;
  const [preferences,saved,subscriptions,notifications,profile]=await Promise.all([api("/job-preferences"),api("/saved-jobs"),api("/job-subscriptions"),api("/job-notifications"),api("/profile")]);
  if(revision!==requestRevision)return;
  if(discoveryState.session!==token)discoveryState={preferences,subscriptions,mode:"best",session:token};
  else Object.assign(discoveryState,{preferences,subscriptions});
  discoveryState.editing=null;
  view.insertAdjacentHTML("beforeend",`<article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("compareFavorites")}</h2><p>${uiText("compareHelp")}</p><button id="compare-selected-jobs">${uiText("compareSelected")}</button><div id="job-comparison"></div><h3>${uiText("myFavorites")}</h3><div id="saved-jobs">${saved.map(j=>`<div class="list-row"><span>${j.fields.title.value?uiOriginal(j.fields.title.value):uiText("jobToVerify")} · ${uiText('jobStatus.'+j.status)}</span><button class="secondary" data-job="${j.id}">${uiText("viewJobEvidence")}</button><button class="secondary" data-favorite="${j.id}">${uiText("unfavoriteJob")}</button></div>`).join("")||`<p>${uiText("noFavorites")}</p>`}</div></article><article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("jobPreferences")}</h2><form id="job-preferences-form" class="form-grid"><label>${uiText("blockedCompanies")}<textarea name="blocked_companies" lang="" dir="auto" aria-label="${uiText("blockedCompanies")}" maxlength="10000">${textValue(preferences.blocked_companies.join("\n"))}</textarea></label><label>${uiText("excludedKeywords")}<textarea name="excluded_keywords" lang="" dir="auto" aria-label="${uiText("excludedKeywords")}" maxlength="10000">${textValue(preferences.excluded_keywords.join("\n"))}</textarea></label><fieldset class="full"><legend>${uiText("enabledModes")}</legend>${modeKeys.map(key=>`<label class="job-choice"><input type="checkbox" name="enabled_modes" value="${key}" ${preferences.enabled_modes.includes(key)?"checked":""}> ${uiText('mode.'+key)}</label>`).join("")}</fieldset><p class="full">${uiText("preferencesHelp")}</p><button type="submit">${uiText("saveJobPreferences")}</button></form></article><article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("recommendationModes")}</h2><form id="recommendation-mode-form"><label>${uiText("recommendationMode")}<select name="mode" aria-label="${uiText("recommendationMode")}">${modeKeys.map(key=>`<option value="${key}" ${discoveryState.mode===key?"selected":""}>${uiText('mode.'+key)}</option>`).join("")}</select></label><button type="submit">${uiText("showMode")}</button></form><div id="job-recommendations"></div></article>${subscriptionForm(profile.timezone||"Asia/Shanghai")}<article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("mySubscriptions")}</h2><button id="refresh-discovery" class="secondary">${uiText("refreshDiscovery")}</button><div id="job-subscriptions" class="growth-stack">${subscriptionCards(subscriptions)}</div></article><article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("jobDigests")}</h2><div id="job-notifications" class="growth-stack">${notificationCards(notifications)}</div></article>`);
  await showRecommendations(discoveryState.mode,revision,false);
}

async function showRecommendations(mode,revision,focus=true){
  const generation=++recommendationRequest;
  const panel=document.querySelector("#job-recommendations");
  if(revision!==requestRevision||!panel)return;
  panel.setAttribute("aria-busy","true");
  panel.innerHTML=`<p role="status">${uiText("loading")}</p>`;
  try{
    const result=await api(`/job-recommendations?mode=${encodeURIComponent(mode)}`);
    if(revision!==requestRevision||generation!==recommendationRequest)return;
    discoveryState.mode=mode;
    panel.innerHTML=`<h3 tabindex="-1">${textValue(uiFormat("modeResult",{mode:uiText('mode.'+mode)}))}</h3><p>${matchText(result.notice,result._ui?.notice)}</p><p>${matchText(result.feedback_basis,result._ui?.feedback_basis)}</p>${result.disabled?`<p>${uiText("modeDisabled")}</p>`:[["eligible","eligibleGroup"],["needs_review","match.UNKNOWN"]].map(([key,label])=>`<section><h3>${uiText(label)}（${result[key].length}）</h3><div class="grid">${result[key].map(matchCard).join("")||`<p>${uiText("noModeJobs")}</p>`}</div></section>`).join("")}`;
    if(focus)focusLearningResult(panel);
  }catch(error){if(revision!==requestRevision||generation!==recommendationRequest)return;panel.innerHTML=`<p role="alert">${textValue(error.message)}</p>`;throw error;}
  finally{if(revision===requestRevision&&generation===recommendationRequest)panel.setAttribute("aria-busy","false");}
}

function subscriptionPayload(form){
  const v=Object.fromEntries(new FormData(form));
  return {name:v.name,filters:{q:v.q,location:v.location,employment_type:v.employment_type,graduation_cohort:v.graduation_cohort,duration:v.duration},favorites_only:!!v.favorites_only,frequency:v.frequency,hour:Number(v.hour),weekday:Number(v.weekday),timezone:v.timezone,quiet_start:Number(v.quiet_start),quiet_end:Number(v.quiet_end),source:"local_library",confirmed:true};
}

let discoveryInputRevision=0;
for(const name of ["input","change"])document.addEventListener(name,event=>{
  if(event.target.closest("#view")&&event.target.matches("input,textarea,select"))discoveryInputRevision++;
});

document.addEventListener("submit",async event=>{
  const form=event.target;
  if(!["job-preferences-form","recommendation-mode-form","subscription-form"].includes(form.id)&&!form.matches(".job-feedback-form"))return;
  event.preventDefault();const revision=requestRevision,button=event.submitter;if(button)button.disabled=true;
  try{
    if(form.id==="job-preferences-form"){
      const v=Object.fromEntries(new FormData(form)),p=discoveryState.preferences;
      await api("/job-preferences",v1Json("PUT",{favorite_ids:p.favorite_ids,blocked_companies:lines(v.blocked_companies),excluded_keywords:lines(v.excluded_keywords),enabled_modes:new FormData(form).getAll("enabled_modes"),confirmed:true},p.version));
      await refreshDiscovery(revision,"preferencesSaved");
    }else if(form.id==="recommendation-mode-form")await showRecommendations(new FormData(form).get("mode"),revision);
    else if(form.matches(".job-feedback-form")){
      const v=Object.fromEntries(new FormData(form));
      await api(`/jobs/${form.dataset.jobId}/feedback`,v1Json("PUT",{...v,confirmed:true},Number(form.dataset.version)));
      await refreshDiscovery(revision,"feedbackRecorded");
    }else if(form.id==="subscription-form"){
      const editing=discoveryState.editing,body=subscriptionPayload(form);
      await api(editing?`/job-subscriptions/${editing.id}`:"/job-subscriptions",{...v1Json(editing?"PUT":"POST",body,editing?.version),headers:editing?versionHeader(editing.version):{"Idempotency-Key":crypto.randomUUID()}});
      if(revision===requestRevision){discoveryState.editing=null;await refreshDiscovery(revision,editing?"subscriptionUpdated":"subscriptionCreated");}
    }
  }catch(error){if(revision===requestRevision)message(error.message,true);}finally{if(button)button.disabled=false;}
});

document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button)return;
  const d=button.dataset,revision=requestRevision;
  if(!d.favorite&&!d.undoFeedback&&!d.feedbackHistory&&!d.checkSubscription&&!d.subscriptionState&&!d.editSubscription&&!d.subscriptionRuns&&!d.readNotification&&!["compare-selected-jobs","refresh-discovery","cancel-subscription-edit"].includes(button.id))return;
  button.disabled=true;
  try{
    if(d.favorite){
      const p=discoveryState.preferences,ids=p.favorite_ids.includes(d.favorite)?p.favorite_ids.filter(id=>id!==d.favorite):[...p.favorite_ids,d.favorite];
      await api("/job-preferences",v1Json("PUT",{favorite_ids:ids,blocked_companies:p.blocked_companies,excluded_keywords:p.excluded_keywords,enabled_modes:p.enabled_modes,confirmed:true},p.version));
      await refreshDiscovery(revision,"favoritesUpdated");
    }
    if(d.undoFeedback){await api(`/jobs/${d.undoFeedback}/feedback/undo`,v1Json("POST",{confirmed:true},Number(d.version)));await refreshDiscovery(revision,"feedbackUndone");}
    if(d.feedbackHistory){
      const rows=await api(`/jobs/${d.feedbackHistory}/feedback/history`);
      if(revision===requestRevision){const box=button.closest(".match-card").querySelector(".feedback-history");box.innerHTML=`<h3 tabindex="-1">${uiText("feedbackHistory")}</h3>`+rows.map(r=>`<p>v${r.version} · ${uiText('feedback.'+r.payload.label)} · ${uiText(r.payload.active?"active":"revoked")}<br>${uiOriginal(r.payload.reason)}</p>`).join("");focusLearningResult(box);}
    }
    if(button.id==="compare-selected-jobs"){
      const ids=[...new Set([...document.querySelectorAll("input[name=compare_jobs]:checked")].map(e=>e.value))];
      if(ids.length<2||ids.length>4)throw new Error(uiText("compareRequired"));
      const result=await api("/jobs/compare",v1Json("POST",{job_ids:ids}));if(revision!==requestRevision)return;
      const box=document.querySelector("#job-comparison");
      box.innerHTML=`<h3 tabindex="-1">${uiText("compareSelected")}</h3><p>${matchText(result.notice,result._ui?.notice)}</p><div class="table-wrap" tabindex="0" role="region" aria-label="${uiText("compareSelected")}"><table><caption class="visually-hidden">${uiText("compareSelected")}</caption><thead><tr><th scope="col">${uiText("compareCondition")}</th>${result.jobs.map(j=>`<th scope="col">${j.fields.title.value?uiOriginal(j.fields.title.value):uiText("jobToVerify")} · v${j.version}</th>`).join("")}</tr></thead><tbody>${Object.keys(fieldLabels).map(key=>`<tr><th scope="row">${uiText('job.'+key)}</th>${result.jobs.map(j=>`<td>${j.fields[key].value===null||j.fields[key].value===undefined?uiText("unknown"):uiOriginal(j.fields[key].value)}</td>`).join("")}</tr>`).join("")}<tr><th scope="row">${uiText("currentEligibility")}</th>${result.jobs.map(j=>`<td>${eligibilityLabel(j.match.eligibility)}${j.match.policy_blocked?" · "+uiText("matchBlocked"):""}</td>`).join("")}</tr></tbody></table></div>`;
      focusLearningResult(box);
    }
    if(d.subscriptionState){const s=discoveryState.subscriptions.find(s=>s.id===d.subscriptionId);await api(`/job-subscriptions/${s.id}`,v1Json("PATCH",{status:d.subscriptionState,confirmed:true},s.version));await refreshDiscovery(revision,"subscriptionStateSaved");}
    if(d.checkSubscription){
      const run=await api(`/job-subscriptions/${d.checkSubscription}/check`,{...v1Json("POST",{confirmed:true}),headers:{"Idempotency-Key":crypto.randomUUID()}});
      if(await refreshDiscovery(revision,"checkQueued"))pollSubscription(d.checkSubscription,run.id,requestRevision,discoveryInputRevision);
    }
    if(d.subscriptionRuns){
      const rows=await api(`/job-subscriptions/${d.subscriptionRuns}/runs`);
      if(revision===requestRevision){const box=button.closest(".subscription-card").querySelector(".subscription-runs");box.innerHTML=`<h3 tabindex="-1">${uiText("viewRuns")}</h3>`+(rows.map(r=>`<p>${r.task?uiText('worker.'+r.task.status):uiText("taskUnverified")} · ${uiFormat("attempts",{count:r.task?.attempts||0})}${r.result?` · ${uiFormat("changesCount",{count:r.result.changes})}`:""}<br>${uiOriginal(r.task?.last_error_class||"")} · ${uiOriginal(r.id.slice(0,8))}</p>`).join("")||`<p>${uiText("noChecks")}</p>`);focusLearningResult(box);}
    }
    if(d.editSubscription){
      const s=discoveryState.subscriptions.find(s=>s.id===d.editSubscription),form=document.querySelector("#subscription-form");discoveryState.editing=s;discoveryInputRevision++;
      for(const [key,value] of Object.entries({...s,...s.filters})){const control=form.elements.namedItem(key);if(control){if(control.type==="checkbox")control.checked=!!value;else control.value=value??"";}}
      document.querySelector("#subscription-form-title").textContent=uiText("editSubscription");document.querySelector("#save-subscription").textContent=uiText("saveSubscription");document.querySelector("#cancel-subscription-edit").hidden=false;form.elements.namedItem("name").focus();
    }
    if(button.id==="cancel-subscription-edit"){discoveryState.editing=null;discoveryInputRevision++;document.querySelector("#subscription-form").reset();document.querySelector("#subscription-form-title").textContent=uiText("subscribeLibrary");document.querySelector("#save-subscription").textContent=uiText("createSubscription");button.hidden=true;document.querySelector("#save-subscription").focus();}
    if(d.readNotification){await api(`/job-notifications/${d.readNotification}`,v1Json("PATCH",{read:d.read==="true",confirmed:true},Number(d.version)));await refreshDiscovery(revision);}
    if(button.id==="refresh-discovery")await refreshDiscovery(revision);
  }catch(error){if(revision===requestRevision)message(error.message,true);}finally{button.disabled=false;}
});

async function pollSubscription(subscriptionId,runId,revision,inputRevision){
  if(revision!==requestRevision||current!=="market")return;
  try{
    const runs=await api(`/job-subscriptions/${subscriptionId}/runs`);if(revision!==requestRevision)return;
    const run=runs.find(r=>r.id===runId),status=run?.task?.status;
    if(["queued","running"].includes(status)){
      if(run.task.available_at>Date.now()/1000+30){message(uiText("checkScheduled"));return;}
      setTimeout(()=>pollSubscription(subscriptionId,runId,revision,inputRevision),1000);
    }else if(!run){message(uiText("taskUnverified"),true);}
    else if(discoveryInputRevision!==inputRevision||discoveryState.editing){message(uiText("subscriptionResultsReady"));}
    else{
      const key=status==="success"?"checkCompleted":status==="cancelled"?"checkCancelled":"checkFailed";
      if(await refreshDiscovery(revision,key,{count:run.result?.changes||0})&&status==="failed")message(uiText(key),true);
    }
  }catch(error){if(revision===requestRevision)message(error.message,true);}
}
