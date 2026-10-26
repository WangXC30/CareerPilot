"use strict";
let v1State={}, v1Session="";
const skillStatus={unassessed:"未评估",self_report:"用户自述",supported:"证据支持",verified:"基础测验已验证",needs_reassessment:"需复评"};
const taskStatus={pending:"待开始",running:"进行中",paused:"已暂停",completed:"已完成",skipped:"已跳过"};
const textValue=value=>escapeHTML(value??"");
const v1Json=(method,body,version)=>({method,body:JSON.stringify(body),headers:version===undefined?{}:{"If-Match":`"${version}"`}});
const selectedJobs=()=>[...document.querySelectorAll("input[name=job_ids]:checked")].map(e=>e.value);
const skillLabel=value=>uiText('skill.'+value);
const taskLabel=value=>uiText('task.'+value);
function jobChoices(jobs){return jobs.map(j=>`<label class="job-choice"><input name="job_ids" type="checkbox" value="${j.id}"> ${j.fields.title.value?uiOriginal(j.fields.title.value):uiText("jobToVerify")}</label>`).join("")||`<p>${uiText("noJobChoices")}</p>`;}

async function renderV1(route,revision){
  if(v1Session!==token){v1State={};v1Session=token;}
  if(!["learning","skills","dashboard"].includes(route))return false;
  if(!token){shellView(empty(uiText("signin.growth"),uiText("signin.growthText"),uiText("completeProfile"),"settings"));return true;}
  view.lang=uiLocale;
  view.innerHTML=`<div class="card" role="status">${uiText("growthLoading")}</div>`;
  try{
    if(route==="dashboard"){
      const [profile,skills,plans,goals,jobs]=await Promise.all([api("/profile"),api("/skills"),api("/plans"),api("/career-goals"),api("/jobs?limit=100")]);
      if(revision!==requestRevision)return true;
      const plan=plans.find(p=>p.status==="active"),goal=goals.find(g=>g.status==="active");
      v1State.dashboardPlan=plan;v1State.timezone=profile.timezone;
      view.innerHTML=`<div class="grid"><article class="card"><h2>${uiText("currentDirection")}</h2><p>${goal?uiOriginal(goal.title):uiText("noPrimaryGoal")}</p><a href="#explore">${uiText("viewGoals")}</a></article><article class="card"><h2>${uiText("cardEvidence")}</h2><div class="metric">${skills.filter(s=>s.status==="verified").length}</div><p>${uiText("basicEvidenceHelp")}</p><a href="#skills">${uiText("viewEvidence")}</a></article><article class="card"><h2>${uiText("learningActions")}</h2><p>${plan?uiFormat("taskCount",{done:plan.tasks.filter(t=>t.status==="completed").length,total:plan.tasks.length}):uiText(profile.version?"budgetPlan":"profileFirst")}</p><a href="#learning">${uiText("enterLearning")}</a></article></div><article class="card growth-detail"><h2>${uiText("assistantTitle")}</h2><p>${uiText("assistantHelp")}</p><form id="assistant-form"><label>${uiText("assistantIntent")}<select name="intent" aria-label="${uiText("assistantIntent")}">${["auto","progress","diagnose","plan","replan","resources"].map(k=>`<option value="${k}">${uiText("intent."+k)}</option>`).join("")}</select></label><details><summary>${uiText("assistantJobs")}</summary>${jobChoices(jobs)}</details><label>${uiText("assistantMessage")}<textarea name="message" aria-label="${uiText("assistantMessage")}" lang="" dir="auto" required maxlength="4000" placeholder="${uiText("assistantPlaceholder")}"></textarea></label><button type="submit">${uiText("assistantSubmit")}</button></form><div id="assistant-result" aria-live="polite"></div></article>`;
    }
    if(route==="skills"){
      const [skills,evidence,memories,jobs,conflicts,profile]=await Promise.all([api("/skills"),api("/skill-evidence"),api("/memories"),api("/jobs?limit=100"),api("/memory-conflicts"),api("/profile")]);
      if(revision!==requestRevision)return true;
      v1State.skills=skills;v1State.evidence=evidence;v1State.timezone=profile.timezone;
      view.innerHTML=`<article class="card"><h2>${uiText("skillsTitle")}</h2><p>${uiText("skillsHelp")}</p><div class="grid">${skills.map(s=>`<div class="evidence-row"><h3>${uiOriginal(s.name)}</h3><span class="tag">${skillLabel(s.status)}</span><p>${uiFormat("evidenceCount",{count:s.evidence_ids.length})}</p><button class="secondary" data-quiz="${s.skill_id}">${escapeHTML(uiFormat("quizStart",{name:s.name}))}</button></div>`).join("")}</div></article><div id="assessment-view"></div>
        <article class="card growth-detail"><h2>${uiText("gapTitle")}</h2><form id="gap-form">${jobChoices(jobs)}<button type="submit">${uiText("gapAnalyze")}</button></form><div id="gap-result"></div></article>
        <article class="card growth-detail"><h2>${uiText("evidenceLedger")}</h2><div id="evidence-ledger">${evidence.map(e=>`<details><summary>${uiOriginal(e.skill_id)} · ${e.kind==="self_report"?uiText("selfReport"):uiText("evidence."+e.kind)} · ${uiText(e.conclusion==="passed"?"passed":e.conclusion==="failed"?"failed":"evidence.learning_completion")}${e.revoked?" · "+uiText("revoked"):""}</summary><p>${uiText("evidenceSource")} ${uiOriginal(e.source_id)} · v${e.source_version}<br>${uiText("rubric")} ${e.rubric_version?uiOriginal(e.rubric_version):uiText("notApplicable")}<br>${uiText("evaluatedAt")} ${textValue(uiDate(e.created_at,v1State.timezone))} · ${uiText("validUntil")} ${textValue(uiDate(e.expires_at,v1State.timezone))}</p>${!e.revoked?`<button class="secondary" data-revoke-evidence="${e.id}">${uiText("revokeEvidence")}</button>`:""}</details>`).join("")||`<p>${uiText("noEvidence")}</p>`}</div></article>
        <article class="card growth-detail"><h2>${uiText("conflictTitle")}</h2><p>${uiText("conflictHelp")}</p>${conflicts.map(c=>`<div class="evidence-row"><h3>${uiOriginal(c.skill_id)}</h3>${c.memories.map((m,i)=>`<p>${uiOriginal(m.text)}${i===0?"":` <button class="secondary" data-resolve-preferred="${c.memories[0].id}" data-resolve-other="${m.id}" data-version="${m.version}">${uiText("preferFirst")}</button>`}</p>`).join("")}</div>`).join("")||`<p>${uiText("noConflicts")}</p>`}</article>
        <div class="section-title"><h2>${uiText("memoryTitle")}</h2><a href="#settings">${uiText("importMaterialLink")}</a></div><div class="growth-stack">${memoryCards(memories)}</div>`;
      if(v1State.exam)showAssessment(v1State.exam);
      mountSkillGraph(jobs);
    }
    if(route==="learning"){
      const [plans,jobs,resources,previews,profile]=await Promise.all([api("/plans"),api("/jobs?limit=100"),api("/learning-resources"),api("/plan-previews"),api("/profile")]);
      if(revision!==requestRevision)return true;
      v1State.plans=plans;v1State.resources=Object.fromEntries(resources.map(r=>[r.id,r]));v1State.timezone=profile.timezone;
      const plan=plans.find(p=>p.status==="active")||plans.find(p=>p.status==="draft");
      v1State.plan=plan;
      view.innerHTML=`<article class="card"><h2>${uiText("planGenerate")}</h2><p>${uiText("planHelp")}</p><form id="plan-form">${jobChoices(jobs)}<div class="form-grid"><label>${uiText("planStart")}<input type="date" name="start_date" required value="${uiCalendarDate(new Date(),profile.timezone||'Asia/Shanghai')}"></label><label>${uiText("planDeadline")}<input type="date" name="deadline"></label></div><button type="submit">${uiText("planDraft")}</button></form></article><div id="plan-view">${plan?planHTML(plan):`<div class="card growth-detail"><h2>${uiText("noPlan")}</h2><p>${uiText("noPlanHelp")}</p></div>`}</div>
        <article class="card growth-detail"><h2>${uiText("planAdjustments")}</h2><p>${uiText("planAdjustmentHelp")}</p><div id="plan-previews">${previews.filter(p=>p.status==="pending").map(previewHTML).join("")||`<p>${uiText("noAdjustments")}</p>`}</div></article>
        <article class="card growth-detail"><h2>${uiText("findResources")}</h2><form id="resource-search"><label>${uiText("keyword")}<input name="q" lang="" dir="auto" maxlength="200" placeholder="Python / SQL"></label><button type="submit">${uiText("searchResources")}</button></form><div id="resource-results"></div></article>`;
      await mountStudy(revision);
    }
  }catch(error){if(revision===requestRevision){shellView(empty(uiText("err.v1"),escapeHTML(error.message),uiText("completeProfile"),"settings"));message(error.message,true);}}
  return true;
}

