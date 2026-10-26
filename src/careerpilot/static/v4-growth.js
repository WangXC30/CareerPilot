/* Local career directions, source-linked plans and practice. */
let v4 = {};
let v4PanelRevision=0;
const v4Fields = (items, name, label) => items.map(x => `<label><input type="checkbox" name="${name}" value="${textValue(x.id)}"> ${uiOriginal(label(x))}</label>`).join('');
const v4Options = (items, label) => items.map(x => `<option lang="" dir="auto" data-original-content value="${textValue(x.id)}">${textValue(label(x))}</option>`).join('');
const v4Values = (form, name) => new FormData(form).getAll(name);
const v4Show = html => {const box=document.querySelector('#v4-result');if(box){box.innerHTML=html;uiFormFields(box);focusLearningResult(box);}};

async function renderV4Growth(route, revision) {
  if (route !== 'directions') return false;
  if (!token) {shellView(empty(uiText('signin.directions'),uiText('signin.directionsText'),uiText('connectAccount'),'settings'));return true;}
  v4Learning={sessions:[],reviews:[],spaces:[]};
  view.lang=uiLocale;view.innerHTML=`<article class="card" role="status">${uiText("r.000")}</article>`;
  try {
    const [views,facts,projects,careers,skills,trends,exams,resumes,translations,timeline,matchingHistory,profile]=await Promise.all([
      api('/direction-views'),api('/resume-facts'),api('/projects'),api('/catalogue/career'),api('/catalogue/skill'),
      api('/trend-subscriptions'),api('/adaptive-exams'),api('/resumes'),api('/resume-translations'),api('/recruitment-timeline'),api('/matching-snapshots'),api('/profile')]);
    if(revision!==requestRevision)return true;
    v4={views,facts,projects,careers,skills,trends,exams,resumes,translations,timeline,profile};
    view.innerHTML=`<div class="grid two"><article class="card"><h2>${uiText("r.001")}</h2><p>${uiText("r.002")}</p>
      <form id="v4-direction"><label>${uiText("r.003")}<input name="title" required maxlength="100"></label>
      <fieldset><legend>${uiText("r.004")}</legend>${v4Fields(facts.filter(f=>f.source_status==='current'),'fact_ids',f=>f.text)||`<p>${uiText("r.005")}</p>`}</fieldset>
      <fieldset><legend>${uiText("r.006")}</legend>${v4Fields(projects,'project_ids',p=>p.template.title)}</fieldset><button>${uiText("r.007")}</button></form>
      ${views.map(d=>`<p>${uiOriginal(d.title)} · v${d.version} <button data-v4-view="${d.id}">${uiText("r.008")}</button></p>`).join('')}</article>
      <article class="card"><h2>${uiText("r.009")}</h2><p>${uiText("r.010")}</p><form id="v4-scenario"><fieldset><legend>${uiText("r.011")}</legend>${v4Fields(careers,'career_ids',c=>c.title)}</fieldset>
      <label>${uiText("r.012")}<input name="weekly_minutes" type="number" min="15" max="10080" value="300" required></label>
      <button name="mode" value="routes">${uiText("r.013")}</button><button name="mode" value="jobs">${uiText("r.014")}</button></form></article></div>
      <article class="card growth-detail"><h2>${uiText("r.015")}</h2>${timeline.map(j=>`<details><summary>${uiOriginal(j.title||uiText("r.016"))} · ${uiOriginal(j.deadline||uiText("r.017"))}</summary><p>${remainingFixed(j.risk.notice)}</p>${j.risk.signals.map(s=>`<blockquote>${uiOriginal(s.quote)}</blockquote>`).join('')||`<p>${uiText("r.018")}</p>`}<button data-v4-snapshot="${j.job_id}">${uiText("r.019")}</button></details>`).join('')||`<p>${uiText("r.020")}</p>`}</article>
      <article class="card growth-detail" id="matching-history"><h2>${uiText("r.021")}</h2><p>${uiText("r.022")}</p>${matchingHistory.map(s=>`<details data-matching-snapshot="${s.id}"><summary>${uiOriginal(s.title)} · ${textValue(uiDate(s.as_of_date))} · ${s.score}${uiText("r.023")}</summary><p>${uiText("r.024")}${uiOriginal(s.rule_version)}${uiText("r.025")}${uiOriginal(s.weight_version)}</p><p>${uiText("r.026")}${Object.entries(s.catalogue_versions).map(([key,version])=>textValue(key)+' v'+version).join('、')}</p><button data-v4-replay="${s.id}">${uiText("r.027")}</button></details>`).join('')||`<p>${uiText("r.028")}</p>`}</article>
      <article class="card growth-detail"><h2>${uiText("r.029")}</h2><p>${uiText("r.030")}</p><form id="v4-trend"><label>${uiText("r.031")}<input name="keyword" required maxlength="100"></label><label>${uiText("r.032")}<select name="interval_hours"><option value="24">${uiText("r.033")}</option><option value="168">${uiText("r.034")}</option><option value="6">${uiText("r.035")}</option></select></label><label><input type="checkbox" required>${uiText("r.036")}</label><button>${uiText("r.037")}</button></form>${trends.map(t=>`<details data-trend="${t.id}"><summary>${uiOriginal(t.keyword)} · ${t.sample?.count||0}${uiText("r.038")}${t.status==='active'?uiText("r.039"):uiText("r.040")}</summary><p>${remainingFixed(t.message)}</p><p>${remainingFixed(t.sample?.basis||'')}${uiText("r.041")}${t.interval_hours||24}${uiText("r.042")}</p><p>${uiText("r.043")}${textValue(uiMoment(t.checked_at,remainingZone()))}</p>${Object.entries(t.skill_delta||{}).map(([skill,delta])=>`<p>${uiOriginal(skill)}${uiText("r.045")}${delta>0?'+':''}${delta}</p>`).join('')}<details><summary>${uiText("r.046")}</summary>${(t.sample?.sources||[]).map(source=>`<p>${uiOriginal(source.id)} · v${source.version}</p>`).join('')||`<p>${uiText("r.047")}</p>`}</details>${t.status==='active'?`<button data-v4-refresh="${t.id}">${uiText("r.048")}</button><button data-v4-pause="${t.id}">${uiText("r.049")}</button>`:''}</details>`).join('')}</article>
      <div class="grid two growth-detail"><article class="card"><h2>${uiText("r.050")}</h2><form id="v4-teach"><label>${uiText("r.051")}<select name="skill_id">${v4Options(skills,s=>s.name)}</select></label><label>${uiText("r.052")}<input name="question_index" type="number" min="1" max="3" value="1" required></label><label>${uiText("r.053")}<select name="level"><option value="1">${uiText("r.054")}</option><option value="2">${uiText("r.055")}</option><option value="3">${uiText("r.056")}</option></select></label><label>${uiText("r.057")}<textarea name="attempt" maxlength="2000"></textarea></label><button>${uiText("r.058")}</button></form></article>
      <article class="card"><h2>${uiText("r.059")}</h2><p>${uiText("r.060")}</p><form id="v4-exam"><fieldset><legend>${uiText("r.051")}</legend>${v4Fields(skills,'skill_ids',s=>s.name)}</fieldset><button>${uiText("r.061")}</button></form>${exams.map(e=>`<p>${e.skills.map(uiOriginal).join('、')} · ${uiOriginal(e.status)} <button data-v4-exam="${e.id}">${uiText("r.062")}</button></p>`).join('')}</article></div>
      <article class="card growth-detail"><h2>${uiText("r.063")}</h2><p>${uiText("r.064")}</p><form id="v4-translation-load"><label>${uiText("r.065")}<select name="resume_id">${v4Options(resumes,r=>r.title)}</select></label><button>${uiText("r.066")}</button></form>${translations.map(t=>`<details><summary>${uiOriginal(t.locale)} · ${t.sources_current?uiText("r.067"):uiText("r.068")}</summary>${t.mappings.map(m=>`<p>${uiOriginal(m.text)}</p>`).join('')}<p>${remainingFixed(t.notice)}</p><h3>${uiText("r.069")}</h3>${t.brand_advice.map(advice=>`<p>${remainingFixed(advice)}</p>`).join('')}</details>`).join('')}</article>
      <article id="v4-result" class="card growth-detail" aria-live="polite"><h2>${uiText("r.070")}</h2><p>${uiText("r.071")}</p></article>`;
    await mountV4Learning(revision);
    await mountDirectionExtras(revision);
    if(revision===requestRevision)uiFormFields(view);
  } catch(error) {if(revision===requestRevision)message(error.message,true);}
  return true;
}

