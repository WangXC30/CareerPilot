/* Transient model-observation review. This page never enables model collaboration. */
const modelComparisonTexts={
  title:['模型对照临时复核','Temporary model comparison review'],
  help:['粘贴已授权的数据集与带步骤的冻结观察。仅重算指标，不重新调用模型；重新加载内容、离开页面或切换语言会清空。','Paste an authorized dataset and frozen observations with steps. This rescores metrics without model calls. Reloading content, leaving the page, or changing language clears the view.'],
  warning:['导入观察的来源未经独立核验；重算一致不证明模型调用真实或样本有代表性。多模型协作保持关闭。','Imported observations are not independently verified. Matching scores do not prove real model calls or representative samples. Multi-model collaboration remains off.'],
  dataset:['授权数据集 JSON','Authorized dataset JSON'],report:['冻结观察 JSON（含 rows）','Frozen observations JSON (including rows)'],
  consent:['我有权处理这些样例，并同意在本地临时核对','I may process these examples and consent to this temporary local review'],
  submit:['核对版本并重算','Check versions and rescore'],clear:['清空临时内容','Clear temporary content'],
  ready:['重算结果与导入指标一致','Rescored metrics match the imported metrics'],
  mismatch:['导入指标与重算不一致；以下显示重算值','Imported metrics differ. Rescored values are shown below'],
  single:['单次决策与工具','Single decision with tools'],reviewed:['决策与复核','Decision and review'],
  arm:['对照方案','Comparison arm'],quality:['正确数 / 全部样例','Correct / all examples'],
  errors:['失败或未执行','Failed or unrun'],cost:['已知估算微费用','Known estimated micro-cost'],
  latency:['平均耗时 ms（已执行样本）','Mean latency, ms (attempted samples)'],
  unknown:['未知或不完整','Unknown or incomplete'],steps:['冻结步骤（不重新执行）','Frozen steps (not re-executed)'],
  row:['第 {repeat} 轮 · 样例 {case} · {arm}','Repeat {repeat} · Example {case} · {arm}'],
  decision:['决策','Decision'],review:['复核','Review'],success:['完成','Completed'],
  notrun:['未执行','Not run'],stopped:['失败或停止（状态码）','Failed or stopped (status code)'],
  gains:['每轮质量差（复核减单次）','Quality gain per repeat (reviewed minus single)'],
  groups:['分组正确数 / 全部样例','Grouped correct / all examples'],
  denominator:['失败与未执行都计入分母；不完整评测不能证明模型收益。','Failures and unrun cases remain in the denominator. Incomplete comparisons do not establish model benefits.'],
  repeatGain:['第 {repeat} 轮：{gain}','Repeat {repeat}: {gain}'],
  manualTitle:['逐条人工修正','Manual corrections'],
  manualHelp:['在步骤下选择修正并填写本人依据，再统一核对。原观察、模型指标和来源标签保持不变；备注仅本次暂存。','Choose corrections below the steps and record your basis, then review them together. Original observations, model metrics and source labels remain unchanged. Notes are temporary.'],
  original:['原观察','Original observation'],noObservation:['未形成有效观察','No valid observation'],
  correction:['人工判断','Human decision'],keep:['保留原观察（不修正）','Keep original (no correction)'],
  markTrue:['true（标注需核查）','true (flag for review)'],markFalse:['false（未标注）','false (not flagged)'],
  correctionNote:['本人核对依据','My review basis'],noteRequired:['每条人工修正均须填写本人核对依据。','Provide your review basis for each correction.'],
  tooMany:['一次最多核对 100 条人工修正。','Review at most 100 manual corrections at once.'],
  manualConsent:['我确认这些是本人判断，不改写原模型观察或认定模型收益','I confirm these are my judgments, without rewriting model observations or establishing model benefits'],
  applyCorrections:['核对人工修正参考结果','Review human-assisted results'],clearCorrections:['撤回全部人工修正','Withdraw all manual corrections'],
  manualResult:['人工修正参考结果','Human-assisted reference results'],
  manualWarning:['以下含人工干预，不是模型质量或启用依据；不会调用模型或保存修正记录。','These results include human intervention and are not model-quality or activation evidence. This does not call models or save corrections.'],
  edited:['人工修正条数','Manual corrections'],
  invalid:['请输入有效 JSON；保留当前输入以便核对。','Enter valid JSON. Your current input has been retained.'],
};
for(const [key,[zh,en]] of Object.entries(modelComparisonTexts)){localeResources['zh-CN']['mc.'+key]=zh;localeResources['en-US']['mc.'+key]=en;}
let modelComparisonSelection=0,modelComparisonPending=null;
function mountModelComparison(){
  const card=document.createElement('article');card.id='model-comparison';card.className='card growth-detail';card.lang=uiLocale;
  card.innerHTML=`<h2>${uiText('mc.title')}</h2><p>${uiText('mc.help')}</p><p>${uiText('mc.warning')}</p><form id="model-comparison-form" autocomplete="off"><label>${uiText('mc.dataset')}<textarea name="dataset" required maxlength="1048576"></textarea></label><label>${uiText('mc.report')}<textarea name="report" required maxlength="1048576"></textarea></label><label><input name="rights" type="checkbox" required>${uiText('mc.consent')}</label><button>${uiText('mc.submit')}</button> <button type="button" id="clear-model-comparison" class="secondary">${uiText('mc.clear')}</button></form><div id="model-comparison-result" aria-live="polite"></div>`;
  view.append(card);uiFormFields(card);
}
function clearModelPreview(){modelComparisonSelection++;modelComparisonPending?.abort();modelComparisonPending=null;document.querySelector('#model-comparison-result')?.replaceChildren();const button=document.querySelector('#model-comparison-form button[type=submit],#model-comparison-form button:not([type])');if(button)button.disabled=false;}
document.addEventListener('input',event=>{if(event.target.closest('#model-comparison-form'))clearModelPreview();});
document.addEventListener('click',event=>{if(event.target.id==='clear-model-comparison'){clearModelPreview();message('');document.querySelector('#model-comparison-form').reset();document.querySelector('#model-comparison-form textarea').focus();}});
document.addEventListener('submit',async event=>{
  const form=event.target;if(form.id!=='model-comparison-form')return;
  event.preventDefault();clearModelPreview();message('');const selection=modelComparisonSelection,revision=requestRevision,button=event.submitter;button.disabled=true;const controller=new AbortController();modelComparisonPending=controller;
  const valid=()=>selection===modelComparisonSelection&&revision===requestRevision&&current==='local-tools';
  try{
    let dataset,report;try{dataset=JSON.parse(form.elements.dataset.value);report=JSON.parse(form.elements.report.value);}catch{throw new Error(uiText('mc.invalid'));}
    const result=await api('/agent-experiments/model-preview',{...v1Json('POST',{dataset,report,confirmed:true}),signal:controller.signal});if(!valid())return;
    const box=document.querySelector('#model-comparison-result');
    box.innerHTML=`<h3>${uiText(result.identical?'mc.ready':'mc.mismatch')}</h3><p>${uiText('mc.warning')}</p><p>${uiOriginal(result.dataset_version)} · ${uiOriginal(result.dataset_sha256)}</p><p>${uiOriginal(result.primary_model)} / ${uiOriginal(result.reviewer_model)}</p><div class="table-wrap" tabindex="0" role="region" aria-label="${uiText('mc.title')}"><table><thead><tr>${['arm','quality','errors','cost','latency'].map(key=>`<th scope="col">${uiText('mc.'+key)}</th>`).join('')}</tr></thead><tbody>${Object.entries(result.summary.arms).map(([arm,s])=>`<tr><th scope="row">${uiText('mc.'+arm)}</th><td>${s.correct} / ${s.cases}</td><td>${s.errors}</td><td>${s.estimated_cost_micros}${s.cost_complete?'':' · '+uiText('mc.unknown')}</td><td>${s.mean_latency_ms===null?uiText('mc.unknown'):Number(s.mean_latency_ms).toFixed(2)}</td></tr>`).join('')}</tbody></table></div><p>${uiText('mc.denominator')}</p><h3>${uiText('mc.gains')}</h3><ul>${result.summary.repeat_quality_gains.map((gain,index)=>`<li>${textValue(uiFormat('mc.repeatGain',{repeat:index+1,gain:(gain*100).toFixed(1)+'%'}))}</li>`).join('')}</ul><h3>${uiText('mc.groups')}</h3><ul>${Object.entries(result.summary.groups).map(([group,arms])=>`<li>${uiOriginal(group)} · ${uiText('mc.single')}: ${arms.single.correct}/${arms.single.cases} · ${uiText('mc.reviewed')}: ${arms.reviewed.correct}/${arms.reviewed.cases}</li>`).join('')}</ul><h3>${uiText('mc.steps')}</h3><form id="model-corrections-form" autocomplete="off"><h3>${uiText('mc.manualTitle')}</h3><p>${uiText('mc.manualHelp')}</p>${result.rows.map(row=>`<details data-correction-row data-repeat="${row.repeat}" data-case="${row.case}" data-arm="${row.arm}"><summary>${textValue(uiFormat('mc.row',{repeat:row.repeat+1,case:row.case+1,arm:uiText('mc.'+row.arm)}))}</summary><p>${row.status==='success'?uiText('mc.success'):uiText('mc.stopped')+' · '+textValue(row.status)}</p><p>${uiText('mc.original')}: ${row.prediction===null?uiText('mc.noObservation'):textValue(String(row.prediction))}</p><ol>${row.steps.map(step=>`<li>${uiText('mc.'+step.stage)} · ${step.status==='success'?uiText('mc.success'):textValue(step.status)}</li>`).join('')||`<li>${uiText('mc.notrun')}</li>`}</ol><label>${uiText('mc.correction')}<select data-human-prediction><option value="">${uiText('mc.keep')}</option><option value="true">${uiText('mc.markTrue')}</option><option value="false">${uiText('mc.markFalse')}</option></select></label><label>${uiText('mc.correctionNote')}<textarea data-human-note maxlength="1000"></textarea></label></details>`).join('')}<label><input type="checkbox" required>${uiText('mc.manualConsent')}</label><button>${uiText('mc.applyCorrections')}</button> <button type="button" class="secondary" id="clear-model-corrections">${uiText('mc.clearCorrections')}</button></form><div id="model-correction-result" aria-live="polite"></div>`;
    uiFormFields(box);focusLearningResult(box);
  }catch(error){if(valid())message(error.message,true);}finally{if(modelComparisonPending===controller)modelComparisonPending=null;if(button.isConnected&&selection===modelComparisonSelection)button.disabled=false;}
});

