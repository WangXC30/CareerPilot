"use strict";
let hiringState = {};
const factLabels=uiLabels("h.fact","name contact summary education experience project skill".split(" "));
const applicationLabels=uiLabels("h.application","draft prepared awaiting_user_submission submitted assessment interviewing offer rejected withdrawn no_response".split(" "));
const appNext = {draft:["prepared","withdrawn"],prepared:["awaiting_user_submission","withdrawn"],awaiting_user_submission:["withdrawn"],submitted:["assessment","interviewing","rejected","withdrawn","no_response"],assessment:["interviewing","rejected","withdrawn"],interviewing:["offer","rejected","withdrawn"]};
const reminderLabels=uiLabels("h.reminder","deadline assessment interview follow_up".split(" "));
const reminderStates=uiLabels("h.reminderState","active paused cancelled delivered suppressed".split(" "));
const sourceStates=uiLabels("h.source","current revoked source_deleted source_withdrawn needs_reconfirmation".split(" "));
const hiringOptions = (entries, selected="",original=true) => entries.map(([id,label])=>`<option value="${escapeHTML(id)}" lang="${original?"":uiLocale}" ${original?"data-original-content":""} ${id===selected?"selected":""}>${escapeHTML(label)}</option>`).join("");
const hiringJson = (method,body,version,key=false)=>({method,body:JSON.stringify(body),headers:{...(version===undefined?{}:versionHeader(version)),...(key?{"Idempotency-Key":crypto.randomUUID()}:{})}});
const jobName = job => job ? job.title||uiText("h.unknownJob") : uiText("h.generalResume");
const resumeJob = job => job ? job.title?uiOriginal(job.title):uiText("h.unknownJob") : uiText("h.generalResume");
const hiringTimestamp = value => uiMoment(value,hiringState.profile?.timezone||"Asia/Shanghai");
function factSource(f){
  const s=f.source;
  if(s.type==="manual")return `${uiText("h.manual")} · ${hiringTimestamp(f.confirmed_at)}
${uiText("h.notIndependent")}`;
  if(s.type==="profile")return `${uiText("h.profileSource")} · ${uiText({major:"h.major",education:"h.education",graduation_date:"h.graduation"}[s.field])} · ${uiFormat("h.sourceVersion",{version:s.version})}
${s.field==="graduation_date"?uiDate(s.value):uiOriginal(s.value)}`;
  const material=hiringState.materials?.find(m=>m.id===s.material_id);
  return `${uiText("h.memorySource")} · ${uiFormat("h.sourceVersion",{version:s.version})}
${uiText("h.originalMaterial")}: ${material?uiOriginal(material.filename):uiText("h.privateMaterial")}
${uiText("h.quote")}: ${uiOriginal(s.quote||f.text)}
${uiText("h.notSkillVerification")}`;
}
const hiringDate = (epoch,zone)=>uiMoment(new Date(epoch*1000),zone)+` (${escapeHTML(zone)})`;
async function refreshHiring(revision,key){
  if(revision!==requestRevision)return;
  await render();
  if(requestRevision===revision+1&&current==="recruiting")message(uiText(key));
}
function clearResumeInspection(){
  hiringState.inspectionRequest=(hiringState.inspectionRequest||0)+1;
  hiringState.inspected=null;
  document.querySelector("#resume-inspection").replaceChildren();
  document.querySelector("#application-resume").innerHTML=`<option value="">${uiText("h.inspectVersionFirst")}</option>`;
}

