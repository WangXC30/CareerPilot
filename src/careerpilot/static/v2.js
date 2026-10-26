"use strict";
let v2Constraints = {version:0};

// The overlay contains only known fixed system explanations, never owner text.
function matchText(original,english){return uiLocale==='en-US'&&typeof english==='string'?textValue(english):uiSource(original);}
const eligibilityLabel=state=>uiText('match.'+state);
function matchCard(item) {
  const display=item._ui||{};
  return `<article class="card match-card" lang="${uiLocale}" data-match-job="${textValue(item.job_id)}">
    <span class="tag">${item.policy_blocked?uiText("matchBlocked"):eligibilityLabel(item.eligibility)}</span><h3>${uiOriginal(item.title)}</h3>
    <p>${item.company?uiOriginal(item.company):uiText("companyUnknown")} · ${uiFormat("jobVersion",{version:item.job_version})}</p>
    <p>${uiFormat("explanationScore",{score:item.score,weight:item.known_weight})}</p>
    ${item.mode_reason?`<p>${matchText(item.mode_reason,display.mode_reason)}<br>${uiFormat("rankingScore",{score:item.ranking_score,adjustment:item.feedback_adjustment})}</p>`:""}
    <details><summary>${uiText("matchDetails")}</summary>
      ${item.checks.map(c=>`<div class="evidence-row"><strong>${uiText('constraint.'+c.key)} · ${eligibilityLabel(c.state)}</strong><p>${matchText(c.reason,display.checks?.[c.key])}<br>${uiText("jobCondition")}：${c.job_value===null||c.job_value===undefined?uiText("notStated"):uiOriginal(c.job_value)}<br>${uiText("myCondition")}：${c.user_value===null||c.user_value===undefined?uiText("notConfirmed"):Array.isArray(c.user_value)&&!c.user_value.length?uiText("confirmedAbsent"):c.key==="education"?textValue(uiText(c.user_value)):uiOriginal(Array.isArray(c.user_value)?c.user_value.join(", "):c.user_value)}</p>${c.job_evidence?`<p>${uiText("originalQuote")}${uiOriginal(c.job_evidence.quote)}</p>`:""}</div>`).join("")}
      <h4>${uiText("scoreBreakdown")}</h4>${Object.entries(item.components).map(([key,c])=>`<p>${uiText('component.'+key)}：${c.ratio===null?uiText("unknown"):`${c.points} / ${c.weight}`}<br>${c.basis.reason?matchText(c.basis.reason,display.components?.[key]):uiFormat("foundationCoverage",{count:c.basis.verified?.length||0,total:c.basis.denominator||0})}</p>`).join("")}
      <h4>${uiText("skillsSources")}</h4>${item.skills.map(s=>`<p>${uiOriginal(s.skill_id)} · ${uiText('requirement.'+s.requirement)} · ${skillLabel(s.state?.status||"unassessed")}<br>${uiText("jdOriginal")}：${uiOriginal(s.evidence.quote)}<br>${uiText("existingEvidence")}：${(s.state?.evidence_ids||[]).map(uiOriginal).join(", ")||uiText("none")}</p>`).join("")||`<p>${uiText("noSkillRequirements")}</p>`}
      <p>${uiText("evidenceSource")}：${item.sources.map(s=>s.source_url?uiOriginal(s.source_url):uiText("pastedByUser")).join("; ")}</p><p>${uiText("ruleLabel")} ${uiOriginal(item.rule_version)} · ${uiText("weightLabel")} ${uiOriginal(item.weight_version)}</p>
    </details>${jobControls(item)}<button class="secondary" data-job="${textValue(item.job_id)}">${uiText("viewJobEvidence")}</button></article>`;
}

function matchGroups(result) {
  return `<p>${matchText(result.notice,result._ui?.notice)}</p><p>${matchText(result.ranking_basis,result._ui?.ranking_basis)}。${uiText("matchEvidenceHelp")}</p>${result.hidden?.length?`<p>${uiFormat("hiddenJobs",{count:result.hidden.length})}</p>`:""}${[["eligible","eligibleGroup"],["needs_review","match.UNKNOWN"],["excluded","match.FAIL"]].map(([key,label])=>`<section class="match-group" data-match-group="${key}"><h3>${uiText(label)}（${result[key].length}）</h3><div class="grid">${result[key].map(matchCard).join("")||`<p>${uiText("noMatchedJobs")}</p>`}</div></section>`).join("")}`;
}

async function enhanceV2Market(revision) {
  if (!token) return;
  const [limits,result] = await Promise.all([api("/matching-constraints"),api("/job-matches")]);
  if (revision!==requestRevision) return;
  v2Constraints=limits;
  const names = value => value===null||value===undefined?"":value.join(", ");
  view.insertAdjacentHTML("beforeend",`<article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("matchingConstraints")}</h2><p>${uiText("matchingConstraintsHelp")}</p><form id="matching-constraints-form" class="form-grid"><label><input name="cities_hard" type="checkbox" ${limits.cities_hard?"checked":""}> ${uiText("citiesHard")}</label><label>${uiText("availableMonths")}<input name="available_months" type="number" min="1" max="36" value="${limits.available_months??""}"></label><label>${uiText("authorizations")}<input name="work_authorizations" lang="" dir="auto" maxlength="2000" value="${textValue(names(limits.work_authorizations))}"></label><label><input name="no_work_authorizations" type="checkbox" ${Array.isArray(limits.work_authorizations)&&!limits.work_authorizations.length?"checked":""}> ${uiText("noAuthorizations")}</label><label>${uiText("qualificationsHeld")}<input name="qualifications" lang="" dir="auto" maxlength="2000" value="${textValue(names(limits.qualifications))}"></label><label><input name="no_qualifications" type="checkbox" ${Array.isArray(limits.qualifications)&&!limits.qualifications.length?"checked":""}> ${uiText("noQualifications")}</label><button type="submit">${uiText("saveMatchingConstraints")}</button></form></article><article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("matchingTitle")}</h2><p>${uiText("matchingHelp")}</p><button id="save-match-snapshot" class="secondary">${uiText("saveMatchSnapshot")}</button><div id="match-snapshot-result" role="status"></div><div id="job-matches">${matchGroups(result)}</div></article>`);
  await enhanceV2Discovery(revision);
}

