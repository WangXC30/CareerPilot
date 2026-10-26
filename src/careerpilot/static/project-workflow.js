"use strict";
const milestoneLabels=uiLabels("p.stage","pending in_progress completed passed unverified needs_review".split(" "));
const narrativeLabels=uiLabels("p.narrative","draft confirmed withdrawn".split(" "));
let projectSelection=0;

function assetChoices(p, selected=[]) {
  const values=p.assets.filter(a=>a.status==="active");
  return values.length?`<fieldset><legend>${uiText("p.linkAssets")}</legend>${values.map(a=>`<label><input type="checkbox" name="asset_ids" value="${a.id}" ${selected.includes(a.id)?"checked":""}>${uiOriginal(a.filename)}${a.kind==="github_reference"?uiText("p.userLink"):""}</label>`).join("")}</fieldset>`:`<p>${uiText("p.assetsFirst")}</p>`;
}

async function mountProjectWorkflow(p, revision, selection) {
  let plans;try{plans=await api("/plans");}catch(e){if(projectCurrent(revision,selection,p.id))throw e;return;}
  if(revision!==requestRevision||selection!==projectSelection||current!=="portfolio")return;
  const active=plans.find(plan=>plan.status==="active"), n=p.narrative||{};
  const milestones=p.progress.map(m=>`<section class="project-stage"><h3>${uiSource(m.title)}</h3><p class="tag">${milestoneLabels[m.status]||escapeHTML(m.status)}</p>${m.verification==="sandbox_tests"?`<p>${uiText("p.receiptOnly")}</p>`:`<form class="project-milestone" data-milestone="${m.id}"><label>${uiText("p.progress")}<select name="status">${["pending","in_progress","completed"].map(s=>`<option value="${s}" ${m.status===s?"selected":""}>${milestoneLabels[s]}</option>`).join("")}</select></label><label>${uiText("p.process")}<textarea name="note" maxlength="4000" rows="3">${escapeHTML(m.note||"")}</textarea></label>${assetChoices(p,m.asset_ids)}<button>${uiText("p.saveStage")}</button></form>`}</section>`).join("");
  const assets=p.assets.map(a=>`<li><strong>${uiOriginal(a.filename)}</strong> · ${a.status==="active"?uiText("p.saved"):uiText("p.withdrawn")}${a.kind==="github_reference"?`<p><a href="${escapeHTML(a.url)}" target="_blank" rel="noopener noreferrer">${uiText("p.githubReference")}</a> · ${uiText("p.notFetched")}</p>`:""}${a.status==="active"?`<div class="actions">${a.kind==="text"?`<button data-project-asset-download="${a.id}" class="secondary">${uiText("p.download")}</button>`:""}<button data-project-asset-withdraw="${a.id}" class="secondary">${uiText("p.withdrawAsset")}</button></div>`:""}</li>`).join("");
  projectState.planPreview=null;projectState.starDraft=null;
  document.querySelector("#project-detail").insertAdjacentHTML("beforeend",`
    <article class="card"><h2>${uiText("p.stages")}</h2><p>${uiText("p.stageHelp")}</p>${milestones}<details><summary>${uiText("p.history")}</summary><button id="project-history" class="secondary">${uiText("p.loadHistory")}</button><div id="project-history-results"></div></details></article>
    <article class="card"><h2>${uiText("p.assets")}</h2><ul class="project-assets">${assets||`<li>${uiText("p.noAssets")}</li>`}</ul><details><summary>${uiText("p.addAsset")}</summary><form id="project-asset"><label>${uiText("p.assetKind")}<select name="kind"><option value="text">${uiText("p.textFile")}</option><option value="github_reference">${uiText("p.githubLink")}</option></select></label><label>${uiText("p.filename")}<input name="filename" maxlength="100" value="README.md" required></label><label>${uiText("p.chooseFile")}<input name="file" type="file" accept=".txt,.md,.json"></label><label>${uiText("p.content")}<textarea name="content" maxlength="65536" rows="4"></textarea></label><label>${uiText("p.githubLink")}<input name="url" type="url" maxlength="500"></label><p>${uiText("p.assetHelp")}</p><button>${uiText("p.saveAsset")}</button></form></details></article>
    <article class="card"><h2>${uiText("p.plan")}</h2><p>${uiText("p.planHelp")}</p>${active?`<form id="project-plan" data-plan="${active.id}"><p>${uiFormat("p.currentPlan",{count:active.tasks.length})}</p><button>${uiText("p.planPreview")}</button></form>`:`<p>${uiText("p.publishFirst")}</p><a href="#learning">${uiText("p.goLearning")}</a>`}<div id="project-plan-preview"></div></article>
    <article class="card"><h2>${uiText("p.star")}</h2><p>${uiText("p.starHelp")}</p><form id="project-narrative"><label>${uiText("p.statementStatus")}<select name="status">${Object.entries(narrativeLabels).map(([s,l])=>`<option value="${s}" ${(n.status||"draft")===s?"selected":""}>${l}</option>`).join("")}</select></label>${Object.entries({situation:uiText("p.situation"),task:uiText("p.task"),action:uiText("p.action"),result:uiText("p.result")}).map(([key,label])=>`<label>${label}<textarea name="${key}" rows="3" maxlength="2000">${escapeHTML(n[key]||"")}</textarea></label>`).join("")}${assetChoices(p,n.asset_ids)}<button>${uiText("p.saveNarrative")}</button></form><div class="actions"><button id="project-star-preview" class="secondary">${uiText("p.starPreview")}</button><button id="project-narrative-history" class="secondary">${uiText("p.narrativeHistory")}</button></div><div id="project-star-result"></div><div id="project-narrative-versions"></div></article>
    <article class="card"><h2>${uiText("p.review")}</h2><p>${uiText("p.reviewHelp")}</p><form id="project-review"><label>${uiText("p.reviewDecision")}<select name="decision"><option value="request_changes">${uiText("p.changesNeeded")}</option><option value="accept">${uiText("p.meetsChecklist")}</option></select></label><label>${uiText("p.reviewBasis")}<textarea name="note" rows="3" maxlength="4000" required></textarea></label><button>${uiText("p.saveReview")}</button></form><ul>${p.reviews.map(r=>`<li>${r.decision==="accept"?uiText("p.reviewPassed"):uiText("p.changesNeeded")} · ${r.current?uiText("p.current"):uiText("p.historical")}<p>${uiOriginal(r.note)}</p></li>`).join("")||`<li>${uiText("p.noReviews")}</li>`}</ul></article>`);
  uiFormFields(document.querySelector("#project-detail"));
}