async function renderHiring(route, revision) {
  if(route!=="recruiting") return false;
  if(!token){shellView(empty(uiText("signin.hiring"),uiText("signin.hiringText"),uiText("settings"),"settings"));return true;}
  view.lang=uiLocale;
  view.innerHTML=`<article class="card" role="status">${uiText("h.loading")}</article>`;
  try {
    const [facts,roots,jobs,memories,profile,applications,materials,reminders,notices,drafts]=await Promise.all([
      api("/resume-facts"),api("/resumes"),api("/jobs?limit=100"),api("/memories"),api("/profile"),api("/applications"),api("/materials"),api("/application-reminders"),api("/application-notifications"),api("/application-drafts")]);
    if(revision!==requestRevision)return true;
    hiringState={facts,roots,jobs,memories,profile,applications,materials,reminders,notices,drafts};
    const jobOptions=hiringOptions(jobs.map(j=>[j.id,j.fields.title.value||uiText("h.unknownJob")]));
    view.innerHTML=`<div class="growth-stack hiring-space"><article class="card hero"><small lang="en">${uiText("h.kicker")}</small><h2>${uiText("h.hero")}</h2><p>${uiText("h.heroHelp")}</p><p>${uiText("h.originalNote")}</p><div class="discovery-actions"><a href="#hiring-facts">${uiText("h.factsAnchor")}</a><a href="#hiring-resumes" data-hiring-anchor="hiring-resumes">${uiText("h.resumesAnchor")}</a><a href="#application-board" data-hiring-anchor="application-board">${uiText("h.applicationsAnchor")}</a></div></article>
    <article class="card" id="hiring-facts"><h2>${uiText("h.factsTitle")}</h2><p>${uiText("h.factsHelp")}</p>
    <details><summary>${uiText("h.importTitle")}</summary><p>${uiText("h.importHelp")}</p><form id="resume-import-form"><label>${uiText("h.textResume")}<input type="file" name="resume_file" accept=".txt,.md,.json" required></label><button>${uiText("h.previewImport")}</button></form><div id="resume-import-preview"></div></details>
    <details><summary>${uiText("h.addFact")}</summary><form id="fact-form" class="form-grid">
    <label>${uiText("h.section")}<select name="kind">${hiringOptions(Object.entries(factLabels),"",false)}</select></label>
    <label>${uiText("h.sourceLabel")}<select name="source_type"><option value="manual">${uiText("h.manual")}</option><option value="memory">${uiText("h.memorySource")}</option><option value="profile">${uiText("h.confirmedProfile")}</option></select></label>
    <label class="full" data-fact-source="manual">${uiText("h.trueStatement")}<textarea name="text" maxlength="4000"></textarea></label>
    <label class="full" data-fact-source="memory" hidden>${uiText("h.selectMemory")}<select name="source_id">${hiringOptions(memories.filter(m=>m.category!=="inference"&&!m.superseded_by).map(m=>[m.id,m.text]))}</select></label>
    <label class="full" data-fact-source="profile" hidden>${uiText("h.profileField")}<select name="profile_field"><option value="major">${uiText("h.major")}</option><option value="education">${uiText("h.education")}</option><option value="graduation_date">${uiText("h.graduation")}</option></select></label>
    <label class="full"><input type="checkbox" name="confirmed" required> ${uiText("h.confirmFact")}</label><button type="submit">${uiText("h.saveFact")}</button></form></details>
    <div id="fact-library">${facts.map(f=>`<div class="evidence-row"><strong>${factLabels[f.kind]} · ${sourceStates[f.source_status]}</strong><p>${uiOriginal(f.text)}</p><details><summary>${uiText("h.factActions")}</summary><pre>${factSource(f)}</pre><button class="secondary" data-copy-fact="${f.id}">${uiText("h.copyFact")}</button>${f.source_status==="current"?`<form class="revoke-fact" data-id="${f.id}"><label>${uiText("h.revokeReason")}<input name="reason" required maxlength="1000"></label><button class="secondary">${uiText("h.revokeFact")}</button></form>`:""}</details></div>`).join("")||`<p>${uiText("h.noFacts")}</p>`}</div></article>
    <article class="card" id="hiring-resumes"><h2>${uiText("h.resumeWorkspace")}</h2><form id="resume-form" class="form-grid"><label>${uiText("h.chooseResume")}<select name="resume_id"><option value="">${uiText("h.newResume")}</option>${hiringOptions(roots.map(r=>[r.id,`${r.title} · ${uiFormat("h.versionCount",{count:r.version_count})}`]))}</select></label><label>${uiText("h.resumeName")}<input name="title" maxlength="100" value="${uiText("h.defaultResume")}"></label><label class="full">${uiText("h.tailoredJob")}<select name="job_id"><option value="">${uiText("h.generalResume")}</option>${jobOptions}</select></label><fieldset class="full"><legend>${uiText("h.chooseFacts")}</legend>${facts.filter(f=>f.source_status==="current").map(f=>`<label class="job-choice"><input type="checkbox" name="fact_ids" value="${f.id}"> ${factLabels[f.kind]} · ${uiOriginal(f.text)}</label>`).join("")||uiText("h.addFactsFirst")}</fieldset><label class="full"><input type="checkbox" name="confirmed" required> ${uiText("h.confirmVersion")}</label><button>${uiText("h.saveVersion")}</button><button type="button" id="tailor-resume" class="secondary">${uiText("h.tailor")}</button></form><div id="resume-versions"></div><div id="resume-inspection" aria-live="polite"></div></article>
    <article class="card"><h2>${uiText("h.applicationDraft")}</h2><p>${uiText("h.applicationHelp")}</p><form id="application-form" class="form-grid"><label>${uiText("h.job")}<select name="job_id" required>${jobOptions}</select></label><label>${uiText("h.fixedVersion")}<select name="resume_version_id" id="application-resume" required><option value="">${uiText("h.inspectVersionFirst")}</option></select></label><label class="full"><input type="checkbox" required> ${uiText("h.confirmApplication")}</label><button>${uiText("h.createApplication")}</button></form></article>
    <section id="application-board"><div class="section-title"><h2>${uiText("h.board")} · ${applications.length}</h2></div><div class="grid two">${applications.map(applicationCard).join("")||`<article class="card"><p>${uiText("h.noApplications")}</p></article>`}</div></section>
    <article class="card" id="hiring-notices"><h2>${uiText("h.notices")}</h2><p>${uiText("h.reminderHelp")}</p>${reminders.map(r=>`<div class="evidence-row"><strong>${reminderLabels[r.kind]} · ${reminderStates[r.status]}</strong><p>${hiringDate(r.due_at,r.timezone)}</p>${["active","paused"].includes(r.status)?`<div class="discovery-actions"><button data-reminder-action="${r.id}" data-status="${r.status==="active"?"paused":"active"}" class="secondary">${r.status==="active"?uiText("h.pauseReminder"):uiText("h.resumeReminder")}</button><button data-reminder-action="${r.id}" data-status="cancelled" class="secondary">${uiText("h.cancelReminder")}</button></div>`:""}</div>`).join("")||`<p>${uiText("h.noReminders")}</p>`}${notices.map(n=>`<div class="evidence-row"><strong>${reminderLabels[n.kind]} · ${n.job?.title?uiOriginal(n.job.title):uiText("h.unknownJob")} · ${n.read?uiText("h.read"):uiText("h.unread")}</strong><p>${recruitingFixed(n.message)}${n.job_changed?uiText("h.jobChanged"):""}</p><p>${hiringDate(n.delivered_at,n.timezone)}</p>${!n.read?`<button data-hiring-read="${n.id}" class="secondary">${uiText("h.markRead")}</button>`:""}</div>`).join("")}</article>
    <article class="card"><h2>${uiText("h.materialDrafts")}</h2><p>${uiText("h.materialHelp")}</p>${drafts.map(d=>`<details><summary>${({email:uiText("h.draft.email"),cover_letter:uiText("h.draft.cover_letter"),introduction:uiText("h.draft.introduction")})[d.kind]} · ${d.tone==="formal"?uiText("h.tone.formal"):uiText("h.tone.plain")}</summary><pre>${uiOriginal(d.text)}</pre><p>${uiText("h.unsent")} · ${uiFormat("h.resumeVersion",{sequence:d.resume_sequence||uiText("i.toCheck")})}</p></details>`).join("")||`<p>${uiText("h.draftWhere")}</p>`}</article></div>`;
    const first=view.querySelector('[href="#hiring-facts"]');first.dataset.hiringAnchor="hiring-facts";
    recruitingFields(view);
    await mountInterviews(revision);
  } catch(e){if(revision===requestRevision){shellView(empty(uiText("err.hiring"),escapeHTML(e.message),uiText("retry"),"recruiting"));message(e.message,true);}}
  return true;
}