async function enhanceV2Explore(revision) {
  if (!token) return;
  const [result,limits] = await Promise.all([api("/career-comparison"),api("/matching-constraints")]);
  if (revision!==requestRevision) return;
  v2Constraints=limits;
  view.insertAdjacentHTML("afterbegin",`<article class="card" lang="${uiLocale}"><h2>${uiText("compareCareers")}</h2><p>${matchText(result.notice,result._ui?.notice)}</p><form id="career-interests-form"><fieldset><legend>${uiText("careerInterests")}</legend>${result.careers.map(c=>`<label class="job-choice"><input name="career_interest" type="checkbox" value="${textValue(c.career_id)}" ${c.interest?"checked":""}> ${uiOriginal(c.title)}</label>`).join("")}</fieldset><button type="submit">${uiText("saveInterests")}</button></form><p>${uiText("backgroundMajor")}：${result.careers[0]?.background.major?uiOriginal(result.careers[0].background.major):uiText("notSet")}。${uiText("samplesNotMarket")}</p></article><div id="career-comparison" class="grid growth-detail" lang="${uiLocale}">${result.careers.map(c=>`<article class="card"><span class="tag">${uiText(c.interest?"interestedDirection":c.active_goal?"currentPrimary":"alternativeDirection")}</span><h2>${uiOriginal(c.title)}</h2><p>${c.reasons.map((text,i)=>matchText(text,c._ui?.reasons[i])).join("; ")}</p><p>${uiFormat("foundationEstimate",{minutes:c.foundation_minutes})} · ${c.minimum_budget_weeks===null?uiText("needStudyBudget"):uiFormat("minimumWeeks",{weeks:c.minimum_budget_weeks})}</p><p>${matchText(c.cost_basis,c._ui?.cost_basis)}</p><p>${uiText("transferable")}：${c.transferable_skills.map(s=>uiOriginal(s.name)).join(", ")||uiText("noValidFoundation")}<br>${uiText("pendingValidation")}：${c.pending_skills.map(s=>uiOriginal(s.name)).join(", ")||uiText("none")}</p><p>${uiFormat("sampleCount",{count:c.sample_count,total:c.sample_denominator})}</p><details><summary>${uiText("sampleBasis")}</summary><p>${matchText(c.sample_basis,c._ui?.sample_basis)}</p>${c.samples.map(s=>`<p>JD ${textValue(s.job_id.slice(0,8))} · v${s.job_version} · ${uiText("overlapSkills")} ${s.overlap_skills.map(uiOriginal).join(", ")}</p>`).join("")}${c.cautions.map((text,i)=>`<p>${matchText(text,c._ui?.cautions[i])}</p>`).join("")}</details><p><a href="${textValue(c.source_url)}" target="_blank" rel="noopener noreferrer">${uiText("careerReference")}</a> · ${textValue(uiDate(c.reviewed_at.slice(0,10)))}${c.stale?" · "+uiText("updatePending"):""}</p><button class="secondary" data-career-title="${textValue(c.title)}">${uiText("useGoalDraft")}</button></article>`).join("")}</div>`);
}

document.addEventListener("submit",async event=>{
  const form=event.target;
  if(!["matching-constraints-form","career-interests-form"].includes(form.id))return;
  event.preventDefault();
  const revision=requestRevision,button=event.submitter;
  if(button)button.disabled=true;
  try {
    const {version,created_at,id,...currentLimits}=v2Constraints;
    const values=Object.fromEntries(new FormData(form));
    let payload={...currentLimits,confirmed:true};
    if(form.id==="career-interests-form")payload.preferred_career_ids=new FormData(form).getAll("career_interest");
    else {
      const names=key=>values[`no_${key}`]?[]:values[key].trim()?values[key].split(/[、,，]/).map(s=>s.trim()).filter(Boolean):null;
      if((values.no_work_authorizations&&values.work_authorizations.trim())||(values.no_qualifications&&values.qualifications.trim()))throw new Error(uiText("constraintConflict"));
      payload={...payload,cities_hard:!!values.cities_hard,available_months:values.available_months?Number(values.available_months):null,work_authorizations:names("work_authorizations"),qualifications:names("qualifications")};
    }
    await api("/matching-constraints",v1Json("PUT",payload,version));
    await refreshDiscovery(revision,"constraintsSaved");
  }catch(error){if(revision===requestRevision)message(error.message,true);}
  finally{if(button)button.disabled=false;}
});

document.addEventListener("click",async event=>{
  const button=event.target.closest("#save-match-snapshot");if(!button)return;
  const revision=requestRevision;button.disabled=true;
  try {
    const snapshot=await api("/job-matches/recompute",v1Json("POST",{job_ids:[]}));
    if(revision!==requestRevision)return;
    document.querySelector("#job-matches").innerHTML=matchGroups(snapshot);
    document.querySelector("#match-snapshot-result").textContent=uiFormat("snapshotSaved",{id:snapshot.id.slice(0,8),profile:snapshot.profile_version,constraints:snapshot.constraints_version});
  }catch(error){if(revision===requestRevision)message(error.message,true);}
  finally{button.disabled=false;}
});