function planHTML(plan){
  const tasks=plan.tasks.map(t=>`<article class="card task-card" data-task-id="${t.id}"><span class="tag">${taskLabel(t.status)}${t.locked?" · "+uiText("locked"):""}</span><h3>${uiSource(t.title)}</h3><p>${textValue(uiDate(t.date))} · ${t.minutes} ${uiText("minutes")} · ${uiOriginal(t.skill_id)}</p><p>${uiSource(t.definition_of_done)}<br>${uiText("verification")}：${uiText(t.verification.startsWith("objective_quiz")?"quizVerification":"recordVerification")}</p>${t.resources.map(studyResourceHTML).join("")}${t.outcome?`<p>${uiText("studyOutcome")}：${uiOriginal(t.outcome)}</p>`:""}${plan.status==="active"?`<div class="task-actions">${["pending","paused"].includes(t.status)?`<button data-task-action="start" data-task="${t.id}">${uiText("startTask")}</button>`:""}${t.status==="running"?`<button data-task-action="pause" data-task="${t.id}" class="secondary">${uiText("pauseTask")}</button><form class="complete-task-form" data-task="${t.id}"><label>${uiText("studyOutcome")}<textarea name="outcome" aria-label="${uiText("studyOutcome")}" lang="" dir="auto" required maxlength="4000"></textarea></label><button type="submit">${uiText("recordCompletion")}</button></form>`:""}<button class="secondary" data-task-action="${t.locked?"unlock":"lock"}" data-task="${t.id}">${uiText(t.locked?"unlock":"lock")}</button>${["pending","paused"].includes(t.status)?`<button class="secondary" data-task-action="skip" data-task="${t.id}">${uiText("skipTask")}</button><details><summary>${uiText("deferSplit")}</summary><form class="defer-task-form" data-task="${t.id}"><label>${uiText("newTaskDate")}<input name="date" type="date" required></label><button type="submit">${uiText("confirmDefer")}</button></form><form class="split-task-form" data-task="${t.id}"><label>${uiText("splitMinutes")}<input name="minutes" type="number" min="1" max="${t.minutes-1}" required></label><button type="submit">${uiText("splitTask")}</button></form></details>`:""}</div>`:""}</article>`).join("");
  return `<article class="card growth-detail"><h2 tabindex="-1">${uiText(plan.status==="active"?"activePlan":"draftPlan")} · v${plan.version}</h2><p>${uiText("sourceLanguageNote")}</p><p>${uiText("milestonesLabel")}：${plan.milestones.map(m=>uiSource(m)).join(" → ")}</p><p>${uiText("budgetLabel")}：${plan.validation.budget_minutes} ${uiText("minutes")}；${uiText("scheduledLabel")} ${Object.values(plan.validation.weekly_minutes).join(" / ")} ${uiText("minutes")}</p>${plan.status==="draft"?`<button id="publish-plan" data-plan="${plan.id}" data-version="${plan.version}">${uiText("publishPlan")}</button>`:`<button id="request-replan" data-plan="${plan.id}" class="secondary">${uiText("previewReplan")}</button>`}<button class="secondary" id="plan-history" data-plan="${plan.id}">${uiText("planHistory")}</button><div id="plan-history-view"></div></article><div class="growth-stack">${tasks}</div>`;
}

