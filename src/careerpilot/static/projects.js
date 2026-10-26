"use strict";
let projectState = {};
const projectStatus=uiLabels("p.status","queued running passed user_failed timeout system_error manual_review cancelled".split(" "));

async function renderProjects(route, revision) {
  if (route !== "portfolio") return false;
  if (!token) { shellView(empty(uiText("signin.projects"),uiText("signin.projectsText"),uiText("connectAccount"),"settings")); return true; }
  view.lang=uiLocale;projectSelection++;
  view.innerHTML=`<article class="card" role="status">${uiText("p.loading")}</article>`;
  try {
    const [jobs,projects,profile]=await Promise.all([api("/jobs"),api("/projects"),api("/profile")]);
    if(revision!==requestRevision)return true;
    projectState={jobs,projects,profile,selected:null};
    view.innerHTML=`<article class="card"><h2>${uiText("p.title")}</h2><p>${uiText("p.help")}</p><p>${uiText("p.originals")}</p><form id="project-recommend"><label>${uiText("p.job")}<select name="job_id" required>${jobs.filter(j=>!["closed","merged"].includes(j.status)).map(j=>`<option lang="" data-original-content value="${escapeHTML(j.id)}">${escapeHTML(j.fields.title.value||uiText("p.importedJob"))}</option>`).join("")}</select></label><button ${jobs.length?"":"disabled"}>${uiText("p.recommend")}</button></form><div id="project-options"></div></article><div class="section-title"><h2>${uiText("p.mine")}</h2><button id="project-refresh" class="secondary">${uiText("p.refresh")}</button></div><div class="grid">${projects.map(p=>`<article class="card"><h3>${uiSource(p.template.title)}</h3><p>${p.template.estimated_minutes} ${uiText("p.minutes")} · ${escapeHTML(p.template.skill_ids.join("、"))}</p><button data-project-open="${p.id}">${uiText("p.open")}</button></article>`).join("")||`<p>${uiText("p.noProjects")}</p>`}</div><div id="project-detail"></div>`;
    uiFormFields(view);
  } catch(e){if(revision===requestRevision){shellView(empty(uiText("err.projects"),escapeHTML(e.message),uiText("retry"),"portfolio"));message(e.message,true);}}
  return true;
}

async function openProject(id,focus=true) {
  const revision=requestRevision,selection=++projectSelection;
  projectState.selected=null;projectState.planPreview=null;projectState.starDraft=null;
  const panel=document.querySelector("#project-detail");panel.innerHTML=`<p role="status">${uiText("p.loadingDetail")}</p>`;
  try{
  const p=await api(`/projects/${id}`);
  if(revision!==requestRevision||selection!==projectSelection||current!=="portfolio")return;
  projectState.selected=p;
  document.querySelector("#project-detail").innerHTML=`<article class="card"><h2>${uiSource(p.template.title)}</h2><p>${uiSource(p.template.instructions)}</p><p>${projectFixed(p.template.scope_notice)}</p><ol>${p.template.milestones.map(m=>`<li>${uiSource(m.title)} · ${m.minutes} ${uiText("p.minutes")} · ${m.verification==="sandbox_tests"?uiText("p.receiptBasis"):uiText("p.selfReview")}</li>`).join("")}</ol><form id="project-code"><label>${uiText("p.code")}<textarea name="code" rows="12" maxlength="32768" required spellcheck="false">${escapeHTML(p.submissions.at(-1)?.code||p.template.starter)}</textarea></label><p>${uiText("p.codeHelp")}</p><button>${uiText("p.submit")}</button></form><div id="project-results">${p.assessments.map(a=>`<section><h3>${projectStatus[a.status]||escapeHTML(a.status)}</h3><p>${uiText("p.testSet")} ${escapeHTML(a.test_version)} · ${uiText("p.fingerprint")} ${escapeHTML(a.artifact_hash.slice(0,12))}</p><ul>${a.results.map(r=>`<li>${escapeHTML(r.id)}：${r.passed?uiText("p.testPass"):uiText("p.testFail")} · ${projectTestReason(r.reason)}</li>`).join("")}</ul>${a.status==="passed"?`<button data-project-statement="${a.artifact_id}" class="secondary">${uiText("p.statement")}</button>`:""}<button data-project-withdraw="${a.artifact_id}" class="secondary">${uiText("p.withdrawSubmission")}</button></section>`).join("")}</div><div id="project-statement"></div></article>`;
  uiFormFields(panel);if(focus)focusLearningResult(panel);
  await mountProjectWorkflow(p,revision,selection);
  if(projectCurrent(revision,selection,id))return {revision,selection};
  }catch(e){if(projectCurrent(revision,selection))throw e;}
  return null;
}

