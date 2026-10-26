"use strict";
let growthState = {};
const versionHeader = version => ({"If-Match": `"${version}"`});
const jsonOptions = (method, body, version) => ({method, body: JSON.stringify(body), headers: versionHeader(version)});
const categoryKeys = {self_report:"selfReport", document_fact:"documentFact", inference:"inference"};
// Remaining Chinese business modules still consume these shared labels.
const fieldLabels=Object.fromEntries(["title","company","location","education","graduation_cohort","duration","salary","employment_type","deadline","published_at","responsibilities"].map(key=>[key,localeResources['zh-CN']['job.'+key]]));
const requirementLabels=Object.fromEntries(["required","preferred","mentioned"].map(key=>[key,localeResources['zh-CN']['requirement.'+key]]));
let profileEditRevision=0;

function profileForm(profile) {
  const input = (key, title, type="text", extra="") => `<label>${title}<input name="${key}" lang="" dir="auto" type="${type}" value="${escapeHTML(profile[key] ?? "")}" ${extra}></label>`;
  return `<article class="card" lang="${uiLocale}"><h2>${uiText("profileTitle")}</h2><p>${uiText("profileHelp")}</p>
    <form id="profile-form" class="form-grid">
      <label>${uiText("education")}<select name="education" aria-label="${uiText("education")}">${["bachelor","master","doctor","associate","high_school","other"].map(key=>`<option value="${key}" ${profile.education===key?"selected":""}>${uiText(key)}</option>`).join("")}</select></label>
      ${input("major",uiText("major"),"text","required maxlength='100'")}${input("academic_year",uiText("academic_year"),"text","required maxlength='40'")}${input("graduation_date",uiText("graduation_date"),"date","required")}
      <label>${uiText("preferred_cities")}<input name="preferred_cities" lang="" dir="auto" required value="${escapeHTML((profile.preferred_cities||[]).join(","))}"></label>
      ${input("expected_monthly_salary",uiText("expected_monthly_salary"),"number","min='0' max='1000000'")}
      ${input("weekly_minutes",uiText("weekly_minutes"),"number","required min='0' max='10080'")}
      <label>${uiText("profileTimezone")}<input name="timezone" required value="${escapeHTML(profile.timezone||"Asia/Shanghai")}"></label>
      <label class="full">${uiText("preferences")}<textarea name="preferences" aria-label="${uiText("preferences")}" lang="" dir="auto" maxlength="2000">${escapeHTML(profile.preferences||"")}</textarea></label>
      <button type="submit">${uiText("profilePreview")}</button>
    </form><div id="profile-preview" lang="${uiLocale}"></div><div id="profile-history" lang="${uiLocale}"></div></article>`;
}

function profileChangeValue(key,value){
  if(value===null||value===undefined||value===""||(Array.isArray(value)&&!value.length))return uiText("notSet");
  if(key==="education")return escapeHTML(uiText(value));
  if(key==="graduation_date")return escapeHTML(uiDate(value));
  if(key==="expected_monthly_salary")return escapeHTML(new Intl.NumberFormat(uiLocale,{style:"currency",currency:"CNY"}).format(value));
  if(key==="weekly_minutes")return escapeHTML(new Intl.NumberFormat(uiLocale).format(value));
  return uiOriginal(Array.isArray(value)?value.join(", "):value);
}
function profileChangesHTML(changes){
  if(!Object.keys(changes).length)return `<p>${uiText("noChanges")}</p>`;
  return `<div class="table-wrap" tabindex="0" role="region" aria-label="${uiText("profileConfirm")}"><table><caption class="visually-hidden">${uiText("profileConfirm")}</caption><thead><tr><th scope="col">${uiText("changeField")}</th><th scope="col">${uiText("changeBefore")}</th><th scope="col">${uiText("changeAfter")}</th></tr></thead><tbody>${Object.entries(changes).map(([key,change])=>`<tr><th scope="row">${escapeHTML(uiText(key==="timezone"?"profileTimezone":key))}</th><td>${profileChangeValue(key,change.before)}</td><td>${profileChangeValue(key,change.after)}</td></tr>`).join("")}</tbody></table></div>`;
}