function applicationCard(a){
  const sources=a.job.sources.filter(s=>s.source_url);
  const materials=hiringOptions(hiringState.materials.map(m=>[m.id,m.filename]));
  return `<article class="card application-card" data-application="${a.id}"><span class="tag">${applicationLabels[a.status]}</span><h3>${a.job?.title?uiOriginal(a.job.title):uiText("h.unknownJob")}</h3><p>${a.job.company?uiOriginal(a.job.company):uiText("h.companyUnknown")}<br>${uiText("h.fixedResume")}：${a.resume_title?uiOriginal(a.resume_title):uiText("h.savedResume")} · ${uiFormat("h.version",{sequence:a.resume_sequence||uiText("i.toCheck")})}<br>${uiText("h.updated")}: ${escapeHTML(hiringTimestamp(a.last_event.at))}<br>${a.verification==="pending_verification"?uiText("h.pendingReceipt"):a.verification==="user_confirmed_receipt"?uiText("h.selfReceipt"):""}</p>${a.receipt?`<p>${uiText("h.receiptCode")}：${uiOriginal(a.receipt.receipt_code)}<br>${uiText("h.submittedAt")}：${escapeHTML(hiringTimestamp(a.receipt.submitted_at))}</p>`:""}
  <div class="discovery-actions">${sources.map(s=>`<a class="text-button" href="${escapeHTML(s.source_url)}" target="_blank" rel="noopener noreferrer">${uiText("h.visitSource")}</a>`).join("")||`<p>${uiText("h.noSourceLink")}</p>`}<button data-app-history="${a.id}" class="secondary">${uiText("h.eventHistory")}</button><button data-inspect-resume="${a.resume_version_id}" class="secondary">${uiText("h.boundResume")}</button></div><div class="app-history"></div>
  ${(appNext[a.status]||[]).length?`<details><summary>${uiText("h.advanceApplication")}</summary><form class="application-transition" data-id="${a.id}"><label>${uiText("h.next")}<select name="target">${hiringOptions((appNext[a.status]||[]).map(s=>[s,applicationLabels[s]]),"",false)}</select></label><label>${uiText("h.note")}<textarea name="note" maxlength="2000"></textarea></label>${a.status==="draft"?`<fieldset><legend>${uiText("h.checklist")}</legend>${Object.entries({facts:uiText("h.check.facts"),contact:uiText("h.check.contact"),requirements:uiText("h.check.requirements"),privacy:uiText("h.check.privacy"),source_reviewed:uiText("h.check.source_reviewed")}).map(([key,label])=>`<label class="job-choice"><input type="checkbox" name="${key}"> ${label}</label>`).join("")}</fieldset>`:""}<label><input type="checkbox" name="confirmed" required> ${uiText("h.confirmStatus")}</label><button>${uiText("h.updateStatus")}</button></form></details>`:""}
  ${a.status==="awaiting_user_submission"?`<details open><summary>${uiText("h.checkReceipt")}</summary><p>${uiText("h.receiptHelp")}</p><form class="application-receipt" data-id="${a.id}"><label>${uiText("h.receiptText")}<textarea name="receipt_text" maxlength="20000"></textarea></label><button type="button" data-save-receipt="${a.id}" class="secondary">${uiText("h.saveReceipt")}</button><label>${uiText("h.chooseMaterial")}<select name="material_id"><option value="">${uiText("h.noReceiptYet")}</option>${materials}</select></label><label>${uiText("h.receiptCode")}<input name="receipt_code" maxlength="100"></label><label>${uiText("h.actualSubmitted")} · ${escapeHTML(Intl.DateTimeFormat().resolvedOptions().timeZone)}<input name="submitted_at" type="datetime-local"></label><label>${uiText("h.note")}<textarea name="note" maxlength="2000"></textarea></label><label><input type="checkbox" required> ${uiText("h.confirmReceipt")}</label><button>${uiText("h.saveSubmission")}</button></form></details>`:""}
  ${!(a.status in appNext)?"":`<details><summary>${uiText("h.addReminder")}</summary><form class="application-reminder" data-id="${a.id}"><label>${uiText("h.reminderKind")}<select name="kind">${hiringOptions(Object.entries(reminderLabels),"",false)}</select></label><label>${uiText("h.localTime")}<input type="datetime-local" name="local_time" required></label><label>${uiText("h.ianaZone")}<input name="timezone" value="${escapeHTML(hiringState.profile?.timezone||"Asia/Shanghai")}" required></label><div class="form-grid"><label>${uiText("h.quietStart")}<input type="number" name="quiet_start" value="22" min="0" max="23" required></label><label>${uiText("h.quietEnd")}<input type="number" name="quiet_end" value="8" min="0" max="23" required></label></div><label><input type="checkbox" required> ${uiText("h.confirmReminder")}</label><button>${uiText("h.saveReminder")}</button></form></details>`}</article>`;
}

