/* Local-only frozen specialist review. No model requests, storage or activation. */
const expertReviewTexts={
  title:['四类专家冻结流程复核','Specialist workflow review'],
  help:['核对已授权场景与冻结步骤，只在本页临时重算。人工修正不改写原观察或模型指标。','Review authorized cases and frozen steps temporarily on this page. Human corrections do not rewrite observations or model metrics.'],
  warning:['来源未经独立核验；下一步/引用评分不证明开放式专家质量或代表性收益。协作保持关闭。','Provenance is not independently verified. Next-step and citation scores do not establish open-ended expert quality or representative benefits. Collaboration stays off.'],
  cases:['授权场景 JSON（case 数组或含 cases 的对象）','Authorized cases JSON (array or object with cases)'],
  references:['引用依据与版本','Source references and versions'],
  stepDecision:['本步骤结构化结果','Structured step result'],
  referencesRequired:['人工判断至少引用一项当前来源版本。','A human decision must cite at least one current source version.'],
  generalist:['通用 Agent','Generalist agent'],specialist:['专家复核','Specialist review'],
  NOT_RUN:['未执行','Not run'],BUDGET_LIMIT:['达到预算预留上限','Budget reservation limit reached'],
  MODEL_TIMEOUT:['模型请求超时','Model request timed out'],MODEL_PROVIDER_ERROR:['供应商请求失败','Provider request failed'],
  MODEL_SCHEMA_ERROR:['响应格式或用量不合规','Invalid response format or usage'],CONSENT_REQUIRED:['缺少调用授权','Model-call consent required'],
  CONSENT_REVOKED:['调用授权已撤回','Model-call consent revoked'],QUOTA_EXCEEDED:['达到 token 配额','Token quota reached'],
  MODEL_LIMIT:['请求超出限制','Request exceeds limits'],INVALID_SCHEMA:['输出约束无效','Invalid output schema'],
  MODEL_GROUNDING_ERROR:['决策或来源引用不合规','Invalid decision or source reference'],
  career:['职业专家','Career specialist'],learning:['学习专家','Learning specialist'],job:['求职专家','Job specialist'],interview:['面试专家','Interview specialist'],
  request_evidence:['请求证据并保留未知','Request evidence and preserve unknowns'],
  compare_directions:['比较方向，由本人选择','Compare directions; owner chooses'],
  reduce_scope:['缩减学习范围','Reduce study scope'],repair_prerequisites:['修复先修缺口','Repair prerequisite gaps'],
  preview_plan:['仅预览计划','Preview the plan only'],preserve_locked_tasks:['保留锁定任务','Preserve locked tasks'],
  request_fact_check:['请求事实核对','Request a fact check'],draft_from_verified_facts:['依据已确认事实起草','Draft from verified facts'],
  require_submission_approval:['要求渠道授权与提交审批','Require channel authorization and submission approval'],
  give_practice_feedback:['给出练习反馈','Give practice feedback'],request_answer:['请求本人回答','Request the owner’s answer'],
  request_assessment_evidence:['请求独立能力验证','Request independent assessment evidence'],
};
for(const [key,[zh,en]] of Object.entries(expertReviewTexts)){localeResources['zh-CN']['ec.'+key]=zh;localeResources['en-US']['ec.'+key]=en;}
let expertReviewSelection=0,expertReviewPending=null;
function mountExpertComparison(){
  const card=document.createElement('article');card.id='expert-comparison';card.className='card growth-detail';card.lang=uiLocale;
  card.innerHTML=`<h2>${uiText('ec.title')}</h2><p>${uiText('ec.help')}</p><p>${uiText('ec.warning')}</p><form id="expert-comparison-form" autocomplete="off"><label>${uiText('ec.cases')}<textarea name="cases" lang="" dir="auto" required maxlength="1048576"></textarea></label><label>${uiText('mc.report')}<textarea name="report" lang="" dir="auto" required maxlength="1048576"></textarea></label><label><input type="checkbox" required>${uiText('mc.consent')}</label><button>${uiText('mc.submit')}</button> <button type="button" id="clear-expert-comparison" class="secondary">${uiText('mc.clear')}</button></form><div id="expert-comparison-result" aria-live="polite"></div>`;
  view.append(card);uiFormFields(card);
}
function clearExpertPreview(){
  expertReviewSelection++;expertReviewPending?.abort();expertReviewPending=null;
  document.querySelector('#expert-comparison-result')?.replaceChildren();
  const button=document.querySelector('#expert-comparison-form button:not([type])');if(button)button.disabled=false;
}
function invalidateExpertCorrections(){
  expertReviewSelection++;expertReviewPending?.abort();expertReviewPending=null;
  document.querySelector('#expert-correction-result')?.replaceChildren();
  const button=document.querySelector('#expert-corrections-form button:not([type])');if(button)button.disabled=false;
}
function expertReviewInput(){
  const form=document.querySelector('#expert-comparison-form');
  try{const parsed=JSON.parse(form.elements.cases.value),cases=Array.isArray(parsed)?parsed:parsed.cases,report=JSON.parse(form.elements.report.value);if(!Array.isArray(cases))throw new Error();return {cases,report,confirmed:true};}
  catch{throw new Error(uiText('mc.invalid'));}
}
function expertAction(decision){return decision?textValue(uiText('ec.'+decision.action)):uiText('mc.noObservation');}
function expertStatus(code){return code==='success'?uiText('mc.success'):uiText('ec.'+code)+' · '+textValue(code);}
function expertReferences(decision){return decision?decision.citations.map(ref=>`${textValue(ref.id)} · v${ref.version}`).join(', '):uiText('mc.noObservation');}
function expertMetrics(result){
  return `<h3>${uiText(result.identical?'mc.ready':'mc.mismatch')}</h3><p>${uiText('ec.warning')}</p><p>${uiOriginal(result.dataset_sha256)}</p><div class="table-wrap" tabindex="0" role="region" aria-label="${uiText('ec.title')}"><table><caption class="visually-hidden">${uiText('ec.title')}</caption><thead><tr>${['arm','quality','errors','cost','latency'].map(key=>`<th scope="col">${uiText('mc.'+key)}</th>`).join('')}</tr></thead><tbody>${Object.entries(result.summary.arms).map(([arm,s])=>`<tr><th scope="row">${uiText('mc.'+arm)}</th><td>${s.correct} / ${s.cases}</td><td>${s.errors}</td><td>${s.estimated_cost_micros}${s.cost_complete?'':' · '+uiText('mc.unknown')}</td><td>${s.mean_latency_ms===null?uiText('mc.unknown'):Number(s.mean_latency_ms).toFixed(2)}</td></tr>`).join('')}</tbody></table></div><p>${uiText('mc.denominator')}</p><h3>${uiText('mc.gains')}</h3><ul>${result.summary.repeat_quality_gains.map((gain,index)=>`<li>${textValue(uiFormat('mc.repeatGain',{repeat:index+1,gain:(gain*100).toFixed(1)+'%'}))}</li>`).join('')}</ul><h3>${uiText('mc.groups')}</h3><ul>${Object.entries(result.summary.groups).map(([role,arms])=>`<li>${uiText('ec.'+role)} · ${uiText('mc.single')}: ${arms.single.correct}/${arms.single.cases} · ${uiText('mc.reviewed')}: ${arms.reviewed.correct}/${arms.reviewed.cases}</li>`).join('')}</ul>`;
}
function expertSteps(result){
  return `<h3>${uiText('mc.steps')}</h3><form id="expert-corrections-form" autocomplete="off"><h3 tabindex="-1">${uiText('mc.manualTitle')}</h3><p>${uiText('mc.manualHelp')}</p>${result.rows.map(row=>{
    const c=result.cases[row.case];return `<details data-expert-row data-repeat="${row.repeat}" data-case="${row.case}" data-arm="${row.arm}"><summary>${textValue(uiFormat('mc.row',{repeat:row.repeat+1,case:row.case+1,arm:uiText('mc.'+row.arm)}))} · ${textValue(c.id)} · ${uiText('ec.'+c.specialist)}</summary><p>${expertStatus(row.error===null?'success':row.error)}</p><p>${uiText('mc.original')}: <span data-expert-original>${expertAction(row.decision)}</span></p>${row.decision?`<p>${uiText('ec.references')}: ${expertReferences(row.decision)}</p>`:''}<ol>${row.steps.map(step=>`<li>${uiText('ec.'+step.stage)} · ${expertStatus(step.status)}${step.decision?`<p data-expert-step-decision>${uiText('ec.stepDecision')}: ${expertAction(step.decision)} · ${expertReferences(step.decision)}</p>`:''}</li>`).join('')||`<li>${uiText('mc.notrun')}</li>`}</ol><label>${uiText('mc.correction')}<select data-expert-action><option value="">${uiText('mc.keep')}</option>${c.allowed_actions.map(action=>`<option value="${textValue(action)}">${uiText('ec.'+action)}</option>`).join('')}</select></label><fieldset><legend>${uiText('ec.references')}</legend>${c.sources.map(ref=>`<label><input type="checkbox" data-expert-source data-id="${textValue(ref.id)}" data-version="${ref.version}" checked> ${textValue(ref.id)} · v${ref.version}</label>`).join('')}</fieldset><label>${uiText('mc.correctionNote')}<textarea data-expert-note lang="" dir="auto" maxlength="1000"></textarea></label></details>`;
  }).join('')}<label><input type="checkbox" required>${uiText('mc.manualConsent')}</label><button>${uiText('mc.applyCorrections')}</button> <button type="button" id="clear-expert-corrections" class="secondary">${uiText('mc.clearCorrections')}</button></form><div id="expert-correction-result" aria-live="polite"></div>`;
}
document.addEventListener('input',event=>{
  if(event.target.closest('#expert-comparison-form'))clearExpertPreview();
  if(event.target.closest('#expert-corrections-form')){const row=event.target.closest('[data-expert-row]');if(row)row.querySelector('[data-expert-note]').required=!!row.querySelector('[data-expert-action]').value;invalidateExpertCorrections();}
});
document.addEventListener('click',event=>{
  if(event.target.id==='clear-expert-comparison'){clearExpertPreview();const form=document.querySelector('#expert-comparison-form');form.reset();form.querySelector('textarea').focus();}
  if(event.target.id==='clear-expert-corrections'){const form=document.querySelector('#expert-corrections-form');form.reset();form.querySelectorAll('[data-expert-note]').forEach(note=>note.required=false);invalidateExpertCorrections();form.querySelector('h3').focus();}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['expert-comparison-form','expert-corrections-form'].includes(form.id))return;
  event.preventDefault();const manual=form.id==='expert-corrections-form';if(manual)invalidateExpertCorrections();else clearExpertPreview();message('');
  const selection=expertReviewSelection,revision=requestRevision,button=event.submitter,controller=new AbortController();expertReviewPending=controller;button.disabled=true;
  const valid=()=>selection===expertReviewSelection&&revision===requestRevision&&current==='local-tools';
  try{
    const body=expertReviewInput();if(manual){body.corrections=[];for(const row of form.querySelectorAll('[data-expert-row]')){const action=row.querySelector('[data-expert-action]').value;if(!action)continue;const note=row.querySelector('[data-expert-note]').value.trim();if(!note)throw new Error(uiText('mc.noteRequired'));const citations=[...row.querySelectorAll('[data-expert-source]:checked')].map(input=>({id:input.dataset.id,version:Number(input.dataset.version)}));if(!citations.length)throw new Error(uiText('ec.referencesRequired'));body.corrections.push({repeat:Number(row.dataset.repeat),case:Number(row.dataset.case),arm:row.dataset.arm,decision:{action,citations},note});}if(body.corrections.length>100)throw new Error(uiText('mc.tooMany'));}
    const result=await api('/agent-experiments/expert-preview',{...v1Json('POST',body),signal:controller.signal});if(!valid())return;
    const box=document.querySelector(manual?'#expert-correction-result':'#expert-comparison-result');
    if(manual){const human=result.human_assisted;box.innerHTML=`<h3>${uiText('mc.manualResult')}</h3><p>${uiText('mc.manualWarning')}</p><p>${uiText('mc.edited')}: ${human.correction_count}</p><ul>${Object.entries(human.arms).map(([arm,s])=>`<li>${uiText('mc.'+arm)}: ${s.correct} / ${s.cases} · ${s.corrected}</li>`).join('')}</ul>`;}
    else box.innerHTML=expertMetrics(result)+expertSteps(result);
    uiFormFields(box);focusLearningResult(box);
  }catch(error){if(valid())message(error.message,true);}finally{if(expertReviewPending===controller)expertReviewPending=null;if(button.isConnected&&selection===expertReviewSelection)button.disabled=false;}
});
function discardExpertComparison(){clearExpertPreview();document.querySelector('#expert-comparison-form')?.reset();}
window.addEventListener('pagehide',discardExpertComparison);
window.addEventListener('pageshow',event=>{if(event.persisted)discardExpertComparison();});
