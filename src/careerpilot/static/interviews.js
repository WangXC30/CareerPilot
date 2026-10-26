"use strict";
let interviewState={};
const interviewLabels=uiLabels("i.status","running completed abandoned".split(" "));
const questionLabels=uiLabels("i.kind","foundation open_foundation project scenario behavior followup".split(" "));
const dimensionLabels=uiLabels("i.dimension","correctness completeness expression project_evidence".split(" "));
const interviewBody=(body,version,key=false)=>hiringJson("POST",body,version,key);
function interviewReferences(refs){return (refs||[]).map(r=>`<p>${textValue({job:uiText("i.ref.job"),catalogue_skill:uiText("i.ref.catalogue_skill"),resume_fact:uiText("i.ref.resume_fact"),interview_answer:uiText("i.ref.interview_answer")}[r.type]||uiText("i.reference"))}${r.version?` · v${r.version}`:""}${r.quote?`<br>${uiOriginal(r.quote)}`:""}${r.url?`<br><a class="text-button" href="${textValue(r.url)}" target="_blank" rel="noopener noreferrer">${uiText("i.officialMaterial")}</a>`:""}</p>`).join("");}

async function mountInterviews(revision){
  if(revision!==requestRevision||current!=="recruiting")return;
  if(interviewState.ownerToken!==token)interviewState={ownerToken:token};
  view.insertAdjacentHTML("beforeend",`<section class="card growth-detail" id="interview-workspace"><h2>${uiText("i.title")}</h2><p role="status">${uiText("i.loading")}</p></section>`);
  const panel=document.querySelector("#interview-workspace");
  try{
    const [sessions,plans]=await Promise.all([api("/interviews"),api("/plans")]);
    if(revision!==requestRevision)return;
    interviewState.sessions=sessions;interviewState.plans=plans;
    panel.innerHTML=`<h2>${uiText("i.title")}</h2><p>${uiText("i.help")}</p><details id="interview-create"><summary>${uiText("i.start")}</summary><form id="interview-create-form" class="form-grid"><label>${uiText("i.targetJob")}<select name="job_id" required>${hiringOptions(hiringState.jobs.map(j=>[j.id,j.fields.title.value||uiText("h.unknownJob")]))}</select></label><label>${uiText("i.resume")}<select name="root_id" required><option value="">${uiText("i.chooseResume")}</option>${hiringOptions(hiringState.roots.map(r=>[r.id,r.title]))}</select></label><label class="full">${uiText("i.immutableVersion")}<select name="resume_version_id" required><option value="">${uiText("i.resumeFirst")}</option></select></label><fieldset class="full" id="interview-project-facts"><legend>${uiText("i.projectFacts")}</legend><p>${uiText("i.projectFactsHelp")}</p></fieldset><label class="full"><input type="checkbox" required> ${uiText("i.confirmContext")}</label><button>${uiText("i.create")}</button></form></details><div id="interview-sessions">${sessions.map(s=>`<div class="list-row"><div><strong>${s.job.title?uiOriginal(s.job.title):uiText("h.unknownJob")} · ${uiFormat("h.resumeVersion",{sequence:s.resume_sequence})}</strong><p>${interviewLabels[s.status]} · ${uiFormat("i.answered",{count:s.answered_count})}</p></div><button data-open-interview="${s.id}" class="secondary">${s.status==="running"?uiText("i.continue"):uiText("i.inspect")}</button></div>`).join("")||`<p>${uiText("i.noSessions")}</p>`}</div><div id="interview-detail" aria-live="polite"></div>`;
    recruitingFields(panel);
    if(interviewState.lastSessionId&&sessions.some(s=>s.id===interviewState.lastSessionId))await openInterview(interviewState.lastSessionId,revision,false);
  }catch(e){if(revision===requestRevision)panel.innerHTML=`<h2>${uiText("i.title")}</h2><p>${textValue(e.message)}</p>`;}
}