function showV4Exam(exam) {
  v4.exam=exam;
  v4Show(`<h2>${uiText("r.072")}</h2><p>${remainingFixed(exam.notice)}</p><p>${remainingFixed(exam.selection_rule||uiText("r.073"))}</p>${exam.current?`<form id="v4-answer"><fieldset><legend>${uiSource(exam.current.prompt)} · ${uiOriginal(exam.current.difficulty==='application'?uiText("r.074"):uiText("r.075"))}</legend>${exam.current.choices.map((choice,i)=>`<label><input type="radio" name="choice" value="${i}" required> ${uiSource(choice)}</label>`).join('')}</fieldset><button>${uiText("r.076")}</button></form>`:`<p>${uiText("r.077")}${exam.summary.answered}${uiText("r.078")}${exam.summary.correct}${uiText("r.079")}</p>`}
  ${(exam.answers||[]).map((a,index)=>`<details><summary>${uiSource(a.prompt)} · ${a.correct?uiText("r.080"):uiText("r.081")}</summary><p>${remainingFixed(a.explanation)}</p>${a.correct?'':`<button data-tutor-start="${index}" data-exam="${exam.id}">${uiText("r.082")}</button>`}</details>`).join('')}
  ${(exam.review_flags||[]).map(f=>`<p>${remainingFixed(f.basis)} ${remainingFixed(f.notice)}</p>`).join('')}
  <form id="v4-appeal"><label>${uiText("r.083")}<textarea name="text" required maxlength="2000">${escapeHTML(exam.appeal?.text||'')}</textarea></label><button>${uiText("r.084")}</button></form>${reviewConsentHTML(exam)}`);
}

