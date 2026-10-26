let mediaSearchRevision=0;
/* Explicit owner records and private authorized-index search. */
const practiceKinds=uiLocaleLabels({written:"r.378",algorithm:"r.379"});
const practiceLevels=uiLocaleLabels({easy:"r.380",medium:"r.381",hard:"r.382"});
const practiceOutcomes=uiLocaleLabels({passed:"r.383",failed:"r.384",unjudged:"r.385"});
function mediaResults(items){return items.map(m=>`<details><summary>${uiOriginal(m.title)} · ${uiOriginal(m.kind)}</summary><p>${uiOriginal(m.license_basis||'')}${uiText("r.386")}${textValue(uiMoment(new Date(m.expires_at*1000),v4Tools.profile?.timezone))}</p><p>${uiOriginal(m.source_url||uiText("r.387"))}</p>${m.segments.map(s=>`<p>${uiOriginal(s.anchor)}：${uiOriginal(s.text)}</p>`).join('')}<button data-tool-media-delete="${m.id}">${uiText("r.233")}</button></details>`).join('')||`<p>${uiText("r.388")}</p>`;}
async function mountPracticeTools(revision){
  const [practice,subs,stats]=await Promise.all([api('/practice-attempts'),api('/content-subscriptions'),api('/analytics-summary')]);
  if(revision!==requestRevision)return;
  const card=document.createElement('article');card.className='card growth-detail';card.id='practice-tools';
  card.innerHTML=`<h2>${uiText("r.389")}</h2><p>${remainingFixed(practice.summary.notice)}</p><form id="practice-add"><label>${uiText("r.390")}<input name="title" required maxlength="200"></label><label>${uiText("r.218")}<select name="kind">${Object.entries(practiceKinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><label>${uiText("r.391")}<select name="difficulty">${Object.entries(practiceLevels).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><label>${uiText("r.392")}<input name="tags" maxlength="300"></label><label>${uiText("r.220")}<input name="practiced_on" type="date" required></label><label>${uiText("r.393")}<input name="minutes" type="number" min="0" max="1440" value="0" required></label><label>${uiText("r.394")}<select name="outcome">${Object.entries(practiceOutcomes).map(([k,v])=>`<option value="${k}" ${k==='unjudged'?'selected':''}>${v}</option>`).join('')}</select></label><label>${uiText("r.395")}<textarea name="reflection" maxlength="4000"></textarea></label><button>${uiText("r.396")}</button></form><h3>${uiText("r.397")}</h3>${practice.summary.cells.map(c=>`<p>${practiceKinds[c.kind]} · ${practiceLevels[c.difficulty]} · ${uiOriginal(c.tag)}：${c.attempts}${uiText("r.398")}${c.passed}/${c.judged}${uiText("r.399")}${c.success_rate===null?uiText("r.400"):Math.round(c.success_rate*100)+'%'}。${remainingFixed(c.review_suggestion)}</p>`).join('')||`<p>${uiText("r.401")}</p>`}${practice.attempts.map(a=>`<details data-practice="${a.id}"><summary>${uiOriginal(a.title)} · ${practiceOutcomes[a.outcome]}</summary><p>${textValue(uiDate(a.practiced_on))} · ${a.minutes}${uiText("r.402")}${uiOriginal(a.tags.join('、'))}</p><p>${uiOriginal(a.reflection)}</p><button data-practice-delete="${a.id}">${uiText("r.403")}</button></details>`).join('')}
    <h2>${uiText("r.404")}</h2><form id="media-search"><label>${uiText("r.405")}<input name="q" maxlength="200"></label><button>${uiText("r.406")}</button></form><div id="media-results"></div><h3>${uiText("r.407")}</h3><p>${uiText("r.408")}</p><form id="content-subscribe"><label>${uiText("r.031")}<input name="keyword" required maxlength="100"></label><label>${uiText("r.409")}<select name="interval_hours"><option value="24">${uiText("r.033")}</option><option value="168">${uiText("r.034")}</option><option value="6">${uiText("r.035")}</option></select></label><label><input type="checkbox" required>${uiText("r.410")}</label><button>${uiText("r.411")}</button></form>${subs.map(s=>`<p>${uiOriginal(s.keyword)} · ${s.status==='active'?uiText("r.039"):uiText("r.040")} ${s.status==='active'?`<button data-content-cancel="${s.id}" data-version="${s.version}">${uiText("r.412")}</button>`:''}</p>`).join('')}
    <h2>${uiText("r.413")}</h2>${v4Tools.experiments.map(e=>`<details><summary>${uiText("r.414")}${textValue(uiMoment(e.created_at,remainingZone()))}</summary><button data-experiment-replay="${e.id}">${uiText("r.415")}</button><p>${uiOriginal(e.human_review?.note||uiText("r.416"))}</p><form class="experiment-review" data-experiment="${e.id}" data-version="${e.version}"><label>${uiText("r.417")}<textarea name="note" required maxlength="2000"></textarea></label><button>${uiText("r.418")}</button></form></details>`).join('')}<h2>${uiText("r.419")}</h2><p>${stats.enabled?(stats.effective_growth_week?uiText("r.420"):uiText("r.421")):uiText("r.422")}</p><p>${remainingFixed(stats.notice)}</p>`;
  view.append(card);uiFormFields(card);
}
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['practice-add','media-search','content-subscribe'].includes(form.id))return;
  event.preventDefault();const revision=requestRevision,searchSelection=form.id==='media-search'?++mediaSearchRevision:mediaSearchRevision,values=Object.fromEntries(new FormData(form)),button=event.submitter;button.disabled=true;
  try{
    if(form.id==='media-search'){
      const selection=searchSelection;document.querySelector('#media-results').textContent=uiText('loading');const items=await api('/media-index?q='+encodeURIComponent(values.q));if(revision===requestRevision&&selection===mediaSearchRevision){const box=document.querySelector('#media-results');box.innerHTML=mediaResults(items);uiFormFields(box);}
    }else if(form.id==='content-subscribe'){
      await api('/content-subscriptions',v1Json('POST',{keyword:values.keyword,interval_hours:Number(values.interval_hours),confirmed:true}));if(revision===requestRevision){if(!await remainingRefresh(revision,'local-tools'))return;toolStatus(uiText("r.423"));}
    }else{
      await api('/practice-attempts',v1Json('POST',{...values,tags:[...new Set(values.tags.split(/[,，]/).map(x=>x.trim()).filter(Boolean))],minutes:Number(values.minutes),confirmed:true}));if(revision===requestRevision){if(!await remainingRefresh(revision,'local-tools'))return;toolStatus(uiText("r.424"));}
    }
  }catch(error){if(revision===requestRevision&&(form.id!=='media-search'||searchSelection===mediaSearchRevision))message(error.message,true);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-practice-delete],[data-content-cancel]');if(!b)return;
  const revision=requestRevision;b.disabled=true;
  try{await api(b.dataset.practiceDelete?'/practice-attempts/'+b.dataset.practiceDelete:'/content-subscriptions/'+b.dataset.contentCancel,v1Json('DELETE',undefined,b.dataset.version?Number(b.dataset.version):undefined));if(revision===requestRevision){if(!await remainingRefresh(revision,'local-tools'))return;toolStatus(uiText("r.425"));}}
  catch(error){if(revision===requestRevision)message(error.message,true);}finally{if(b.isConnected)b.disabled=false;}
});

document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('.experiment-review'))return;
  event.preventDefault();const revision=requestRevision,button=event.submitter;button.disabled=true;
  try{await api('/agent-experiments/'+form.dataset.experiment+'/review',v1Json('POST',{note:new FormData(form).get('note'),confirmed:true},Number(form.dataset.version)));if(revision===requestRevision){if(!await remainingRefresh(revision,'local-tools'))return;toolStatus(uiText("r.426"));}}
  catch(error){if(revision===requestRevision)message(error.message,true);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-experiment-replay]');if(!b)return;
  const revision=requestRevision;b.disabled=true;
  try{const result=await api('/agent-experiments/'+b.dataset.experimentReplay+'/replay');if(revision===requestRevision)toolStatus(result.results.every(r=>r.identical)?uiText("r.427"):uiText("r.428"));}
  catch(error){if(revision===requestRevision)message(error.message,true);}finally{if(b.isConnected)b.disabled=false;}
});

document.addEventListener('input',event=>{if(event.target.matches('#media-search input')){mediaSearchRevision++;document.querySelector('#media-results').replaceChildren();}});