function memoryCards(memories) {
  return memories.length ? memories.map(m=>`<article class="card" lang="${uiLocale}"><form class="memory-form" data-id="${m.id}" data-version="${m.version}">
    <span class="tag">${uiText(categoryKeys[m.category])} · ${uiText("unverifiedSkill")} · v${m.version}</span>
    <label>${uiText("memoryText")}<textarea name="text" aria-label="${uiText("memoryText")}" lang="" dir="auto" required maxlength="2000">${escapeHTML(m.text)}</textarea></label>
    <label>${uiText("claimType")}<select name="category" aria-label="${uiText("claimType")}">${Object.entries(categoryKeys).map(([key,label])=>`<option value="${key}" ${m.category===key?"selected":""}>${uiText(label)}</option>`).join("")}</select></label>
    <p>${uiText("originalQuote")}${uiOriginal(m.source_quote)}<br>${uiText("sourceVersion")}${m.source_version}</p>
    <button type="submit">${uiText("editMemory")}</button>
    <details><summary>${uiText("deleteMemory")}</summary><p>${uiText("deleteMemoryHelp")}</p><button type="button" class="secondary" data-delete-memory="${m.id}" data-version="${m.version}">${uiText("confirmDeleteMemory")}</button></details>
  </form></article>`).join("") : `<p lang="${uiLocale}">${uiText("noMemories")}</p>`;
}