function showV4Direction(direction) {
  const choices = (items, name, selected, label) => items.map(item =>
    `<label><input type="checkbox" name="${name}" value="${textValue(item.id)}" ${selected.includes(item.id)?'checked':''}> ${uiOriginal(label(item))}</label>`).join('');
  v4Show(`<h2>${uiOriginal(direction.title)} · v${direction.version}</h2>
    ${direction.facts.map(fact=>`<p>${uiOriginal(fact.text)} · ${uiOriginal(fact.source_status)}</p>`).join('')}
    <form id="v4-direction-edit" data-direction="${direction.id}" data-version="${direction.version}">
    <label>${uiText("r.085")}<input name="title" required maxlength="100" value="${textValue(direction.title)}"></label>
    <fieldset><legend>${uiText("r.086")}</legend>${choices(v4.facts,'fact_ids',direction.fact_ids,f=>f.text+' · '+f.source_status)}</fieldset>
    <fieldset><legend>${uiText("r.087")}</legend>${choices(v4.projects,'project_ids',direction.project_ids,p=>p.template.title)}</fieldset>
    <p>${uiText("r.088")}</p><button>${uiText("r.089")}</button></form>
    ${direction.history.map(history=>`<p>${uiText("r.090")}${history.version} · ${uiOriginal(history.payload.title)} <button data-v4-restore="${direction.id}" data-current="${direction.version}" data-target="${history.version}">${uiText("r.091")}</button></p>`).join('')}`);
}