function previewHTML(p){return `<div class="growth-detail" lang="${uiLocale}" data-adjustment="${p.id}">${p.mode_change?`<p>${uiText("budgetLabel")}：${p.mode_change.before_budget} → ${p.mode_change.after_budget} ${uiText("minutes")}；${uiText("profileBudget")} ${p.mode_change.profile_budget} ${uiText("minutes")}。${uiText(p.mode_change.mode==="sprint"?"priorityCoverage":["light","research_busy"].includes(p.mode_change.mode)?"priorityShort":"priorityPrerequisite")}</p>`:""}<h3 tabindex="-1">${textValue(uiText('reason.'+p.reason))} · ${uiFormat("previewBase",{version:p.base_version})}</h3><p>${uiFormat("previewCounts",{added:p.diff.added.length,removed:p.diff.removed.length,changed:p.diff.changed.length})}</p><details><summary>${uiText("viewChanges")}</summary>${p.diff.added.map(t=>`<p>${uiText("added")}：${uiSource(t.title)} · ${textValue(uiDate(t.date))} · ${t.minutes} ${uiText("minutes")}</p>`).join("")}${p.diff.removed.map(t=>`<p>${uiText("removed")}：${uiSource(t.title)}</p>`).join("")}${p.diff.changed.map(t=>`<p>${uiSource(t.after.title)}：${textValue(uiDate(t.before.date))} → ${textValue(uiDate(t.after.date))}</p>`).join("")}</details>${p.stale?`<p>${uiText("staleAdjustment")}</p>`:p.validation_error?`<p role="alert">${p.validation_error.message_locale?textValue(p.validation_error.message):uiSource(p.validation_error.message)}</p>`:`<button data-decide-preview="accept" data-preview="${p.id}" data-version="${p.version}">${uiText("acceptAdjustment")}</button>`}<button class="secondary" data-decide-preview="reject" data-preview="${p.id}" data-version="${p.version}">${uiText("keepCurrentPlan")}</button></div>`;}