async function chooseInterviewVersion(id,revision=requestRevision){
  const selection=(interviewState.versionRequest||0)+1;interviewState.versionRequest=selection;interviewState.resumeVersion=null;
  const projectPanel=document.querySelector("#interview-project-facts");projectPanel.innerHTML=`<legend>${uiText("i.projectFacts")}</legend><p role="status">${uiText("h.selectionLoading")}</p>`;
  if(!id)return;
  let version;try{version=await api(`/resume-versions/${id}`);}catch(e){if(revision===requestRevision&&interviewState.versionRequest===selection)throw e;return;}
  if(revision!==requestRevision||interviewState.versionRequest!==selection)return;
  const form=document.querySelector("#interview-create-form");if(!form)return;
  interviewState.resumeVersion=version;
  if(version.job)form.elements.job_id.value=version.job.id;
  const facts=version.facts.filter(f=>["project","experience"].includes(f.kind));
  form.querySelector("#interview-project-facts").innerHTML=`<legend>${uiText("i.projectFacts")}</legend>${facts.map(f=>`<label class="job-choice"><input type="checkbox" name="fact_ids" value="${f.id}"> ${uiOriginal(f.text)}</label>`).join("")||`<p>${uiText("i.noProjectFacts")}</p>`}<p>${version.source_status.valid?uiText("i.validSources"):uiText("i.invalidSources")}</p>`;
}

async function openInterview(id,revision=requestRevision,scroll=true){
  const selection=(interviewState.openRequest||0)+1;interviewState.openRequest=selection;
  interviewState.session=null;interviewState.evaluation=null;interviewState.review=null;
  document.querySelector("#interview-detail").innerHTML=`<p role="status">${uiText("i.loading")}</p>`;
  try{
  const s=await api(`/interviews/${id}`);
  if(revision!==requestRevision||interviewState.openRequest!==selection)return;
  interviewState.session=s;interviewState.lastSessionId=id;
  const panel=document.querySelector("#interview-detail");if(!panel)return;
  const q=s.current_question;
  panel.innerHTML=`<div class="growth-detail"><h3>${interviewLabels[s.status]} · ${s.job.title?uiOriginal(s.job.title):uiText("i.jobPractice")}</h3><p>${uiFormat("i.progress",{sequence:s.resume_sequence,answered:s.answered_count,total:s.question_count})}${s.status==="running"?` (${uiText("i.followupsMayGrow")})`:""}</p><p>${recruitingFixed(s.notice)}</p>${q?`<article class="resume-preview"><span class="tag">${questionLabels[q.kind]}</span><h3 class="question-prompt">${uiOriginal(q.prompt)}</h3><details><summary>${uiText("i.questionBasis")}</summary>${interviewReferences(q.references)}</details><form id="interview-answer-form" data-question-id="${q.id}">${q.choices.length?`<fieldset><legend>${uiText("i.chooseAnswer")}</legend>${q.choices.map((c,i)=>`<label class="quiz-choice"><input type="radio" name="choice" value="${i}"> ${uiSource(c)}</label>`).join("")}</fieldset>`:""}<label>${uiText("i.answer")}<textarea name="text" maxlength="6000" placeholder="${uiText("i.answerPlaceholder")}"></textarea></label><label><input type="checkbox" name="skipped"> ${uiText("i.skip")}</label><div class="discovery-actions"><button>${uiText("i.submitAnswer")}</button><button type="button" data-refresh-interview="${s.id}" class="secondary">${uiText("i.refresh")}</button></div></form><details><summary>${uiText("i.endTitle")}</summary><p>${uiText("i.endHelp")}</p><button data-abandon-interview="${s.id}" class="secondary">${uiText("i.end")}</button></details></article>`:""}<details><summary>${uiText("i.savedAnswers")}</summary>${s.answers.map(a=>{const question=s.answered_questions.find(q=>q.id===a.question_id);return `<div class="evidence-row"><strong>${uiOriginal(question?.prompt)}</strong><p>${a.skipped?uiText("i.skipped"):a.choice!==null?uiSource(question?.choices[a.choice]):uiOriginal(a.text)}</p><details><summary>${uiText("i.originalReferences")}</summary>${interviewReferences(question?.references)}</details></div>`;}).join("")||`<p>${uiText("i.noAnswers")}</p>`}</details><div id="interview-evaluation"></div></div>`;
  if(s.status==="completed"){
    const e=await api(`/interviews/${s.id}/evaluate`,interviewBody({confirmed:true},s.version));
    if(revision!==requestRevision||interviewState.openRequest!==selection)return;
    const reviews=await api("/interview-reviews");
    if(revision!==requestRevision||interviewState.openRequest!==selection)return;
    interviewState.evaluation=e;interviewState.review=reviews.find(r=>r.evaluation_id===e.id);
    showInterviewEvaluation(e);
  }
  recruitingFields(panel);
  if(scroll){focusLearningResult(panel);panel.scrollIntoView({block:"start"});}
  }catch(e){if(revision===requestRevision&&interviewState.openRequest===selection)throw e;}
}