async function renderGrowth(route, revision) {
  if (await renderHiring(route,revision)) return true;
  if (await renderV1(route,revision)) return true;
  if (!["settings", "market", "skills", "explore"].includes(route)) return false;
  growthState = {};
  view.innerHTML = `<div class="card" role="status" lang="${uiLocale}">${uiText("personalLoading")}</div>`;
  try {
    if (route === "settings") {
      const [profile, materials, consents, history] = token ? await Promise.all([api("/profile"), api("/materials"), api("/consents"), api("/profile/history")]) : [{version:0},[],[],[]];
      if (revision !== requestRevision) return true;
      growthState.profile = profile;
      view.innerHTML = `<div class="growth-stack">${profileForm(profile)}
        <article class="card" lang="${uiLocale}"><h2>${uiText("importMaterials")}</h2><p>${uiText("materialHelp")}</p>
          <form id="material-form"><label>${uiText("chooseTextFile")}<input name="file" type="file" accept=".txt,.md,.json"></label><label>${uiText("pasteMaterial")}<textarea name="content" lang="" dir="auto" maxlength="100000" placeholder="${uiText("materialPlaceholder")}"></textarea></label><button type="submit">${uiText("parseMaterial")}</button></form>
          <div id="material-preview"></div><h3>${uiText("importedMaterials")}</h3>${materials.map(m=>`<div class="list-row"><span>${uiOriginal(m.filename)} · ${uiText(m.status==="confirmed"?"confirmed":"pendingConfirmation")}</span><button class="secondary" data-material="${m.id}">${uiText("viewMaterial")}</button></div>`).join("")||`<p>${uiText("noMaterials")}</p>`}</article>
        <div class="grid two" lang="${uiLocale}"><article class="card"><h2>${uiText("privacyConsent")}</h2><p>${uiText("privacyHelp")}</p><label><input id="model-consent" type="checkbox" ${consents.some(c=>c.purpose==="external_model"&&c.granted)?"checked":""}> ${uiText("modelConsent")}</label><div class="actions"><button id="save-consent">${uiText("saveConsent")}</button></div></article>
        <article class="card"><h2>${uiText("accountConnection")}</h2><p>${uiText("accountConnectionHelp")}</p><button id="logout" class="secondary">${uiText("logout")}</button></article></div></div>`;
      document.querySelector("#profile-history").innerHTML = history.length ? `<details><summary>${escapeHTML(uiFormat("profileHistory",{version:profile.version}))}</summary>${history.map(h=>`<div class="list-row"><span>${escapeHTML(uiFormat("profileHistoryRow",{version:h.version,date:uiDate(h.created_at,profile.timezone),minutes:new Intl.NumberFormat(uiLocale).format(h.payload.weekly_minutes)}))}</span>${h.version!==profile.version?`<button class="secondary" data-restore-profile="${h.version}">${uiText("restoreVersion")}</button>`:`<span>${uiText("currentVersion")}</span>`}</div>`).join("")}</details>` : "";
      await enhanceV1Settings(revision);
    }
    if (route === "skills") {
      const memories = token ? await api("/memories") : [];
      if (revision !== requestRevision) return true;
      view.innerHTML = `<article class="card" lang="${uiLocale}"><h2>${uiText("memoryTitle")}</h2><p>${uiText("memoryHelp")}</p><a href="#settings">${uiText("importMaterialLink")}</a></article><div class="growth-stack">${memoryCards(memories)}</div>`;
    }
    if (route === "explore") {
      const goals = token ? await api("/career-goals?limit=100") : [];
      if (revision !== requestRevision) return true;
      view.innerHTML = `<article class="card" lang="${uiLocale}"><h2>${uiText("careerGoals")}</h2><p>${uiText("careerGoalsHelp")}</p><form id="goal-form" class="goal-form"><label>${uiText("interestedCareer")}<input id="goal-title" lang="" dir="auto" maxlength="200" required placeholder="${uiText("careerPlaceholder")}"></label><button type="submit">${uiText("saveGoalDraft")}</button></form><div id="goals">${goals.map(g=>`<div class="list-row"><strong>${uiOriginal(g.title)}</strong><span class="tag">${uiText(g.status==="active"?"primaryGoal":"alternativeGoal")} · v${g.version}</span>${g.status!=="active"?`<button class="secondary" data-goal-preview="${g.id}">${uiText("previewGoal")}</button>`:""}</div>`).join("")||`<p>${uiText("noGoals")}</p>`}</div><div id="goal-impact"></div></article>`;
      await enhanceV1Explore(revision);
      await enhanceV2Explore(revision);
    }
    if (route === "market") {
      const [jobs, profile] = token ? await Promise.all([api("/jobs"),api("/profile")]) : [[],{}];
      if (revision !== requestRevision) return true;
      growthState.jobs = jobs;
      growthState.profile = profile;
      view.innerHTML = `<div class="grid two" lang="${uiLocale}"><article class="card"><h2>${uiText("importJob")}</h2><p>${uiText("importJobHelp")}</p><form id="job-form"><label>${uiText("sourceUrl")}<input name="source_url" type="url" placeholder="${uiText("sourcePlaceholder")}"></label><label>${uiText("jdOriginal")}<textarea name="content" lang="" dir="auto" required maxlength="100000" rows="12" placeholder="${uiText("jdPlaceholder")}"></textarea></label><button type="submit">${uiText("extractJob")}</button></form></article><article class="card"><h2>${uiText("myJobs")}</h2><form id="job-search" class="form-grid"><label>${uiText("keyword")}<input name="q" lang="" dir="auto"></label><label>${uiText("city")}<input name="location" lang="" dir="auto"></label><button type="submit">${uiText("searchJobs")}</button></form><div id="job-list"></div></article></div><div id="job-detail" lang="${uiLocale}"></div>`;
      showJobList(jobs);
      await enhanceV1Market(revision);
      await enhanceV2Market(revision);
    }
    if (!token) message(uiText("personalConnect"));
  } catch (error) {
    if (revision === requestRevision) { shellView(empty(uiText("err.growth"),escapeHTML(error.message),uiText("reload"),route)); message(error.message,true); }
  }
  return true;
}

