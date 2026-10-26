"use strict";
const studyModes={sprint:100,balanced:75,research_busy:50,light:25};
async function mountStudy(revision){
  const queue=await api("/study-reviews");
  if(revision!==requestRevision)return;
  const plan=v1State.plan,resources=Object.values(v1State.resources||{});
  view.insertAdjacentHTML("beforeend",`<article class="card growth-detail" lang="${uiLocale}"><h2>${uiText("studyPace")}</h2><p>${uiText("studyPaceHelp")}</p>${plan?.status==="active"?`<form id="study-mode-form"><label>${uiText("studyMode")}<select name="mode" aria-label="${uiText("studyMode")}">${Object.keys(studyModes).map(k=>`<option value="${k}" ${plan.study_mode===k?"selected":""}>${uiText("mode."+k)}</option>`).join("")}</select></label><button>${uiText("previewPace")}</button></form>`:`<p>${uiText("publishBeforePace")}</p>`}<div id="study-mode-preview"></div></article><article class="card growth-detail" lang="${uiLocale}" id="study-review-queue"><h2>${uiText("reviewsTitle")}</h2><p>${uiText("reviewNotice")}</p><p>${uiText("todayLabel")}：${textValue(uiDate(queue.today))} · ${uiOriginal(queue.timezone)}。<a href="#progress">${uiText("reviewReminders")}</a></p>${queue.items.map(i=>`<details data-review-skill="${textValue(i.skill_id)}"><summary>${uiOriginal(i.name)} · ${textValue(uiDate(i.due_date))} · ${uiText("risk."+i.risk)}</summary><p>${uiFormat("reviewInterval",{days:i.interval_days})} ${uiText(i.conclusion==="passed"?"passed":"failed")}。</p>${i.wrong_questions.map(q=>`<p>${uiText("wrongQuestion")} ${uiOriginal(q.question_id)}：${uiSource(q.explanation)}</p>`).join("")}<button data-quiz="${textValue(i.skill_id)}">${escapeHTML(uiFormat("retakeQuiz",{name:i.name}))}</button></details>`).join("")||`<p>${uiText("noReviews")}</p>`}</article><div id="assessment-view"></div><article class="card growth-detail" lang="${uiLocale}" id="study-resource-feedback"><h2>${uiText("resourceFeedback")}</h2><p>${uiText("resourceFeedbackHelp")}</p>${resources.map(r=>`<details data-resource="${textValue(r.id)}"><summary>${uiOriginal(r.title)}${r.feedback?.favorite?" · "+uiText("favoriteSaved"):""}${r.status!=="available"?" · "+uiText("notRecommended"):""}</summary><p>${r.status==="available"?`<a href="${textValue(r.url)}" target="${r.url.startsWith("/")?"_self":"_blank"}" rel="noopener noreferrer">${uiText("openResource")}</a>`:uiText("resourceUnavailable")}</p>${r.local_fallback?`<p>${uiSource(r.alternative)}</p>`:`<form class="study-resource-form" data-resource="${textValue(r.id)}" data-version="${r.feedback.version}"><label><input type="checkbox" name="favorite" ${r.feedback.favorite?"checked":""}> ${uiText("favorite")}</label><label>${uiText("quality")}<select name="quality" aria-label="${uiText("quality")}">${["unrated","helpful","poor"].map(k=>`<option value="${k}" ${r.feedback.quality===k?"selected":""}>${uiText("quality."+k)}</option>`).join("")}</select></label><label><input type="checkbox" name="broken" ${r.feedback.broken?"checked":""}> ${uiText("reportBroken")}</label><label>${uiText("feedbackNote")}<textarea name="note" aria-label="${uiText("feedbackNote")}" lang="" dir="auto" maxlength="1000">${textValue(r.feedback.note)}</textarea></label><button>${uiText("saveFeedback")}</button></form>`}</details>`).join("")}</article>`);
}

function studyResourceHTML(id){
  const resource=v1State.resources?.[id];
  if(!resource)return "";
  if(resource.status==="available")return `<p><a href="${textValue(resource.url)}" target="${resource.url.startsWith("/")?"_self":"_blank"}" rel="noopener noreferrer">${uiOriginal(resource.title)}</a> · ${uiText("registeredSource")}</p>`;
  const alternatives=Object.values(v1State.resources).filter(r=>r.skill_id===resource.skill_id&&r.status==="available");
  return `<p>${uiOriginal(resource.title)} · ${uiText("notRecommended")}</p>${alternatives.map(r=>`<p>${uiText("alternativeResource")}：<a href="${textValue(r.url)}" target="${r.url.startsWith("/")?"_self":"_blank"}" rel="noopener noreferrer">${uiOriginal(r.title)}</a></p>`).join("")}`;
}

document.addEventListener("submit",async event=>{
  const form=event.target;
  if(!form.matches("#study-mode-form,.study-resource-form"))return;
  event.preventDefault();const revision=requestRevision,button=event.submitter;
  button.disabled=true;
  try{
    const data=Object.fromEntries(new FormData(form));
    if(form.id==="study-mode-form"){
      const plan=v1State.plan;
      const preview=await api(`/plans/${plan.id}/mode-preview`,{...v1Json("POST",{mode:data.mode,confirmed:true},plan.version),headers:{"If-Match":`"${plan.version}"`,"Idempotency-Key":crypto.randomUUID()}});
      if(revision===requestRevision){const box=document.querySelector("#study-mode-preview");box.innerHTML=previewHTML(preview);focusLearningResult(box);}
    }else{
      await api(`/learning-resources/${form.dataset.resource}/feedback`,v1Json("PUT",{favorite:Boolean(data.favorite),quality:data.quality,broken:Boolean(data.broken),note:data.note,confirmed:true},form.dataset.version));
      if(revision===requestRevision){await render();message(uiText("feedbackSaved"));}
    }
  }catch(e){if(revision===requestRevision)message(e.message,true);}
  finally{if(button.isConnected)button.disabled=false;}
});