function projectPreviewMarkup(preview) {
  const error=preview.validation_error;
  return `<h3>${uiText("p.confirmPlan")}</h3><p>${uiFormat("p.planCounts",{added:preview.diff.added.length,changed:preview.diff.changed.length})}</p><ul>${preview.diff.added.map(t=>`<li>${escapeHTML(uiDate(t.date))} · ${uiOriginal(t.title)} · ${t.minutes} ${uiText("p.minutes")}</li>`).join("")}</ul>${error?`<p role="alert">${error.message_locale?escapeHTML(error.message):uiSource(error.message)}</p>`:`<p>${uiFormat("p.weeklyLimit",{minutes:preview.candidate.validation.budget_minutes})}${Object.entries(preview.candidate.validation.weekly_minutes).map(([week,minutes])=>`${escapeHTML(uiDate(week))}：${minutes} ${uiText("p.minutes")}`).join("；")}</p>`}<div class="actions"><button data-project-plan-decision="accept" ${error?"disabled":""}>${uiText("p.acceptPlan")}</button><button data-project-plan-decision="reject" class="secondary">${uiText("p.keepPlan")}</button></div>`;
}

async function refreshProjectWorkflow(p,revision,selection,messageKey){
  if(!projectCurrent(revision,selection,p.id))return;
  const refreshed=await openProject(p.id,false);
  if(refreshed&&projectCurrent(refreshed.revision,refreshed.selection,p.id)&&messageKey)message(uiText(messageKey));
}