function showJobList(jobs) {
  growthState.jobs = jobs;
  document.querySelector("#job-list").innerHTML=jobs.map(j=>`<div class="list-row"><strong>${j.fields.title.value?uiOriginal(j.fields.title.value):uiText("unidentifiedJob")}</strong><button class="secondary" data-job="${j.id}">${uiText("viewJobEvidence")}</button></div>`).join("")||`<p>${uiText("noJobs")}</p>`;
}

function showJob(job) {
  growthState.job = job;
  const evidence = e => e ? `<button class="text-button" data-span-start="${e.start}" data-span-end="${e.end}">${uiText("originalQuote")}${uiOriginal(e.quote)}</button>` : uiText("noQuote");
  document.querySelector("#job-detail").innerHTML=`<article class="card growth-detail"><h2>${job.fields.title.value?uiOriginal(job.fields.title.value):uiText("jobToVerify")} · v${job.version}</h2><p>${uiText("jobVerifyHelp")}</p><div class="grid two"><div>${Object.entries(job.fields).map(([k,f])=>`<div class="evidence-row"><strong>${uiText("job."+k)}</strong><p>${f.value===null||f.value===undefined?uiText("unknown"):["deadline","published_at"].includes(k)&&/^\d{4}-\d{2}-\d{2}$/.test(f.value)?escapeHTML(uiDate(f.value)):uiOriginal(f.value)}<br>${evidence(f.evidence)}</p></div>`).join("")}<h3>${uiText("skillWording")}</h3>${job.skills.map(s=>`<p>${uiOriginal(s.skill_id)} · ${uiText("requirement."+s.requirement)} ${evidence(s.evidence)}</p>`).join("")||`<p>${uiText("noSkills")}</p>`}</div><div><h3>${uiText("jobOriginal")}</h3><pre id="jd-original" lang="" dir="auto" data-original-content>${escapeHTML(job.content)}</pre><h3>${uiText("sourcesTitle")}</h3>${job.sources.map(s=>`<p>${s.source_url?`<a href="${escapeHTML(s.source_url)}" target="_blank" rel="noopener noreferrer">${uiOriginal(s.source_url)}</a>`:uiText("pastedSource")}<br>${escapeHTML(uiDate(s.retrieved_at,growthState.profile?.timezone))} · ${uiText("userImported")}</p>`).join("")}<button class="secondary" data-job-history="${job.id}">${uiText("jobHistory")}</button><div id="job-history"></div></div></div></article>`;
  enhanceV1Job(job);
}

function showMaterial(material) {
  growthState.material=material;
  const container=document.querySelector("#material-preview");
  container.innerHTML=`<div class="growth-detail"><h3 tabindex="-1">${uiOriginal(material.filename)} · ${uiText(material.status==="confirmed"?"confirmed":"confirmEach")}</h3><details><summary>${uiText("viewOriginal")}</summary><pre lang="" dir="auto" data-original-content>${escapeHTML(material.content)}</pre></details>${material.status==="pending_confirmation"?`<form id="confirm-material-form"><p>${uiText("selectClaims")}</p>${material.candidates.map((c,i)=>`<div class="claim-row"><label><input type="checkbox" name="selected" value="${i}"> ${uiFormat("keepClaim",{number:i+1})}</label><textarea lang="" dir="auto" aria-label="${uiFormat("claimLabel",{number:i+1})}" data-claim="${i}" maxlength="2000">${escapeHTML(c.text)}</textarea></div>`).join("")}<button type="submit">${uiText("confirmClaims")}</button></form>`:`<p>${uiText("materialConfirmed")}</p>`}</div>`;
  focusGrowthHeading(container);
}