document.addEventListener("submit",async event=>{
  if(current!=="portfolio"||!["project-recommend","project-code"].includes(event.target.id))return;
  event.preventDefault();const form=event.target,button=form.querySelector("button"),revision=requestRevision,selection=projectSelection;
  button.disabled=true;
  const request=form.id==="project-recommend"?(projectState.recommendationRequest||0)+1:null;
  if(request)projectState.recommendationRequest=request;
  const valid=()=>revision===requestRevision&&current==="portfolio"&&(request?request===projectState.recommendationRequest:selection===projectSelection);
  try {
    const data=new FormData(form);
    if(form.id==="project-recommend"){
      const options=await api(`/project-recommendations?job_id=${encodeURIComponent(data.get("job_id"))}`);
      if(!valid())return;
      projectState.options=options;
      const panel=document.querySelector("#project-options");
      panel.innerHTML=options.map((o,i)=>`<h3>${uiSource(o.title)}</h3><p>${o.estimated_minutes} ${uiText("p.minutes")} · ${uiFormat("p.estimated",{minutes:o.weekly_budget_minutes,weeks:o.estimated_weeks})}${projectFixed(o.scheduling)}</p><button data-project-create="${i}">${uiText("p.create")}</button>`).join("")||`<p>${uiText("p.noTemplate")}</p>`;
      projectFocus(panel);
    }else{
      const p=projectState.selected;if(!p)return;
      await api(`/projects/${p.id}/submissions`,{method:"POST",headers:{...versionHeader(p.version),"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({code:data.get("code")})});
      await refreshProjectWorkflow(p,revision,selection,"p.queued");
    }
  }catch(e){if(valid())message(e.message,true);}finally{button.disabled=false;}
});

document.addEventListener("change",event=>{
  if(!event.target.matches('#project-recommend [name="job_id"]'))return;
  projectState.recommendationRequest=(projectState.recommendationRequest||0)+1;
  projectState.options=[];document.querySelector("#project-options").replaceChildren();
});

document.addEventListener("click",async event=>{
  const target=event.target.closest("button");if(current!=="portfolio"||!target)return;
  if(target.id!=="project-refresh"&&!Object.keys(target.dataset).some(k=>["projectOpen","projectCreate","projectStatement","projectFact","projectWithdraw"].includes(k)))return;
  const revision=requestRevision,selection=projectSelection,p=projectState.selected;
  const valid=()=>projectCurrent(revision,selection);
  target.disabled=true;
  try {
    if(target.id==="project-refresh"){if(p)await openProject(p.id);else await render();}
    if(target.dataset.projectOpen)await openProject(target.dataset.projectOpen);
    if(target.dataset.projectCreate!==undefined){
      const o=projectState.options[Number(target.dataset.projectCreate)];
      const created=await api("/projects",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({job_id:o.job_id,template_id:o.id,confirmed:true})});
      if(!valid())return;await render();
      if(requestRevision===revision+1&&current==="portfolio")await openProject(created.id);
    }
    if(target.dataset.projectStatement){
      const id=target.dataset.projectStatement,request=(projectState.statementRequest||0)+1;
      projectState.statementRequest=request;
      const panel=document.querySelector("#project-statement");panel.replaceChildren();
      const draft=await api(`/project-submissions/${id}/statement`);
      if(!valid()||request!==projectState.statementRequest)return;
      panel.innerHTML=`<h3>${uiText("p.confirmStatement")}</h3><p>${uiOriginal(draft.text)}</p><p>${uiText("p.unknown")}: ${projectUnknown(draft.unknown)}. ${projectFixed(draft.notice)}</p><button data-project-fact="${id}">${uiText("p.saveFact")}</button>`;
      projectFocus(panel);
    }
    if(target.dataset.projectFact){await api(`/project-submissions/${target.dataset.projectFact}/resume-fact`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({confirmed:true})});if(valid())message(uiText("p.factSaved"));}
    if(target.dataset.projectWithdraw){
      const a=p.submissions.find(s=>s.id===target.dataset.projectWithdraw);
      await api(`/project-submissions/${a.id}/withdraw`,jsonOptions("POST",{confirmed:true},a.version));
      await refreshProjectWorkflow(p,revision,selection,"p.withdrawnSubmission");
    }
  }catch(e){if(revision===requestRevision&&(valid()||target.id==="project-refresh"||target.dataset.projectOpen))message(e.message,true);}finally{target.disabled=false;}
});