function showAssessment(exam){
  v1State.exam=exam;
  const container=document.querySelector("#assessment-view");if(!container)return;
  container.innerHTML=`<article class="card growth-detail" lang="${uiLocale}"><h2 tabindex="-1">${escapeHTML(uiFormat("quizTitle",{name:exam.skill_id}))}</h2><p>${uiFormat("quizHelp",{count:exam.questions.length})}</p><p>${uiText("sourceLanguageNote")}</p>${exam.status==="pending"?`<form id="assessment-form">${exam.questions.map((q,i)=>`<fieldset lang="zh-CN" data-source-content><legend>${i+1}. ${textValue(q.prompt)}</legend>${q.choices.map((choice,j)=>`<label class="quiz-choice"><input type="radio" name="q${i}" value="${j}" required> ${textValue(choice)}</label>`).join("")}</fieldset>`).join("")}<button type="submit">${uiText("quizSubmit")}</button></form>`:exam.status==="system_error"?`<p role="alert">${uiText("quizSystemError")}</p>`:`<p role="status">${uiText(exam.status==="passed"?"quizPassed":"quizFailed")} ${exam.result.score}/${exam.result.total}</p>${exam.result.feedback.map(f=>`<p>${f.correct?"✓":uiText("needsPractice")} ${uiSource(f.explanation)}</p>`).join("")}<a href="#learning">${uiText("quizPlanLink")}</a>`}</article>`;
  focusLearningResult(container);
}

async function enhanceV1Settings(revision){
  if(!token)return;
  await enhanceProfileHistory(revision);if(revision!==requestRevision)return;
  const consents=await api("/consents");if(revision!==requestRevision)return;
  view.insertAdjacentHTML("beforeend",`<article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("publicAccess")}</h2><p>${uiText("publicAccessHelp")}</p><label><input id="web-consent" type="checkbox" ${consents.some(c=>c.purpose==="public_web"&&c.granted)?"checked":""}> ${uiText("publicConsent")}</label><button id="save-web-consent" class="secondary">${uiText("savePublicConsent")}</button></article><article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("myData")}</h2><button id="export-account">${uiText("exportData")}</button><details><summary>${uiText("deleteAccount")}</summary><p>${uiText("deleteAccountHelp")}</p><form id="delete-account-form"><label>${uiText("deleteConfirmation")}<input name="confirmation" required autocomplete="off"></label><button type="submit">${uiText("confirmDeleteAccount")}</button></form></details></article>`);
}