async function loadResumeVersions(id,revision=requestRevision){
  const selection=(hiringState.versionRequest||0)+1;hiringState.versionRequest=selection;
  clearResumeInspection();document.querySelector("#resume-versions").replaceChildren();
  let versions;try{versions=id?await api(`/resumes/${id}/versions`):[];}catch(e){if(revision===requestRevision&&hiringState.versionRequest===selection)throw e;return;}
  if(revision!==requestRevision||current!=="recruiting"||hiringState.versionRequest!==selection)return;
  if(document.querySelector('#resume-form [name="resume_id"]').value!==id)return;
  hiringState.versions=versions;
  document.querySelector("#resume-versions").innerHTML=versions.length?`<h3>${uiText("h.versionHistory")}</h3>${versions.map(v=>`<div class="list-row"><div><strong>${uiFormat("h.version",{sequence:v.sequence})}</strong><p>${hiringTimestamp(v.created_at)} · ${resumeJob(v.job)} · ${uiFormat("h.factCount",{count:v.facts.length})}</p></div><div class="discovery-actions"><button class="secondary" data-inspect-resume="${v.id}">${uiFormat("h.inspectVersion",{sequence:v.sequence})}</button><button class="secondary" data-restore-resume="${v.id}">${uiText("h.restoreVersion")}</button></div></div>`).join("")}`:"";
  const latest=versions[0];
  const form=document.querySelector("#resume-form");
  if(latest){form.elements.job_id.value=latest.job?.id||"";form.querySelectorAll('[name="fact_ids"]').forEach(input=>{input.checked=latest.facts.some(f=>f.id===input.value);});}
}