function discardModelComparison(){clearModelPreview();document.querySelector('#model-comparison-form')?.reset();}
window.addEventListener('pagehide',discardModelComparison);
window.addEventListener('pageshow',event=>{if(event.persisted)discardModelComparison();});

function invalidateModelCorrections(){
  modelComparisonSelection++;modelComparisonPending?.abort();modelComparisonPending=null;
  document.querySelector('#model-correction-result')?.replaceChildren();
  const button=document.querySelector('#model-corrections-form button:not([type])');if(button)button.disabled=false;
}
document.addEventListener('input',event=>{
  if(!event.target.closest('#model-corrections-form'))return;
  const row=event.target.closest('[data-correction-row]');
  if(row)row.querySelector('[data-human-note]').required=!!row.querySelector('[data-human-prediction]').value;
  invalidateModelCorrections();
});
document.addEventListener('click',event=>{
  if(event.target.id!=='clear-model-corrections')return;
  const form=document.querySelector('#model-corrections-form');form.reset();
  form.querySelectorAll('[data-human-note]').forEach(note=>note.required=false);
  invalidateModelCorrections();message('');form.querySelector('h3').focus();
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(form.id!=='model-corrections-form')return;
  event.preventDefault();invalidateModelCorrections();message('');
  const revision=requestRevision,selection=modelComparisonSelection,button=event.submitter;
  const controller=new AbortController();modelComparisonPending=controller;button.disabled=true;
  const valid=()=>revision===requestRevision&&selection===modelComparisonSelection&&current==='local-tools';
  try{
    const corrections=[];
    for(const row of form.querySelectorAll('[data-correction-row]')){
      const choice=row.querySelector('[data-human-prediction]').value;if(!choice)continue;
      const note=row.querySelector('[data-human-note]').value;if(!note.trim())throw new Error(uiText('mc.noteRequired'));
      corrections.push({repeat:Number(row.dataset.repeat),case:Number(row.dataset.case),arm:row.dataset.arm,prediction:choice==='true',note});
    }
    if(corrections.length>100)throw new Error(uiText('mc.tooMany'));
    const source=document.querySelector('#model-comparison-form');
    const body={dataset:JSON.parse(source.elements.dataset.value),report:JSON.parse(source.elements.report.value),corrections,confirmed:true};
    const result=await api('/agent-experiments/model-preview',{...v1Json('POST',body),signal:controller.signal});if(!valid())return;
    const box=document.querySelector('#model-correction-result');
    box.innerHTML=`<h3>${uiText('mc.manualResult')}</h3><p>${uiText('mc.manualWarning')}</p><p>${uiText('mc.edited')}: ${result.human_assisted.correction_count}</p><ul>${Object.entries(result.human_assisted.arms).map(([arm,value])=>`<li>${uiText('mc.'+arm)}: ${value.correct}/${value.cases} · ${uiText('mc.edited')}: ${value.corrected}</li>`).join('')}</ul>`;
    uiFormFields(box);focusLearningResult(box);
  }catch(error){if(valid())message(error.message,true);}finally{if(modelComparisonPending===controller)modelComparisonPending=null;if(button.isConnected&&selection===modelComparisonSelection)button.disabled=false;}
});
