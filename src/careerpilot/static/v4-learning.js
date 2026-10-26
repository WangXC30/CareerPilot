/* Explicit private tutoring and an opt-in peer review channel. */
let v4Learning={sessions:[],reviews:[],spaces:[]};
const reviewConclusionLabels=uiLocaleLabels({context_explains_timing:"r.140",needs_followup:"r.141",inconclusive:"r.142"});
async function mountV4Learning(revision){
  const [sessions,reviews,spaces]=await Promise.all([api('/teaching-sessions'),api('/practice-reviews'),api('/workspaces')]);
  if(revision!==requestRevision)return;
  v4Learning={sessions,reviews,spaces:spaces.spaces.filter(space=>space.owner)};
  document.querySelector('#v4-result').insertAdjacentHTML('beforebegin',`<article class="card growth-detail" id="tutoring-history"><h2>${uiText("r.143")}</h2><p>${uiText("r.144")}</p>${sessions.map(session=>`<p>${uiSource(session.item.prompt)}${uiText("r.145")}${session.level}/3 · ${session.turns.length}${uiText("r.146")}<button data-tutor-open="${session.id}">${uiText("r.147")}</button> <button data-tutor-delete="${session.id}">${uiText("r.148")}</button></p>`).join('')||`<p>${uiText("r.149")}</p>`}</article><article class="card growth-detail" id="practice-review-list"><h2>${uiText("r.150")}</h2><p>${uiText("r.151")}</p><button id="refresh-practice-reviews">${uiText("r.152")}</button>${reviews.map(review=>`<p>${uiOriginal(review.snapshot.skills.join('、'))} · ${!review.access_active?uiText("r.153"):review.status==='reviewed'?uiText("r.154"):uiText("r.155")} <button data-review-open="${review.id}">${uiText("r.156")}</button>${!review.is_owner?'':` <button data-review-revoke="${review.id}" data-version="${review.version}">${uiText("r.157")}</button>`}</p>`).join('')||`<p>${uiText("r.158")}</p>`}</article>`);
}
function reviewConsentHTML(exam){
  if(exam.status!=='completed'||!exam.appeal)return `<p>${uiText("r.159")}</p>`;
  const pairs=v4Learning.spaces.flatMap(space=>space.members.map(member=>({space:space.id,member,name:space.name})));
  return pairs.length?`<form id="practice-review-request" data-exam="${exam.id}"><h3>${uiText("r.160")}</h3><p>${uiText("r.161")}</p><label>${uiText("r.162")}<select name="recipient">${pairs.map(pair=>`<option lang="" dir="auto" data-original-content value="${pair.space}:${pair.member}">${textValue(pair.name)} · ${textValue(pair.member)}</option>`).join('')}</select></label><label><input type="checkbox" required>${uiText("r.163")}</label><button>${uiText("r.164")}</button></form>`:`<p>${uiText("r.165")}</p>`;
}
function showTutor(session){
  v4Show(`<h2>${uiText("r.166")}</h2><p>${uiSource(session.item.prompt)}</p><p>${remainingFixed(session.notice)}</p><p>${uiText("r.167")}${uiOriginal(session.bank_version)}${uiText("r.168")}${session.catalogue_version} · <a href="${textValue(session.source_url)}" target="_blank" rel="noopener noreferrer">${uiText("r.100")}</a></p>${session.turns.map((turn,index)=>`<details ${index===session.turns.length-1?'open':''}><summary>${uiText("r.169")}${index+1}${uiText("r.170")}${turn.level}/3</summary><p>${uiText("r.171")}${uiOriginal(turn.attempt||uiText("r.172"))}</p>${turn.question?`<p>${uiText("r.173")}${uiOriginal(turn.question)}</p>`:''}<p>${remainingFixed(turn.hint)}</p></details>`).join('')}${session.turns.length<12?`<form id="tutor-followup" data-session="${session.id}" data-version="${session.version}"><label>${uiText("r.174")}<textarea name="attempt" required maxlength="2000"></textarea></label><label>${uiText("r.175")}<textarea name="question" maxlength="2000"></textarea></label><label>${uiText("r.176")}<select name="focus"><option value="concept">${uiText("r.177")}</option><option value="condition">${uiText("r.178")}</option><option value="counterexample">${uiText("r.179")}</option></select></label><button name="action" value="reflect">${uiText("r.180")}</button><button name="action" value="hint">${uiText("r.181")}</button></form>`:`<p>${uiText("r.182")}</p>`}`);
}
function showPracticeReview(review){
  v4Show(`<h2>${uiText("r.183")}</h2><p>${remainingFixed(review.notice)}</p><p>${review.access_active?uiText("r.184"):uiText("r.185")}</p><p>${uiText("r.186")}${uiOriginal(review.snapshot.appeal.text)}</p>${review.snapshot.answers.map((answer,index)=>`<details><summary>${uiText("r.169")}${index+1}${uiText("r.187")}${uiSource(answer.prompt)}</summary><p>${uiText("r.188")}${uiSource(answer.choices[answer.choice])} · ${answer.elapsed_seconds}${uiText("r.189")}${answer.correct?uiText("r.080"):uiText("r.081")}</p></details>`).join('')}${review.snapshot.review_flags.map(flag=>`<p>${remainingFixed(flag.basis)} ${remainingFixed(flag.notice)}</p>`).join('')}${review.decision?`<p>${uiText("r.190")}${textValue(reviewConclusionLabels[review.decision.conclusion])}；${uiOriginal(review.decision.note)}</p><p>${uiText("r.191")}</p>`:''}${review.can_review&&review.access_active?`<form id="practice-review-decision" data-review="${review.id}" data-version="${review.version}"><label>${uiText("r.192")}<select name="conclusion">${Object.entries(reviewConclusionLabels).map(([key,label])=>`<option value="${key}">${textValue(label)}</option>`).join('')}</select></label><label>${uiText("r.193")}<textarea name="note" required maxlength="2000"></textarea></label><label><input type="checkbox" required>${uiText("r.194")}</label><button>${uiText("r.195")}</button></form>`:''}`);
}
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['tutor-followup','practice-review-request','practice-review-decision'].includes(form.id))return;
  event.preventDefault();const revision=requestRevision,selection=++v4PanelRevision,button=event.submitter,values=Object.fromEntries(new FormData(form));button.disabled=true;
  try{
    if(form.id==='tutor-followup'){
      const result=await api('/teaching-sessions/'+form.dataset.session+'/turns',v1Json('POST',{attempt:values.attempt,question:values.question,focus:values.focus,action:button.value,confirmed:true},Number(form.dataset.version)));if((revision===requestRevision&&selection===v4PanelRevision))showTutor(result);
    }else if(form.id==='practice-review-request'){
      const [workspace_id,reviewer_id]=values.recipient.split(':');await api('/adaptive-exams/'+form.dataset.exam+'/review-requests',v1Json('POST',{workspace_id,reviewer_id,confirmed:true}));if((revision===requestRevision&&selection===v4PanelRevision)){if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;message(uiText("r.196"));}
    }else{
      await api('/practice-reviews/'+form.dataset.review+'/decision',v1Json('POST',{conclusion:values.conclusion,note:values.note,confirmed:true},Number(form.dataset.version)));if((revision===requestRevision&&selection===v4PanelRevision)){if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;message(uiText("r.197"));}
    }
  }catch(error){if((revision===requestRevision&&selection===v4PanelRevision)){if(form.id==='practice-review-decision')v4Show(`<p>${uiText("r.198")}</p>`);message(error.message,true);}}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-tutor-start],[data-tutor-open],[data-tutor-delete],[data-review-open],[data-review-revoke],#refresh-practice-reviews');if(!button||current!=='directions')return;
  const revision=requestRevision,selection=++v4PanelRevision;button.disabled=true;
  if(button.dataset.tutorOpen||button.dataset.tutorStart!==undefined||button.dataset.reviewOpen)v4Show(`<h2>${uiText("loading")}</h2>`);
  try{
    if(button.dataset.tutorStart!==undefined){const value=await api('/teaching-sessions',v1Json('POST',{exam_id:button.dataset.exam,answer_index:Number(button.dataset.tutorStart),confirmed:true}));if((revision===requestRevision&&selection===v4PanelRevision))showTutor(value);}
    else if(button.dataset.tutorOpen){const value=await api('/teaching-sessions/'+button.dataset.tutorOpen);if((revision===requestRevision&&selection===v4PanelRevision))showTutor(value);}
    else if(button.dataset.tutorDelete){await api('/teaching-sessions/'+button.dataset.tutorDelete,{method:'DELETE'});if((revision===requestRevision&&selection===v4PanelRevision))if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
    else if(button.dataset.reviewOpen){if((revision===requestRevision&&selection===v4PanelRevision))v4Show(`<p>${uiText("r.199")}</p>`);const value=await api('/practice-reviews/'+button.dataset.reviewOpen);if((revision===requestRevision&&selection===v4PanelRevision))showPracticeReview(value);}
    else if(button.dataset.reviewRevoke){await api('/practice-reviews/'+button.dataset.reviewRevoke,v1Json('DELETE',undefined,Number(button.dataset.version)));if((revision===requestRevision&&selection===v4PanelRevision))if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
    else if((revision===requestRevision&&selection===v4PanelRevision))if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;
  }catch(error){if((revision===requestRevision&&selection===v4PanelRevision)){if(button.dataset.reviewOpen)v4Show(`<p>${uiText("r.200")}</p>`);message(error.message,true);}}finally{if(button.isConnected)button.disabled=false;}
});