async function inspectResume(id,revision=requestRevision){
  clearResumeInspection();const selection=hiringState.inspectionRequest;
  document.querySelector("#resume-inspection").innerHTML=`<p role="status">${uiText("h.selectionLoading")}</p>`;
  let v,ats;try{[v,ats]=await Promise.all([api(`/resume-versions/${id}`),api(`/resume-versions/${id}/ats`)]);}catch(e){if(revision===requestRevision&&hiringState.inspectionRequest===selection)throw e;return;}
  if(revision!==requestRevision||current!=="recruiting"||hiringState.inspectionRequest!==selection)return;
  hiringState.inspected=v;
  const panel=document.querySelector("#resume-inspection");
  panel.innerHTML=`<div class="growth-detail"><h3>${uiFormat("h.resumeVersion",{sequence:v.sequence})} · ${resumeJob(v.job)}</h3><p>${v.source_status.valid?uiText("h.allSourcesValid"):uiText("h.sourcesChanged")}</p><div class="resume-preview">${Object.entries(factLabels).map(([kind,label])=>{const facts=v.facts.filter(f=>f.kind===kind);return facts.length?`<h3>${label}</h3>${facts.map(f=>`<p>${uiOriginal(f.text)}</p><details><summary>${uiText("h.factSource")}</summary><pre>${factSource(f)}</pre></details>`).join("")}`:"";}).join("")}</div><h3>${uiText("h.atsTitle")}</h3><p>${recruitingFixed(ats.notice)}</p><p>${ats.issues.map(i=>`${recruitingFixed(i.message)}${i.original?`<br>${uiText("h.originalSentence")}: ${uiOriginal(i.original)}`:""}<br>${uiText("h.suggestion")}: ${recruitingFixed(i.suggestion)}`).join("<br><br>")||uiText("h.noIssues")}</p><p>${uiText("h.missingKeywords")}：${ats.missing_keywords.length?uiOriginal(ats.missing_keywords.join(", ")):uiText("h.none")}. ${uiText("h.onlyTrueExperience")}</p><div class="discovery-actions"><button data-resume-export="docx" ${!v.source_status.valid?"disabled":""}>${uiText("h.exportWord")}</button><button data-resume-export="pdf" ${!v.source_status.valid?"disabled":""}>${uiText("h.exportPdf")}</button></div><form id="material-draft" class="form-grid"><label>${uiText("h.materialType")}<select name="kind"><option value="cover_letter">${uiText("h.draft.cover_letter")}</option><option value="email">${uiText("h.draft.email")}</option><option value="introduction">${uiText("h.draft.introduction")}</option></select></label><label>${uiText("h.tone")}<select name="tone"><option value="plain">${uiText("h.tone.plain")}</option><option value="formal">${uiText("h.tone.formal")}</option></select></label><label class="full"><input type="checkbox" required> ${uiText("h.confirmDraft")}</label><button>${uiText("h.generateDraft")}</button></form></div>`;
  const select=document.querySelector("#application-resume");
  select.innerHTML=hiringOptions([[v.id,`${uiFormat("h.version",{sequence:v.sequence})} · ${jobName(v.job)}`]],v.id);
  if(v.job)document.querySelector('#application-form [name="job_id"]').value=v.job.id;
  recruitingFields(panel);focusLearningResult(panel);
  panel.scrollIntoView({block:"start"});
}