function showInterviewEvaluation(e){
  const r=interviewState.review;
  const selections=r?.decisions||e.suggested_review;
  const options=e.suggested_review.map(d=>({...d,...selections.find(s=>s.skill_id===d.skill_id)}));
  const plans=interviewState.plans.filter(p=>p.status==="active");
  document.querySelector("#interview-evaluation").innerHTML=`<div class="growth-detail"><h3>${uiText("i.evaluationTitle")}</h3><p>${uiText("i.evaluationHelp")}</p><div class="grid two">${Object.keys(dimensionLabels).map(key=>[key,e.dimensions[key]]).map(([key,d])=>`<div class="evidence-row"><strong>${dimensionLabels[key]}：${d.score===null?uiText("i.toCheck"):`${d.score} / ${d.max_score}`}</strong><p>${recruitingFixed(d.basis)}${d.total_questions?`<br>${uiFormat("i.coverage",{count:d.evaluated_questions,total:d.total_questions})}`:""}</p></div>`).join("")}</div>${e.feedback.map(f=>`<details><summary>${uiOriginal(interviewState.session.answered_questions.find(q=>q.id===f.question_id)?.prompt)}</summary><p>${f.explanation?uiSource(f.explanation):recruitingFixed(f.suggestion)}</p>${f.explanation?`<p>${recruitingFixed(f.suggestion)}</p>`:""}${f.spans.map(s=>`<p>${uiText("i.structureBasis")}：${recruitingFixed(s.label)} — ${uiOriginal(s.quote)}</p>`).join("")}</details>`).join("")}<h3>${uiText("i.reviewTitle")}</h3><p>${uiText("i.reviewHelp")}</p><form id="interview-review-form"><label>${uiText("i.summary")}<textarea name="summary" required maxlength="4000">${textValue(r?.summary||uiText("i.defaultSummary"))}</textarea></label>${options.map((d,i)=>`<fieldset data-review-index="${i}"><legend>${textValue(d.skill_id)} · ${textValue(questionLabels[interviewState.session.answered_questions.find(q=>q.id===d.question_id)?.kind]||uiText("i.reviewQuestion"))}</legend><label><input type="checkbox" name="included" ${!r||selections.some(s=>s.skill_id===d.skill_id)?"checked":""}> ${uiText("i.include")}</label><label>${uiText("i.minutes")}<input type="number" name="minutes" min="10" max="180" value="${d.minutes}" required></label><label>${uiText("i.reviewGoal")}<textarea name="note" maxlength="2000">${textValue(d.note)}</textarea></label></fieldset>`).join("")||`<p>${uiText("i.noReviewTopics")}</p>`}<label><input type="checkbox" required> ${uiText("i.confirmReview")}</label><div class="discovery-actions"><button name="status" value="confirmed">${uiText("i.saveReview")}</button>${r?`<button name="status" value="withdrawn" class="secondary">${uiText("i.withdrawReview")}</button>`:""}</div></form>${r?`<p>${uiFormat("i.reviewVersion",{version:r.version})} · ${r.status==="confirmed"?uiText("i.confirmed"):uiText("i.withdrawn")}</p><form id="interview-preview-form"><label>${uiText("i.targetPlan")}<select name="plan_id" required>${hiringOptions(plans.map(p=>[p.id,uiFormat("i.activePlan",{version:p.version})]),"",false)}</select></label>${plans.length?"":`<p>${uiText("i.publishFirst")}</p>`}<button ${r.status!=="confirmed"||!plans.length?"disabled":""}>${uiText("i.previewReview")}</button></form>`:""}<div id="interview-plan-preview"></div></div>`;
  recruitingFields(document.querySelector("#interview-evaluation"));
  interviewState.reviewOptions=options;
}

document.addEventListener("change",async event=>{
  if(current!=="recruiting")return;const revision=requestRevision;
  try{
    if(event.target.matches('#interview-create-form [name="root_id"]')){
      const id=event.target.value;const selection=(interviewState.rootRequest||0)+1;interviewState.rootRequest=selection;
      interviewState.versionRequest=(interviewState.versionRequest||0)+1;interviewState.resumeVersion=null;
      const form=event.target.form;form.elements.resume_version_id.innerHTML=`<option value="">${uiText("i.resumeFirst")}</option>`;form.querySelector("#interview-project-facts").innerHTML=`<legend>${uiText("i.projectFacts")}</legend><p>${uiText("i.projectFactsHelp")}</p>`;
      if(!id)return;
      let versions;try{versions=await api(`/resumes/${id}/versions`);}catch(e){if(revision===requestRevision&&interviewState.rootRequest===selection)throw e;return;}
      if(revision!==requestRevision||event.target.value!==id||interviewState.rootRequest!==selection)return;
      const select=document.querySelector('#interview-create-form [name="resume_version_id"]');
      select.innerHTML=hiringOptions(versions.map(v=>[v.id,`${uiFormat("h.version",{sequence:v.sequence})} · ${jobName(v.job)}`]));
      if(versions[0])await chooseInterviewVersion(versions[0].id,revision);
    }
    if(event.target.matches('#interview-create-form [name="resume_version_id"]'))await chooseInterviewVersion(event.target.value,revision);
  }catch(e){if(revision===requestRevision)message(e.message,true);}
});