document.addEventListener("submit", async event => {
  const form=event.target;
  if (!["profile-form","material-form","confirm-material-form","job-form","job-search"].includes(form.id)&&!form.matches(".memory-form")) return;
  event.preventDefault();
  const button=form.querySelector("button[type=submit]");
  if(button) button.disabled=true;
  const revision=requestRevision;
  try {
    const values=Object.fromEntries(new FormData(form));
    if(form.id==="profile-form") {
      const payload={...values,preferred_cities:values.preferred_cities.split(/[,，]/).map(v=>v.trim()).filter(Boolean),weekly_minutes:Number(values.weekly_minutes),expected_monthly_salary:values.expected_monthly_salary===""?null:Number(values.expected_monthly_salary)};
      const editRevision=profileEditRevision;
      const preview=await api("/profile/preview",{method:"POST",body:JSON.stringify(payload)});
      if(revision!==requestRevision)return;
      if(editRevision!==profileEditRevision){message(uiText("profileChanged"));return;}
      growthState.profileDraft={payload,version:preview.base_version};
      const container=document.querySelector("#profile-preview");
      container.innerHTML=`<div class="growth-detail"><h3 tabindex="-1">${uiText("profileConfirm")}</h3><p>${escapeHTML(preview.message)}</p>${profileChangesHTML(preview.changes)}<button type="button" id="confirm-profile">${uiText("profileSave")}</button> <button type="button" id="cancel-profile" class="secondary">${uiText("profileCancel")}</button></div>`;
      focusGrowthHeading(container);
    } else if(form.id==="material-form") {
      const file=form.querySelector("input[type=file]").files[0];
      if(!file&&!values.content.trim())throw new Error(uiText("materialRequired"));
      if(file&&file.size>1024*1024)throw new Error(uiText("materialTooLarge"));
      const material=await api(`/materials?filename=${encodeURIComponent(file?file.name:"material.md")}`,{method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file||values.content});
      if(revision===requestRevision){showMaterial(material);message(uiText("materialSaved"));}
    } else if(form.id==="confirm-material-form") {
      const material=growthState.material;
      const selected=[...form.querySelectorAll("input[name=selected]:checked")];
      if(!selected.length)throw new Error(uiText("claimRequired"));
      const claims=selected.map(input=>{const i=Number(input.value),c=material.candidates[i];return {text:form.querySelector(`[data-claim="${i}"]`).value,start:c.start,end:c.end,category:"self_report"};});
      await api(`/materials/${material.id}/confirm`,jsonOptions("POST",{confirmed:true,claims},material.version));
      await render();message(uiText("claimsSaved"));
    } else if(form.matches(".memory-form")) {
      await api(`/memories/${form.dataset.id}`,jsonOptions("PATCH",{...values,confirmed:true},form.dataset.version));
      await render();message(uiText("memorySaved"));
    } else if(form.id==="job-form") {
      const job=await api("/jobs/import",{method:"POST",body:JSON.stringify({content:values.content,source_url:values.source_url||null})});
      if(revision!==requestRevision)return;
      const jobs=await api("/jobs");
      if(revision!==requestRevision)return;
      showJobList(jobs);showJob(job);message(uiText(job.deduplicated?"jobDeduplicated":"jobImported"));
    } else if(form.id==="job-search") {
      const jobs=await api(`/jobs?${new URLSearchParams(values)}`);
      if(revision===requestRevision)showJobList(jobs);
    }
  } catch(error) {if(revision===requestRevision)message(error.message,true);} finally {if(button)button.disabled=false;}
});

function invalidateProfilePreview(event) {
  if (!event.target.closest("#profile-form"))return;
  profileEditRevision++;
  if (growthState.profileDraft) {
    growthState.profileDraft = null;
    document.querySelector("#profile-preview").innerHTML = `<p role="status">${uiText("profileChanged")}</p>`;
  }
}
document.addEventListener("input",invalidateProfilePreview);
document.addEventListener("change",invalidateProfilePreview);