document.addEventListener("change",async event=>{
  if(current!=="recruiting")return;
  try{
    if(event.target.matches('#fact-form [name="source_type"]')){
      document.querySelectorAll("[data-fact-source]").forEach(e=>{e.hidden=e.dataset.factSource!==event.target.value;});
      if(event.target.value==="profile")document.querySelector('#fact-form [name="kind"]').value="education";
    }
    if(event.target.matches('#resume-form [name="resume_id"]'))await loadResumeVersions(event.target.value);
  }catch(e){message(e.message,true);}
});

document.addEventListener("submit",async event=>{
  const form=event.target;
  if(current!=="recruiting"||!form.matches("#fact-form,#resume-form,#application-form,#material-draft,#resume-import-form,#resume-import-confirm,.revoke-fact,.application-transition,.application-receipt,.application-reminder"))return;
  event.preventDefault();const revision=requestRevision;const data=Object.fromEntries(new FormData(form));
  const button=form.querySelector('button:not([type="button"])');if(button)button.disabled=true;
  try{
    if(form.id==="resume-import-form"){
      const file=form.elements.resume_file.files[0];if(!file)throw new Error(uiText("h.chooseFile"));if(file.size>1024*1024)throw new Error(uiText("h.fileLarge"));
      const response=await fetch(`/api/v1/resume-imports?filename=${encodeURIComponent(file.name)}`,{method:"POST",headers:{Authorization:`Bearer ${token}`,"Accept-Language":uiLocale},body:await file.arrayBuffer()});
      if(!response.ok)throw new Error((await response.json()).message);const preview=await response.json();if(revision!==requestRevision)return;
      hiringState.importPreview=preview;
      document.querySelector("#resume-import-preview").innerHTML=`<p>${recruitingFixed(preview.notice)}</p><form id="resume-import-confirm">${preview.candidates.map((c,i)=>`<fieldset data-import-index="${i}"><legend>${uiFormat("h.importLine",{number:i+1})}</legend><p>${uiOriginal(c.text)}</p><label class="job-choice"><input type="checkbox" name="selected" ${c.section_heading?"":"checked"}> ${uiText("h.includeStatement")}</label><label>${uiText("h.importSection")}<select name="kind">${hiringOptions(Object.entries(factLabels),c.suggested_kind,false)}</select></label><label>${uiText("h.checkedStatement")}<textarea name="text" maxlength="2000" required>${escapeHTML(c.text)}</textarea></label></fieldset>`).join("")}<label><input type="checkbox" required> ${uiText("h.confirmImport")}</label><button>${uiText("h.importFacts")}</button></form>`;
      const panel=document.querySelector("#resume-import-preview");recruitingFields(panel);panel.querySelector("legend")?.setAttribute("tabindex","-1");panel.querySelector("legend")?.focus();
      return;
    }
    if(form.id==="resume-import-confirm"){
      const p=hiringState.importPreview,claims=Array.from(form.querySelectorAll("[data-import-index]")).filter(row=>row.querySelector('[name="selected"]').checked).map(row=>{const original=p.candidates[Number(row.dataset.importIndex)];return {kind:row.querySelector('[name="kind"]').value,text:row.querySelector('[name="text"]').value,start:original.start,end:original.end,category:"self_report"};});
      if(!claims.length)throw new Error(uiText("h.chooseStatement"));
      await api(`/resume-imports/${p.material.id}/confirm`,hiringJson("POST",{claims,confirmed:true},p.material.version));
    }
    if(form.id==="fact-form"){
      let body={kind:data.kind,source_type:data.source_type,confirmed:true};
      if(data.source_type==="manual")body.text=data.text;
      if(data.source_type==="memory"){body.source_id=data.source_id;body.source_version=hiringState.memories.find(m=>m.id===data.source_id)?.version;}
      if(data.source_type==="profile"){body.profile_field=data.profile_field;body.source_version=hiringState.profile.version;}
      await api("/resume-facts",hiringJson("POST",body));
    }
    if(form.matches(".revoke-fact"))await api(`/resume-facts/${form.dataset.id}/revoke`,hiringJson("POST",{reason:data.reason,confirmed:true}));
    if(form.id==="resume-form"){
      const body={fact_ids:new FormData(form).getAll("fact_ids"),job_id:data.job_id||null,confirmed:true};
      if(!data.resume_id){body.title=data.title;await api("/resumes",hiringJson("POST",body,undefined,true));}
      else await api(`/resumes/${data.resume_id}/versions`,hiringJson("POST",body,hiringState.roots.find(r=>r.id===data.resume_id).version));
    }
    if(form.id==="application-form")await api("/applications",hiringJson("POST",{job_id:data.job_id,resume_version_id:data.resume_version_id,confirmed:true},undefined,true));
    if(form.id==="material-draft")await api(`/resume-versions/${hiringState.inspected.id}/drafts`,hiringJson("POST",{kind:data.kind,tone:data.tone,confirmed:true}));
    if(form.matches(".application-transition")){
      const a=hiringState.applications.find(a=>a.id===form.dataset.id);const body={target:data.target,note:data.note,confirmed:true};
      if(data.target==="prepared"){const keys=["facts","contact","requirements","privacy","source_reviewed"];if(keys.some(k=>!data[k]))throw new Error(uiText("h.completeChecklist"));body.checklist=Object.fromEntries(keys.map(k=>[k,true]));}
      await api(`/applications/${a.id}/transition`,hiringJson("POST",body,a.version));
    }
    if(form.matches(".application-receipt")){
      const a=hiringState.applications.find(a=>a.id===form.dataset.id);const body={note:data.note,confirmed:true};
      if(data.material_id){if(!data.submitted_at||!data.receipt_code)throw new Error(uiText("h.receiptFields"));body.material_id=data.material_id;body.material_version=hiringState.materials.find(m=>m.id===data.material_id).version;body.receipt_code=data.receipt_code;const moment=new Date(data.submitted_at);const parts=data.submitted_at.split(/[-T:]/).map(Number);if(!Number.isFinite(moment.getTime())||[moment.getFullYear(),moment.getMonth()+1,moment.getDate(),moment.getHours(),moment.getMinutes()].some((v,i)=>v!==parts[i]))throw new Error(uiText("h.invalidReceiptTime"));body.submitted_at=moment.toISOString();}
      await api(`/applications/${a.id}/receipt`,hiringJson("POST",body,a.version));
    }
    if(form.matches(".application-reminder"))await api(`/applications/${form.dataset.id}/reminders`,hiringJson("POST",{...data,quiet_start:Number(data.quiet_start),quiet_end:Number(data.quiet_end),confirmed:true},undefined,true));
    await refreshHiring(revision,"h.saved");
  }catch(e){if(revision===requestRevision)message(e.message,true);}finally{if(button)button.disabled=false;}
});