document.addEventListener("submit",async event=>{
  const form=event.target;if(!form.matches("#interview-create-form,#interview-answer-form,#interview-review-form,#interview-preview-form"))return;
  event.preventDefault();const revision=requestRevision,selection=interviewState.openRequest,sessionId=interviewState.session?.id,data=Object.fromEntries(new FormData(form)),button=event.submitter;
  if(button)button.disabled=true;
  try{
    if(form.id==="interview-create-form"){
      if(interviewState.resumeVersion?.id!==data.resume_version_id)throw new Error(uiText("h.selectionLoading"));
      const s=await api("/interviews",interviewBody({job_id:data.job_id,resume_version_id:data.resume_version_id,fact_ids:new FormData(form).getAll("fact_ids"),confirmed:true},undefined,true));
      if(revision!==requestRevision)return;interviewState.lastSessionId=s.id;await refreshHiring(revision,"i.created");
    }
    if(form.id==="interview-answer-form"){
      const s=interviewState.session;const skipped=Boolean(data.skipped);
      if(!skipped&&s.current_question.kind==="foundation"&&data.choice===undefined)throw new Error(uiText("i.chooseOrSkip"));
      if(!skipped&&s.current_question.kind!=="foundation"&&!data.text.trim())throw new Error(uiText("i.answerOrSkip"));
      await api(`/interviews/${s.id}/answers`,interviewBody({question_id:s.current_question.id,text:data.text,choice:data.choice===undefined?null:Number(data.choice),skipped},s.version));
      if(revision===requestRevision&&interviewState.openRequest===selection)await openInterview(s.id,revision,false);
    }
    if(form.id==="interview-review-form"){
      const decisions=Array.from(form.querySelectorAll("[data-review-index]")).filter(row=>row.querySelector('[name="included"]').checked).map(row=>{const d=interviewState.reviewOptions[Number(row.dataset.reviewIndex)];return {question_id:d.question_id,skill_id:d.skill_id,minutes:Number(row.querySelector('[name="minutes"]').value),note:row.querySelector('[name="note"]').value};});
      await api(`/interview-evaluations/${interviewState.evaluation.id}/review`,hiringJson("PUT",{status:button.value,summary:data.summary,decisions,confirmed:true},interviewState.review?.version||0));
      if(revision===requestRevision&&interviewState.openRequest===selection){await openInterview(sessionId,revision,false);if(revision===requestRevision&&interviewState.session?.id===sessionId)message(uiText("i.reviewSaved"));}
    }
    if(form.id==="interview-preview-form"){
      const r=interviewState.review;const p=await api(`/interview-reviews/${r.id}/plan-preview`,interviewBody({plan_id:data.plan_id,confirmed:true},r.version,true));
      if(revision===requestRevision&&interviewState.openRequest===selection&&interviewState.review?.id===r.id){const panel=document.querySelector("#interview-plan-preview");panel.innerHTML=previewHTML(p);focusLearningResult(panel);message(uiText("i.checkPreview"));}
    }
  }catch(e){if(revision===requestRevision&&interviewState.openRequest===selection)message(e.message,true);}finally{if(button)button.disabled=false;}
});

document.addEventListener("click",async event=>{
  if(current!=="recruiting")return;const button=event.target.closest("button");if(!button)return;
  const d=button.dataset,revision=requestRevision;
  if(!d.openInterview&&!d.refreshInterview&&!d.abandonInterview)return;button.disabled=true;
  try{
    if(d.openInterview||d.refreshInterview)await openInterview(d.openInterview||d.refreshInterview,revision);
    if(d.abandonInterview){const s=interviewState.session,selection=interviewState.openRequest;await api(`/interviews/${s.id}/abandon`,interviewBody({confirmed:true},s.version));if(revision===requestRevision&&interviewState.openRequest===selection)await render();}
  }catch(e){if(revision===requestRevision)message(e.message,true);}finally{button.disabled=false;}
});