async function enhanceV1Explore(revision){
  if(!token)return;const careers=await api("/catalogue/career");if(revision!==requestRevision)return;
  view.insertAdjacentHTML("afterbegin",`<div class="grid" lang="${uiLocale}">${careers.map(c=>`<article class="card"><small>${uiText("careerEditorial")}${c.stale?" · "+uiText("updatePending"):""}</small><h2>${uiOriginal(c.title)}</h2><p>${c.duties.map(uiOriginal).join("; ")}</p><p>${uiText("typicalTasks")}：${c.typical_tasks.map(uiOriginal).join("; ")}</p><p>${uiOriginal(c.entry_requirements)}</p><a href="${textValue(c.source_url)}" target="_blank" rel="noopener noreferrer">${uiText("careerSource")}</a><p>${uiText("reviewDate")}：${textValue(uiDate(c.reviewed_at.slice(0,10)))} · ${uiText("generalReference")}</p><button class="secondary" data-career-title="${textValue(c.title)}">${uiText("useGoalDraft")}</button></article>`).join("")}</div>`);
  const [goals,milestones]=await Promise.all([api("/career-goals?limit=100"),api("/goal-milestones")]);if(revision!==requestRevision)return;
  view.insertAdjacentHTML("beforeend",`<article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("milestonesTitle")}</h2>${goals.length?`<form id="milestone-form" class="form-grid"><label>${uiText("milestoneGoal")}<select name="goal_id" aria-label="${uiText("milestoneGoal")}">${goals.map(g=>`<option value="${g.id}" lang="" data-original-content>${textValue(g.title)}</option>`).join("")}</select></label><label>${uiText("milestoneOutcome")}<input name="title" lang="" dir="auto" required maxlength="200"></label><label>${uiText("milestoneDate")}<input name="target_date" type="date" required></label><button type="submit">${uiText("milestoneAdd")}</button></form>`:`<p>${uiText("milestoneNeedsGoal")}</p>`}${milestones.map(m=>`<div class="list-row"><span>${uiOriginal(m.title)} · ${textValue(uiDate(m.target_date))} · ${uiText(m.completed?"task.completed":"milestonePending")}</span><button class="secondary" data-milestone="${m.id}" data-version="${m.version}" data-completed="${m.completed}">${uiText(m.completed?"milestoneReset":"milestoneComplete")}</button></div>`).join("")}</article>`);
}

async function enhanceV1Market(revision){
  if(!token)return;const filters=await api("/job-filters");if(revision!==requestRevision)return;
  v1State.filters=filters;
  const form=document.querySelector("#job-search");
  form.insertAdjacentHTML("beforeend",`<label>${uiText("filterCompany")}<input name="company" lang="" dir="auto"></label><label>${uiText("filterEducation")}<input name="education" lang="" dir="auto"></label><label>${uiText("filterCohort")}<input name="graduation_cohort" lang="" dir="auto"></label><label>${uiText("filterEmployment")}<input name="employment_type" lang="" dir="auto"></label><label>${uiText("filterDuration")}<input name="duration" lang="" dir="auto"></label><label>${uiText("salaryMinimum")}<input name="salary_min" type="number" min="0"></label><label>${uiText("salaryUnit")}<select name="salary_unit" aria-label="${uiText("salaryUnit")}"><option value="month">${uiText("cnyMonth")}</option><option value="day">${uiText("cnyDay")}</option></select></label><label>${uiText("deadlineBefore")}<input name="deadline_before" type="date"></label><label>${uiText("sort")}<select name="descending" aria-label="${uiText("sort")}"><option value="true">${uiText("newestFirst")}</option><option value="false">${uiText("oldestFirst")}</option></select></label><label>${uiText("filterName")}<input name="filter_name" lang="" dir="auto" maxlength="100"></label><button type="button" id="save-job-filter" class="secondary">${uiText("saveFilter")}</button><button type="button" id="next-jobs" class="secondary">${uiText("nextPage")}</button>`);
  form.insertAdjacentHTML("afterend",`<div id="saved-filters">${filters.map(f=>`<button class="secondary" data-use-filter="${f.id}">${uiOriginal(f.name)}</button>`).join("")}</div>`);
}

function searchValues(){const values=Object.fromEntries(new FormData(document.querySelector("#job-search"))),{filter_name,descending,...filters}=values;filters.salary_min=filters.salary_min===""?null:Number(filters.salary_min);filters.deadline_before=filters.deadline_before||null;return {filters,name:filter_name,descending};}
function enhanceV1Job(job){
  const others=(growthState.jobs||[]).filter(j=>j.id!==job.id);
  document.querySelector("#job-detail").insertAdjacentHTML("beforeend",`<article class="card growth-detail"><h3>${uiText("checkJobStatus")}</h3><form class="job-status-form"><label>${uiText("jobStatus")}<select name="status" aria-label="${uiText("jobStatus")}">${["unverified","verified","possibly_expired","closed"].map(key=>`<option value="${key}" ${job.status===key?"selected":""}>${uiText("jobStatus."+key)}</option>`).join("")}</select></label><label>${uiText("verificationReason")}<input name="reason" lang="" dir="auto" required maxlength="200"></label><button type="submit">${uiText("saveJobStatus")}</button></form>${others.length?`<details><summary>${uiText("mergeJobs")}</summary><p>${uiText("mergeJobsHelp")}</p><form class="merge-job-form"><label>${uiText("mergeSource")}<select name="source_id" aria-label="${uiText("mergeSource")}">${others.map(j=>`<option value="${j.id}" lang="">${textValue(j.fields.title.value||uiText("jobToVerify"))} · ${j.id.slice(0,8)}</option>`).join("")}</select></label><button type="submit">${uiText("confirmMerge")}</button></form></details>`:""}${(job.merged_records||[]).map(record=>`<details><summary>${uiFormat("mergedOriginal",{id:record.id.slice(0,8),version:record.version})}</summary><pre lang="" dir="auto" data-original-content>${textValue(record.content)}</pre></details>`).join("")}</article>`);
}
async function doJobSearch(cursor){const values=searchValues();const rows=await api(`/job-search?descending=${values.descending}${cursor?`&cursor=${cursor}`:""}`,v1Json("POST",values.filters));if(current==="market"){showJobList(rows);v1State.lastCursor=rows.at(-1)?.id;}}