document.addEventListener("click",async event=>{
  if(current!=="recruiting")return;
  const anchor=event.target.closest("[data-hiring-anchor]");if(anchor){event.preventDefault();document.getElementById(anchor.dataset.hiringAnchor)?.querySelector("h2,h3")?.focus();return;}
  const button=event.target.closest("button");if(!button)return;const d=button.dataset,revision=requestRevision;
  if(!["copyFact","inspectResume","restoreResume","resumeExport","appHistory","saveReceipt","reminderAction","hiringRead"].some(k=>k in d))return;
  button.disabled=true;
  try{
    if(d.copyFact){const f=hiringState.facts.find(f=>f.id===d.copyFact);const form=document.querySelector("#fact-form");form.closest("details").open=true;form.elements.kind.value=f.kind;form.elements.source_type.value="manual";form.elements.text.value=f.text;form.elements.confirmed.checked=false;form.elements.source_type.dispatchEvent(new Event("change",{bubbles:true}));form.scrollIntoView();form.elements.text.focus();}
    if(d.inspectResume)await inspectResume(d.inspectResume,revision);
    if(d.restoreResume){const root=hiringState.roots.find(r=>r.id===hiringState.versions.find(v=>v.id===d.restoreResume).resume_id);await api(`/resumes/${root.id}/restore`,hiringJson("POST",{version_id:d.restoreResume,confirmed:true},root.version));await refreshHiring(revision,"h.restored");}
    if(d.resumeExport){const v=hiringState.inspected;const response=await fetch(`/api/v1/resume-versions/${v.id}/export/${d.resumeExport}`,{method:"POST",headers:{Authorization:`Bearer ${token}`,"Accept-Language":uiLocale}});if(!response.ok)throw new Error((await response.json()).message);const blob=await response.blob();if(revision!==requestRevision)return;const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=response.headers.get("Content-Disposition").match(/filename="([^"]+)"/)[1];a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message(uiFormat("h.exported",{sequence:v.sequence}));}
    if(d.appHistory){const history=await api(`/applications/${d.appHistory}/history`);if(revision===requestRevision){const panel=button.closest(".application-card").querySelector(".app-history");panel.innerHTML=`<h4>${uiText("h.historyTitle")}</h4>${history.map(h=>`<p>${escapeHTML(hiringTimestamp(h.payload.last_event.at))} · ${applicationLabels[h.payload.status]} · ${uiOriginal(h.payload.last_event.note)}</p>`).join("")}`;recruitingFields(panel);panel.querySelector("h4").focus();}}
    if(d.saveReceipt){const form=button.closest("form"),text=form.elements.receipt_text.value;if(!text.trim())throw new Error(uiText("h.pasteReceipt"));const response=await fetch("/api/v1/materials?filename=application-receipt.txt",{method:"POST",headers:{Authorization:`Bearer ${token}`,"Accept-Language":uiLocale,"Content-Type":"text/plain"},body:text});if(!response.ok)throw new Error((await response.json()).message);const material=await response.json();if(revision!==requestRevision)return;hiringState.materials.push(material);form.elements.material_id.insertAdjacentHTML("beforeend",hiringOptions([[material.id,material.filename]],material.id));form.elements.material_id.value=material.id;message(uiText("h.receiptSaved"));}
    if(d.reminderAction){const r=hiringState.reminders.find(r=>r.id===d.reminderAction);const body=Object.fromEntries(["kind","local_time","timezone","quiet_start","quiet_end"].map(k=>[k,r[k]]));await api(`/application-reminders/${r.id}`,hiringJson("PUT",{...body,status:d.status,confirmed:true},r.version));await refreshHiring(revision,"h.reminderUpdated");}
    if(d.hiringRead){const n=hiringState.notices.find(n=>n.id===d.hiringRead);await api(`/application-notifications/${n.id}/read`,hiringJson("POST",{},n.version));if(revision===requestRevision)await render();}
  }catch(e){if(revision===requestRevision)message(e.message,true);}finally{button.disabled=false;}
});

document.addEventListener("click",async event=>{
  if(current!=="recruiting"||event.target.id!=="tailor-resume")return;
  const button=event.target;button.disabled=true;
  const revision=requestRevision,form=document.querySelector("#resume-form");
  try{const root=hiringState.roots.find(r=>r.id===form.elements.resume_id.value);if(!root||!form.elements.job_id.value)throw new Error(uiText("h.chooseTailor"));await api(`/resumes/${root.id}/tailor`,hiringJson("POST",{job_id:form.elements.job_id.value,confirmed:true},root.version));await refreshHiring(revision,"h.tailored");}catch(e){if(revision===requestRevision)message(e.message,true);}finally{button.disabled=false;}
});