document.addEventListener("submit",async event=>{
  const form=event.target;
  if(current!=="portfolio")return;
  if(!["project-asset","project-plan","project-narrative","project-review"].includes(form.id)&&!form.classList.contains("project-milestone"))return;
  event.preventDefault();const button=form.querySelector("button"), p=projectState.selected,revision=requestRevision,selection=projectSelection;
  if(!p)return;button.disabled=true;
  try{
    const data=new FormData(form), selected=data.getAll("asset_ids"), base=`/projects/${p.id}`;
    if(form.classList.contains("project-milestone")){
      await api(`${base}/milestones/${form.dataset.milestone}`,jsonOptions("PATCH",{status:data.get("status"),note:data.get("note"),asset_ids:selected,confirmed:true},p.version));
      await refreshProjectWorkflow(p,revision,selection,"p.stageSaved");
    }
    if(form.id==="project-asset"){
      const kind=data.get("kind"), file=data.get("file");
      if(file&&file.size>65536)throw new Error(uiText("p.fileLarge"));
      const body={kind,filename:file?.size?file.name:data.get("filename"),content:kind==="text"?(file?.size?await file.text():data.get("content")):"",url:kind==="github_reference"?data.get("url"):""};
      await api(`${base}/assets`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify(body)});
      await refreshProjectWorkflow(p,revision,selection,"p.assetSaved");
    }
    if(form.id==="project-plan"){
      const preview=await api(`${base}/plan-preview`,{...jsonOptions("POST",{plan_id:form.dataset.plan,confirmed:true},p.version),headers:{...versionHeader(p.version),"Idempotency-Key":crypto.randomUUID()}});
      if(revision!==requestRevision||selection!==projectSelection)return;
      projectState.planPreview=preview;const panel=document.querySelector("#project-plan-preview");panel.innerHTML=projectPreviewMarkup(preview);projectFocus(panel);
    }
    if(form.id==="project-narrative"){
      await api(`${base}/narrative`,jsonOptions("PUT",{status:data.get("status"),situation:data.get("situation"),task:data.get("task"),action:data.get("action"),result:data.get("result"),asset_ids:selected,confirmed:true},p.narrative?.version||0));
      await refreshProjectWorkflow(p,revision,selection,"p.narrativeSaved");
    }
    if(form.id==="project-review"){
      await api(`${base}/reviews`,{method:"POST",headers:{...versionHeader(p.version),"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({decision:data.get("decision"),note:data.get("note"),confirmed:true})});
      await refreshProjectWorkflow(p,revision,selection,"p.reviewSaved");
    }
  }catch(e){if(projectCurrent(revision,selection,p.id))message(e.message,true);}finally{button.disabled=false;}
});

document.addEventListener("click",async event=>{
  const button=event.target.closest("button"),p=projectState.selected;
  if(current!=="portfolio"||!button||!p)return;
  if(!["project-star-preview","project-star-fact","project-history","project-narrative-history"].includes(button.id)&&!Object.keys(button.dataset).some(k=>["projectAssetDownload","projectAssetWithdraw","projectPlanDecision"].includes(k)))return;
  const revision=requestRevision,selection=projectSelection;
  button.disabled=true;
  try{
    if(button.dataset.projectAssetDownload){
      const a=p.assets.find(a=>a.id===button.dataset.projectAssetDownload);
      const response=await fetch(`/api/v1/project-assets/${a.id}/download`,{headers:{Authorization:`Bearer ${token}`}});
      if(!response.ok)throw new Error(uiText("p.downloadUnavailable"));
      const blob=await response.blob();if(!projectCurrent(revision,selection,p.id))return;
      const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=a.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    if(button.dataset.projectAssetWithdraw){
      const a=p.assets.find(a=>a.id===button.dataset.projectAssetWithdraw);
      await api(`/project-assets/${a.id}/withdraw`,jsonOptions("POST",{confirmed:true},a.version));
      await refreshProjectWorkflow(p,revision,selection,"p.assetWithdrawn");
    }
    if(button.dataset.projectPlanDecision){
      button.disabled=true;const preview=projectState.planPreview;
      await api(`/plan-previews/${preview.id}/${button.dataset.projectPlanDecision}`,jsonOptions("POST",{confirmed:true},preview.version));
      await refreshProjectWorkflow(p,revision,selection,button.dataset.projectPlanDecision==="accept"?"p.planAccepted":"p.planKept");
    }
    if(button.id==="project-star-preview"){
      projectState.starDraft=null;const panel=document.querySelector("#project-star-result");panel.replaceChildren();
      const draft=await api(`/projects/${p.id}/narrative-preview`);
      if(revision!==requestRevision||selection!==projectSelection)return;
      projectState.starDraft=draft;
      panel.innerHTML=`<h3>${uiText("p.starTitle")}</h3><pre>${draft.text?uiOriginal(draft.text):uiText("p.saveActualFirst")}</pre><p>${uiText("p.unknown")}：${projectUnknown(draft.unknown)}</p><p>${projectFixed(draft.notice)}</p>${draft.narrative?.status==="confirmed"&&draft.receipt?`<button id="project-star-fact">${uiText("p.saveStarFact")}</button>`:`<p>${uiText("p.needConfirmedReceipt")}</p>`}`;
    }
    if(button.id==="project-star-fact"){
      button.disabled=true;const draft=projectState.starDraft;
      await api(`/projects/${p.id}/narrative-fact`,{method:"POST",headers:{...versionHeader(draft.narrative.version),"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({confirmed:true})});if(projectCurrent(revision,selection,p.id))message(uiText("p.starSaved"));
    }
    if(button.id==="project-history"){
      const history=await api(`/projects/${p.id}/history`);
      if(revision!==requestRevision||selection!==projectSelection)return;
      document.querySelector("#project-history-results").innerHTML=`<h3>${uiText("p.history")}</h3>`+history.map(h=>`<p>${uiFormat("p.version",{version:h.version})} · ${projectMoment(h.created_at)} · ${h.payload.latest_submission_id?uiText("p.codeLinked"):uiText("p.stageRecord")}</p>`).join("");
    }
    if(button.id==="project-narrative-history"){
      const history=await api(`/projects/${p.id}/narrative/history`);
      if(revision!==requestRevision||selection!==projectSelection)return;
      document.querySelector("#project-narrative-versions").innerHTML=`<h3>${uiText("p.narrativeHistory")}</h3>`+(history.map(h=>`<p>${uiFormat("p.version",{version:h.version})} · ${narrativeLabels[h.payload.status]} · ${h.payload.action?uiOriginal(h.payload.action):uiText("p.noAction")}</p>`).join("")||`<p>${uiText("p.noVersions")}</p>`);
    }
    if(projectCurrent(revision,selection,p.id)){
      const selector={"project-star-preview":"#project-star-result","project-history":"#project-history-results","project-narrative-history":"#project-narrative-versions"}[button.id];
      if(selector)projectFocus(document.querySelector(selector));
    }
  }catch(e){if(projectCurrent(revision,selection,p.id))message(e.message,true);}finally{button.disabled=false;}
});