document.addEventListener("submit",async event=>{
  const form=event.target;
  if(!["login-form","plan-form","gap-form","assessment-form","assistant-form","resource-search","delete-account-form","job-search"].includes(form.id)&&!form.matches(".complete-task-form,.defer-task-form,.split-task-form,.job-status-form,.merge-job-form"))return;
  event.preventDefault();event.stopImmediatePropagation();const revision=requestRevision,button=event.submitter;
  if(button)button.disabled=true;
  try{
    const values=Object.fromEntries(new FormData(form));
    if(form.id==="login-form"){
      const registration=button.value==="register";
      if(registration&&(!values.privacy||!values.display_name.trim()))throw new Error(uiText("registerNeeds"));
      const body={email:values.email,password:values.password,...(registration?{display_name:values.display_name,privacy_accepted:true}:{})};
      const response=await fetch(`/api/v1/auth/${registration?"register":"login"}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw new Error(result.message||uiText("loginFailed"));
      token=result.access_token;form.reset();document.querySelector("#login-error").textContent="";document.querySelector("#account-dialog").close();document.querySelector("#connect").textContent=uiText("connected");await render();message(uiText(registration?"registered":"loggedIn"));
    }else if(form.id==="plan-form"){
      const plan=await api("/plans",v1Json("POST",{job_ids:selectedJobs(),start_date:values.start_date,deadline:values.deadline||null}));if(revision!==requestRevision)return;await render();if(requestRevision!==revision+1)return;if(current==="learning"){v1State.plan=plan;document.querySelector("#plan-view").innerHTML=planHTML(plan);focusLearningResult(document.querySelector("#plan-view"));}message(uiText("planCreated"));
    }else if(form.id==="gap-form"){
      const result=await api("/gap-analysis",v1Json("POST",{job_ids:selectedJobs()}));if(revision!==requestRevision)return;
      document.querySelector("#gap-result").innerHTML=`<h3 tabindex="-1">${uiText("gapTitle")}</h3>`+result.skills.map(s=>`<div class="evidence-row"><strong>${uiOriginal(s.name)}</strong> · ${skillLabel(s.status)}<p>${uiFormat("gapFactors",{priority:s.priority,frequency:s.factors.job_frequency,minutes:s.factors.estimated_minutes})}</p>${s.sources.map(source=>source.job_id?`<p>JD ${source.job_id.slice(0,8)} · ${uiText("originalQuote")}${uiOriginal(source.evidence.quote)}</p>`:source.career_id?`<p>${uiText("careerBasis")}</p>`:`<p>${uiText("prerequisiteFor")}：${uiOriginal(source.prerequisite_of)}</p>`).join("")}<p>${uiText("existingEvidence")}：${s.evidence_ids.map(uiOriginal).join(", ")||uiText("none")}</p></div>`).join("");focusLearningResult(document.querySelector("#gap-result"));
    }else if(form.id==="assessment-form"){
      const exam=v1State.exam,answers=exam.questions.map((_,i)=>Number(values[`q${i}`]));
      const result=await api(`/assessments/${exam.id}/submit`,v1Json("POST",{answers},exam.version));if(revision!==requestRevision)return;v1State.exam=result;await render();if(requestRevision!==revision+1)return;showAssessment(result);
    }else if(form.matches(".complete-task-form,.defer-task-form,.split-task-form")){
      const plan=v1State.plan;const action=form.matches(".complete-task-form")?"complete":form.matches(".defer-task-form")?"defer":"split";
      if(action==="split")values.minutes=Number(values.minutes);
      await api(`/plans/${plan.id}/tasks/${form.dataset.task}`,v1Json("POST",{action,...values},plan.version));if(revision!==requestRevision)return;await render();message(uiText("taskUpdated"));
    }else if(form.id==="assistant-form"){
      const generation=++assistantPollGeneration;
      const run=await api("/assistant/runs",{...v1Json("POST",{message:values.message,intent:values.intent,job_ids:selectedJobs(),plan_id:v1State.dashboardPlan?.id||null}),headers:{"Idempotency-Key":crypto.randomUUID()}});
      if(revision!==requestRevision||generation!==assistantPollGeneration)return;
      document.querySelector("#assistant-result").textContent=uiText("assistantQueued");
      pollAssistant(run.id,revision,generation);
    }else if(form.id==="resource-search"){
      const result=await api(`/web/search?q=${encodeURIComponent(values.q)}`);if(revision!==requestRevision)return;
      document.querySelector("#resource-results").innerHTML=`<p>${textValue(result.notice)}</p>`+result.results.map(r=>`<div class="evidence-row"><a href="${textValue(r.url)}" target="${r.url.startsWith("/")?"_self":"_blank"}" rel="noopener noreferrer">${uiOriginal(r.title)}</a><p>${textValue(r.language)} · ${textValue(uiDate(r.reviewed_at.slice(0,10)))} · ${uiText(r.status==="available"?"registeredSource":"unverifiedSource")}</p>${r.local_fallback?"":`<button class="secondary" data-fetch-source="${textValue(r.url)}">${uiText("fetchExcerpt")}</button>`}</div>`).join("")+`<div id="source-excerpt"></div>`;
    }else if(form.id==="delete-account-form"){
      const result=await api("/account/deletion",v1Json("POST",{confirmation:values.confirmation}));token="";document.querySelector("#connect").textContent=uiText("connect");await render();message(uiText("deletionQueued"));pollDeletion(result,requestRevision);
    }else if(form.id==="job-search"){await doJobSearch();}
    else if(form.matches(".job-status-form")){
      const job=growthState.job;await api(`/jobs/${job.id}/status`,v1Json("POST",{...values,confirmed:true},job.version));await render();message(uiText("jobStatusSaved"));
    }else if(form.matches(".merge-job-form")){
      const job=growthState.job;await api(`/jobs/${job.id}/merge`,v1Json("POST",{source_id:values.source_id,confirmed:true},job.version));await render();message(uiText("jobsMerged"));
    }
  }catch(error){if(form.id==="login-form")document.querySelector("#login-error").textContent=error.message;else if(revision===requestRevision)message(error.message,true);}finally{if(button)button.disabled=false;}
});

let assistantPollGeneration=0;
async function pollAssistant(id,revision,generation){
  if(revision!==requestRevision||generation!==assistantPollGeneration)return;
  try{
    const run=await api(`/assistant/runs/${id}`);
    if(revision!==requestRevision||generation!==assistantPollGeneration)return;
    const box=document.querySelector("#assistant-result");if(!box)return;
    box.innerHTML=`<p>${textValue(run.summary||uiText('run.'+run.status))}</p>${run.steps.map(s=>`<p>${s.tool?textValue(uiText('intent.'+s.tool.replace('growth.',''))):uiOriginal(s.code)} · ${textValue(uiText('run.'+s.status))}</p>`).join("")}${run.status==="success"?`<a href="${run.input.intent==="plan"||run.result.tasks?"#learning":"#skills"}">${uiText("assistantRelated")}</a>`:""}`;
    if(["queued","running"].includes(run.status))setTimeout(()=>pollAssistant(id,revision,generation),1000);
  }catch(error){if(revision===requestRevision&&generation===assistantPollGeneration)message(error.message,true);}
}
async function pollDeletion(request,revision){
  try{const response=await fetch(`/api/v1/account/deletion/${request.id}`,{headers:{"X-Deletion-Receipt":request.receipt}});const result=await response.json();if(revision!==requestRevision)return;if(!response.ok)throw new Error(result.message);message(uiText(result.status==="completed"?"deletionDone":"deletionPending"));if(result.status!=="completed")setTimeout(()=>pollDeletion(request,revision),1000);}catch(error){if(revision===requestRevision)message(error.message,true);}
}

document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button)return;const d=button.dataset,revision=requestRevision;
  try{
    if(d.quiz){const exam=await api("/assessments",v1Json("POST",{skill_id:d.quiz}));if(revision===requestRevision){showAssessment(exam);document.querySelector("#assessment-view").scrollIntoView({block:"start"});}}
    if(button.id==="publish-plan"){await api(`/plans/${d.plan}/publish`,v1Json("POST",{confirmed:true},d.version));await render();message(uiText("planPublished"));}
    if(d.taskAction){const plan=v1State.plan;await api(`/plans/${plan.id}/tasks/${d.task}`,v1Json("POST",{action:d.taskAction},plan.version));await render();message(uiText("taskSaved"));}
    if(button.id==="request-replan"){await api(`/plans/${d.plan}/replan`,{method:"POST"});await render();message(uiText("replanCreated"));}
    if(d.decidePreview){await api(`/plan-previews/${d.preview}/${d.decidePreview}`,v1Json("POST",{confirmed:true},d.version));await render();message(uiText(d.decidePreview==="accept"?"adjustmentAccepted":"planKept"));}
    if(button.id==="plan-history"){
      const history=await api(`/plans/${d.plan}/history`);if(revision!==requestRevision)return;
      document.querySelector("#plan-history-view").innerHTML=history.map(h=>`<div class="list-row"><span>${textValue(uiFormat("planHistoryRow",{version:h.version,date:uiDate(h.created_at,v1State.timezone),count:h.payload.tasks.length}))}</span>${h.version!==v1State.plan.version?`<button class="secondary" data-restore-plan="${h.version}">${uiFormat("restorePlan",{version:h.version})}</button>`:""}</div>`).join("");
    }
    if(d.restorePlan){const plan=v1State.plan;await api(`/plans/${plan.id}/restore`,v1Json("POST",{target_version:Number(d.restorePlan),confirmed:true},plan.version));await render();message(uiText("planRestored"));}
    if(d.revokeEvidence){await api(`/skill-evidence/${d.revokeEvidence}/revoke`,v1Json("POST",{reason:"用户主动撤销",confirmed:true}));await render();message(uiText("evidenceRevoked"));}
    if(d.resolvePreferred){await api("/memory-conflicts/resolve",v1Json("POST",{preferred_id:d.resolvePreferred,superseded_id:d.resolveOther,confirmed:true},d.version));await render();message(uiText("memoryPreferred"));}
    if(d.careerTitle){document.querySelector("#goal-title").value=d.careerTitle;document.querySelector("#goal-form").scrollIntoView({block:"center"});document.querySelector("#goal-title").focus();}
    if(button.id==="save-web-consent"){await api("/consents/public_web",v1Json("PUT",{granted:document.querySelector("#web-consent").checked}));message(uiText("publicConsentSaved"));}
    if(button.id==="export-account"){
      const result=await api("/account/export"),url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="careerpilot-export.json";a.click();URL.revokeObjectURL(url);message(uiText("exportReady"));
    }
    if(d.fetchSource){const result=await api("/web/fetch",v1Json("POST",{url:d.fetchSource}));if(revision===requestRevision)document.querySelector("#source-excerpt").innerHTML=`<p>${uiOriginal(result.excerpt)}</p><p>${uiText("evidenceSource")}：${uiOriginal(result.citation)}</p>`;}
    if(button.id==="save-job-filter"){const values=searchValues();if(!values.name.trim())throw new Error(uiText("filterNameRequired"));await api("/job-filters",v1Json("POST",{...values.filters,name:values.name}));await render();message(uiText("filterSaved"));}
    if(d.useFilter){const saved=v1State.filters.find(f=>f.id===d.useFilter);const form=document.querySelector("#job-search");for(const [key,value] of Object.entries(saved)){if(form.elements.namedItem(key))form.elements.namedItem(key).value=value??"";}await doJobSearch();}
    if(button.id==="next-jobs"&&v1State.lastCursor)await doJobSearch(v1State.lastCursor);
    if(d.milestone){await api(`/goal-milestones/${d.milestone}`,v1Json("PATCH",{completed:d.completed!=="true",confirmed:true},d.version));await render();message(uiText("milestoneUpdated"));}
  }catch(error){if(revision===requestRevision)message(error.message,true);}
});

document.addEventListener("submit",async event=>{
  if(event.target.id!=="milestone-form")return;
  event.preventDefault();const values=Object.fromEntries(new FormData(event.target));
  try{await api(`/career-goals/${values.goal_id}/milestones`,v1Json("POST",{title:values.title,target_date:values.target_date,confirmed:true}));await render();message(uiText("milestoneSaved"));}catch(error){message(error.message,true);}
});