document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['v4-direction','v4-direction-edit','v4-scenario','v4-trend','v4-teach','v4-exam','v4-answer','v4-appeal','v4-translation-load','v4-translation'].includes(form.id))return;
  event.preventDefault();const revision=requestRevision,selection=++v4PanelRevision,values=Object.fromEntries(new FormData(form)),button=event.submitter;button.disabled=true;
  try{
    if(form.id==='v4-direction'){await api('/direction-views',v1Json('POST',{title:values.title,fact_ids:v4Values(form,'fact_ids'),project_ids:v4Values(form,'project_ids'),confirmed:true}));if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
    else if(form.id==='v4-direction-edit'){
      await api('/direction-views/'+form.dataset.direction,v1Json('PUT',{title:values.title,fact_ids:v4Values(form,'fact_ids'),project_ids:v4Values(form,'project_ids'),confirmed:true},Number(form.dataset.version)));
      if((revision===requestRevision&&selection===v4PanelRevision)){if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;message(uiText("r.092"));}
    }
    else if(form.id==='v4-scenario'){
      const jobs=button.value==='jobs',result=await api(jobs?'/multi-target-jobs':'/career-scenarios',v1Json('POST',{career_ids:v4Values(form,'career_ids'),weekly_minutes:Number(values.weekly_minutes)}));if((revision!==requestRevision||selection!==v4PanelRevision))return;
      const cards=result.map(r=>{
        if(jobs){
          const rows=r.jobs.map(j=>`<p>${uiOriginal(j.title)}${uiText("r.093")}${Math.round(j.coverage*100)}% · ${j.deadline_days===null?uiText("r.094"):j.deadline_days+uiText("r.095")}<br>${remainingFixed(j.notice)}</p>`).join('');
          return `<h2>${uiOriginal(r.title)}</h2>${rows}`;
        }
        const steps=r.steps.map(item=>`<li>${uiOriginal(item.name)} · ${item.minutes}${uiText("r.096")}${item.requires.length?`${uiText("r.097")}${item.requires.map(key=>textValue(v4.skills.find(skill=>skill.id===key)?.name||key)+((item.satisfied_prerequisites||[]).includes(key)?uiText("r.098"):uiText("r.099"))).join('、')}`:''} · <a href="${textValue(item.source_url)}" target="_blank" rel="noopener noreferrer">${uiText("r.100")}</a></li>`).join('');
        const notes=[...r.assumptions,...r.risks].map(note=>`<p>${remainingFixed(note)}</p>`).join('');
        return `<h2>${uiOriginal(r.title)}</h2><p>${uiText("r.101")}${r.foundation_minutes}${uiText("r.102")}${r.estimated_weeks_range.lower}–${r.estimated_weeks_range.upper}${uiText("r.103")}</p><p>${remainingFixed(r.estimated_weeks_range.basis)}</p><ol>${steps}</ol>${(r.paths||[]).map(path=>`<section class="learning-path"><h3>${remainingFixed(path.title)}</h3><p>${path.minutes}${uiText("r.104")}${path.estimated_weeks}${uiText("r.105")}${path.additional_practice_minutes}${uiText("r.106")}</p><p>${remainingFixed(path.prerequisites)}</p><p>${uiText("r.107")}${remainingFixed(path.resources)}。${remainingFixed(path.cost_basis)}</p><p>${remainingFixed(path.risk)}</p><p>${remainingFixed(path.assumption)}</p><p>${uiText("r.108")}${Object.entries(path.source_versions).map(([key,version])=>textValue(key)+' v'+version).join('、')||uiText("r.109")}</p></section>`).join('')}${notes}`;
      });
      v4Show(cards.join(''));
    }else if(form.id==='v4-trend'){await api('/trend-subscriptions',v1Json('POST',{keyword:values.keyword,interval_hours:Number(values.interval_hours),confirmed:true}));if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
    else if(form.id==='v4-teach'){
      const body={skill_id:values.skill_id,question_index:Number(values.question_index)-1,level:Number(values.level),attempt:values.attempt,confirmed:true};
      const r=await api('/teaching-turns',v1Json('POST',body));
      if((revision===requestRevision&&selection===v4PanelRevision)){
        v4Show(`<h2>${uiSource(r.question)}</h2><p>${remainingFixed(r.hint)}</p><a href="${textValue(r.source_url)}" target="_blank" rel="noopener noreferrer">${uiText("r.110")}</a><p>${remainingFixed(r.notice)}</p>`);
      }
    }
    else if(form.id==='v4-exam'){const r=await api('/adaptive-exams',v1Json('POST',{skill_ids:v4Values(form,'skill_ids'),confirmed:true}));if((revision===requestRevision&&selection===v4PanelRevision))showV4Exam(r);}
    else if(form.id==='v4-answer'||form.id==='v4-appeal'){const exam=v4.exam,isAnswer=form.id==='v4-answer';const r=await api('/adaptive-exams/'+exam.id+(isAnswer?'/answer':'/appeal'),v1Json('POST',isAnswer?{choice:Number(values.choice),confirmed:true}:{text:values.text,confirmed:true},exam.version));if((revision===requestRevision&&selection===v4PanelRevision))showV4Exam(r);}
    else if(form.id==='v4-translation-load'){const versions=await api('/resumes/'+values.resume_id+'/versions');if((revision!==requestRevision||selection!==v4PanelRevision))return;const version=versions[0];if(!version)throw new Error(uiText("r.111"));v4.translationVersion=version;v4Show(`<h2>${uiText("r.112")}</h2><form id="v4-translation"><label>${uiText("r.113")}<select name="locale"><option value="en-US">English</option><option value="zh-CN">${uiText("r.114")}</option></select></label>${version.facts.map(f=>`<label>${uiOriginal(f.text)}<textarea name="fact-${f.id}" required maxlength="4000"></textarea></label>`).join('')}<label><input type="checkbox" required>${uiText("r.115")}</label><button>${uiText("r.116")}</button></form>`);}
    else{const version=v4.translationVersion;await api('/resume-translations',v1Json('POST',{resume_version_id:version.id,locale:values.locale,mappings:version.facts.map(f=>({fact_id:f.id,text:values['fact-'+f.id]})),confirmed:true}));if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
  }catch(error){if((revision===requestRevision&&selection===v4PanelRevision))message(error.message,true);}finally{if(button.isConnected)button.disabled=false;}
});

document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-v4-view],[data-v4-restore],[data-v4-exam],[data-v4-refresh],[data-v4-pause],[data-v4-snapshot],[data-v4-replay]');if(!b||current!=='directions')return;
  const revision=requestRevision,selection=++v4PanelRevision;b.disabled=true;
  if(b.dataset.v4View||b.dataset.v4Replay)v4Show(`<h2>${uiText("loading")}</h2>`);
  try{
    if(b.dataset.v4View){const d=await api('/direction-views/'+b.dataset.v4View);if((revision===requestRevision&&selection===v4PanelRevision))showV4Direction(d);}
    else if(b.dataset.v4Restore){await api('/direction-views/'+b.dataset.v4Restore+'/restore',v1Json('POST',{target_version:Number(b.dataset.target),confirmed:true},Number(b.dataset.current)));if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
    else if(b.dataset.v4Exam)showV4Exam(v4.exams.find(e=>e.id===b.dataset.v4Exam));
    else if(b.dataset.v4Snapshot){const r=await api('/matching-snapshots',v1Json('POST',{job_id:b.dataset.v4Snapshot,confirmed:true}));if((revision===requestRevision&&selection===v4PanelRevision)){if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;v4Show(`<h2>${uiText("r.117")}</h2><p>${uiOriginal(r.result.title)} · ${r.result.score}${uiText("r.118")}${uiOriginal(r.result.eligibility)}</p><button data-v4-replay="${r.id}">${uiText("r.119")}</button>`);}}
    else if(b.dataset.v4Replay){const r=await api('/matching-snapshots/'+b.dataset.v4Replay+'/replay');if((revision===requestRevision&&selection===v4PanelRevision))v4Show(`<h2>${r.identical?uiText("r.120"):uiText("r.121")}</h2><p>${remainingFixed(r.notice)}</p><p>${uiText("r.024")}${uiOriginal(r.rule_version)}${uiText("r.122")}${uiOriginal(r.weight_version)}${uiText("r.123")}${textValue(uiDate(r.as_of_date))}</p><p>${uiText("r.124")}${r.result.skills.map(s=>textValue(s.skill_id)+'（'+uiOriginal(s.evidence.quote)+'）').join('、')||uiText("r.125")}</p><p>${uiText("r.026")}${Object.entries(r.catalogue_versions).map(([key,version])=>textValue(key)+' v'+version).join('、')}</p>`);}
    else{await api('/trend-subscriptions/'+(b.dataset.v4Refresh||b.dataset.v4Pause)+(b.dataset.v4Refresh?'/refresh':''),b.dataset.v4Refresh?v1Json('POST',{confirmed:true}):{method:'DELETE'});if(!await remainingRefresh(revision,'directions',()=>selection===v4PanelRevision))return;}
  }catch(error){if((revision===requestRevision&&selection===v4PanelRevision))message(error.message,true);}finally{if(b.isConnected)b.disabled=false;}
});