document.addEventListener("click", async event => {
  const button=event.target.closest("button");
  if(!button)return;
  const d=button.dataset, revision=requestRevision;
  if(!["confirm-profile","cancel-profile","accept-goal","cancel-goal"].includes(button.id)&&!["restoreProfile","material","deleteMemory","job","jobHistory","spanStart","goalPreview"].some(key=>key in d))return;
  try {
    if(button.id==="confirm-profile") {
      button.disabled=true;
      const draft=growthState.profileDraft;
      if(!draft)throw new Error(uiText("profileChanged"));
      await api("/profile",jsonOptions("PUT",{...draft.payload,confirmed:true},draft.version));
      await render();message(uiText("profileSaved"));
    }
    if(button.id==="cancel-profile") {document.querySelector("#profile-preview").innerHTML="";growthState.profileDraft=null;document.querySelector("#profile-form button[type=submit]").focus();}
    if(d.restoreProfile) {
      await api("/profile/restore",jsonOptions("POST",{target_version:Number(d.restoreProfile),confirmed:true},growthState.profile.version));
      await render();message(uiText("profileRestored"));
    }
    if(d.material) {const material=await api(`/materials/${d.material}`);if(revision===requestRevision)showMaterial(material);}
    if(d.deleteMemory) {
      await api(`/memories/${d.deleteMemory}`,{method:"DELETE",headers:versionHeader(d.version)});
      await render();message(uiText("memoryDeleted"));
    }
    if(d.job) {const job=await api(`/jobs/${d.job}`);if(revision===requestRevision)showJob(job);}
    if(d.jobHistory) {
      const history=await api(`/jobs/${d.jobHistory}/history`);
      if(revision===requestRevision)document.querySelector("#job-history").innerHTML=history.map(h=>`<details><summary>v${h.version} · ${escapeHTML(uiDate(h.created_at,growthState.profile?.timezone))}</summary><pre lang="" dir="auto" data-original-content>${escapeHTML(h.payload.content)}</pre></details>`).join("");
    }
    if(d.spanStart!==undefined) {
      const chars=Array.from(growthState.job.content), start=Number(d.spanStart), end=Number(d.spanEnd);
      document.querySelector("#jd-original").innerHTML=`${escapeHTML(chars.slice(0,start).join(""))}<mark>${escapeHTML(chars.slice(start,end).join(""))}</mark>${escapeHTML(chars.slice(end).join(""))}`;
      document.querySelector("#jd-original mark").scrollIntoView({block:"center"});
    }
    if(d.goalPreview) {
      const preview=await api(`/career-goals/${d.goalPreview}/preview`,{method:"POST"});
      if(revision!==requestRevision)return;
      growthState.goalPreview=preview;
      const container=document.querySelector("#goal-impact");
      container.innerHTML=`<div class="growth-detail"><h3 tabindex="-1">${uiText("setPrimaryGoal")}${uiOriginal(preview.goal_title)}</h3><p>${escapeHTML(preview.impact)}</p><p>${uiText("currentPrimary")}${preview.previous_active.length?preview.previous_active.map(g=>uiOriginal(g.title)).join(", "):uiText("noneYet")}</p><button id="accept-goal">${uiText("confirmGoal")}</button> <button id="cancel-goal" class="secondary">${uiText("cancelGoal")}</button></div>`;
      focusGrowthHeading(container);
    }
    if(button.id==="accept-goal") {
      button.disabled=true;
      const preview=growthState.goalPreview;
      await api(`/goal-previews/${preview.id}/accept`,jsonOptions("POST",{confirmed:true},preview.version));
      await render();message(uiText("goalChanged"));
    }
    if(button.id==="cancel-goal") {const id=growthState.goalPreview?.goal_id;document.querySelector("#goal-impact").innerHTML="";growthState.goalPreview=null;document.querySelector(`[data-goal-preview="${id}"]`)?.focus();message(uiText("goalCancelled"));}
  } catch(error) {if(revision===requestRevision)message(error.message,true);} finally {button.disabled=false;}
});
