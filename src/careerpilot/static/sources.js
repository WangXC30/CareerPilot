"use strict";
let sourceState={};
const sourceKinds=uiLabels("s.kind","project documentation community video article course exercise experience".split(" "));
const sourceTypes=uiLabels("s.type","official_entry curated_entry publisher_metadata user_declared_official user_declared_personal user_declared_advertising user_declared_unknown".split(" "));
const sourcePlatforms=uiLabels("s.platform","all other github xiaohongshu douyin".split(" "));
const sourceOptions=(items,original=false)=>items.map(([key,label])=>`<option lang="${original?"":uiLocale}" ${original?"data-original-content":""} value="${textValue(key)}">${textValue(label)}</option>`).join("");
const sourceJson=(method,body,version)=>({method,body:body===undefined?undefined:JSON.stringify(body),headers:version===undefined?{}:{"If-Match":`"${version}"`}});
function sourceTime(r){
  const d=r.published_on,age=d?(Date.now()-Date.parse(d))/86400000:null;
  return `${uiText("s.published")}: ${d?escapeHTML(uiDate(d)):uiText("s.unknown")}${age>180?uiText("s.old"):""}<br>${r.retrieved_at?`${uiText("s.retrieved")}: ${communityMoment(r.retrieved_at)}`:r.imported_at?`${uiText("s.imported")}: ${communityMoment(r.imported_at)} ${uiText("s.notPublished")}`:`${uiText("s.reviewed")}: ${r.reviewed_at?escapeHTML(uiDate(r.reviewed_at)):uiText("s.unknown")}`} ${r.expires_at?`<br>${uiText("s.retainedUntil")} ${communityMoment(r.expires_at)}`:""}`;
}

function sourceCards(rows){return rows.map(r=>`<article class="card source-card" data-source-id="${textValue(r.id)}"><span class="tag">${textValue(sourceKinds[r.kind]||r.kind)} · ${textValue(sourceTypes[r.source_type]||uiText("s.toCheck"))}</span><h3>${uiOriginal(r.title)}</h3>${r.url?`<a href="${textValue(r.url)}" target="${r.url.startsWith("/")?"_self":"_blank"}" rel="noopener noreferrer">${uiText("s.visit")}</a>`:`<p>${uiText("s.noUrl")}</p>`}<p>${sourceTime(r)}</p>${r.status==="unavailable"?`<p role="status">${uiText("s.unavailable")}：${r.alternative?uiOriginal(r.alternative):uiText("s.alternative")}</p>`:r.status==="unverified"?`<p>${uiText("s.unverified")}</p>`:""}<p>${communityFixed(r.verification)}</p>${r.author?`<p>${uiText("s.author")}：${uiOriginal(r.author)}</p>`:""}${r.text?`<details><summary>${uiText("s.excerpt")}</summary><pre>${uiOriginal(r.text)}</pre></details>`:""}${r.metadata?`<p>${uiText("s.stars")} ${r.metadata.stargazers_count} · ${uiText("s.forks")} ${r.metadata.forks_count}<br>${uiText("s.language")} ${r.metadata.language?uiOriginal(r.metadata.language):uiText("s.unknown")} · ${uiText("s.license")} ${uiOriginal(r.metadata.license_spdx)}<br>${uiText("s.updated")} ${r.metadata.updated_at?communityMoment(r.metadata.updated_at):uiText("s.unknown")}</p>`:""}${r.version?`<label class="job-choice"><input type="checkbox" name="source_compare" value="${r.id}"> ${uiText("s.chooseCompare")}</label><details><summary>${uiText("s.deleteTitle")}</summary><p>${uiText("s.deleteHelp")}</p><button class="secondary" data-delete-source="${r.id}" data-version="${r.version}">${uiText("s.delete")}</button></details>`:""}</article>`).join("")||`<div class='card'><p>${uiText("s.empty")}</p></div>`;}

async function renderSources(route,revision){
  if(route!=="sources")return false;
  if(!token){shellView(empty(uiText("signin.sources"),uiText("signin.sourcesText"),uiText("settings"),"settings"));return true;}
  if(sourceState.ownerToken!==token)sourceState={ownerToken:token};
  view.lang=uiLocale;sourceState.searchRequest=(sourceState.searchRequest||0)+1;sourceState.compareRequest=(sourceState.compareRequest||0)+1;sourceState.editRevision=0;
  view.innerHTML=`<div class="card" role="status">${uiText("s.loading")}</div>`;
  try{
    const [result,skills,jobs,profile]=await Promise.all([api("/source-discovery"),api("/catalogue/skill"),api("/jobs"),api("/profile")]);
    if(revision!==requestRevision)return true;
    sourceState.skills=skills;sourceState.profile=profile;
    const skillOptions=`<option value="">${uiText("s.anySkill")}</option>`+sourceOptions(skills.map(s=>[s.id,s.name]),true);
    view.innerHTML=`<section class="card"><h2>${uiText("s.title")}</h2><p>${communityFixed(result.notice)}</p><form id="sources-search" class="form-grid"><label>${uiText("s.keyword")}<input name="q" maxlength="200" placeholder="Python / Git"></label><label>${uiText("s.skill")}<select name="skill_id">${skillOptions}</select></label><label>${uiText("s.job")}<select name="job_id"><option value="">${uiText("s.anyJob")}</option>${sourceOptions(jobs.map(j=>[j.id,j.fields.title.value||uiText("jobToVerify")]),true)}</select></label><label>${uiText("s.kind")}<select name="kind"><option value="all">${uiText("s.anyKind")}</option>${sourceOptions(Object.entries(sourceKinds))}</select></label><label>${uiText("s.platform")}<select name="platform">${sourceOptions(Object.entries(sourcePlatforms))}</select></label><button>${uiText("s.search")}</button></form><p id="source-restrictions">${uiText("s.restrictions")}</p></section><details class="card growth-detail" id="source-import"><summary>${uiText("s.import")}</summary><p>${uiText("s.importHelp")}</p><form id="source-import-form" class="form-grid"><label>${uiText("s.name")}<input name="title" required maxlength="200"></label><label>${uiText("s.url")}<input name="url" type="url" maxlength="2000" placeholder="https://..."></label><label>${uiText("s.kind")}<select name="kind">${sourceOptions(Object.entries(sourceKinds))}</select></label><label>${uiText("s.skill")}<select name="skill_id">${skillOptions}</select></label><label>${uiText("s.declaredType")}<select name="declared_type">${sourceOptions([["unknown",uiText("s.unknown")],["official",uiText("s.official")],["personal",uiText("s.personal")],["advertising",uiText("s.advertising")]])}</select></label><label>${uiText("s.optionalAuthor")}<input name="author" maxlength="100"></label><label>${uiText("s.optionalDate")}<input name="published_on" type="date"></label><label>${uiText("s.authorization")}<select name="authorization_scope"><option value="reference_only">${uiText("s.referenceOnly")}</option><option value="local_excerpt">${uiText("s.localExcerpt")}</option></select></label><label class="full">${uiText("s.text")}<textarea name="text" maxlength="4000"></textarea></label><label>${uiText("s.topic")}<input name="claim_topic" maxlength="80" placeholder="${uiText("s.topicExample")}"></label><label>${uiText("s.value")}<input name="claim_value" maxlength="300" placeholder="${uiText("s.valueExample")}"></label><p class="full">${uiText("s.claimHelp")}</p><label class="full"><input type="checkbox" name="rights" required> ${uiText("s.rights")}</label><button>${uiText("s.confirmImport")}</button></form></details><details class="card growth-detail"><summary>${uiText("s.metadataTitle")}</summary><p>${uiText("s.metadataHelp")}</p><form id="source-metadata-form"><label>${uiText("s.repositoryUrl")}<input name="url" type="url" required placeholder="https://github.com/owner/repository"></label><label>${uiText("s.skill")}<select name="skill_id">${skillOptions}</select></label><label><input type="checkbox" required> ${uiText("s.metadataConsent")}</label><button>${uiText("s.readMetadata")}</button></form></details><div class="section-title"><h2>${uiText("s.library")}</h2><button id="source-compare-button" class="secondary">${uiText("s.compare")}</button></div><div id="source-comparison" aria-live="polite"></div><p id="source-search-status">${uiText("s.indexLoaded")}</p><div class="grid two source-space" id="source-results">${sourceCards(result.results)}</div>`;
    uiFormFields(view);
  }catch(e){if(revision===requestRevision){shellView(empty(uiText("err.sources"),escapeHTML(e.message),uiText("reload"),"sources"));}}
  return true;
}

document.addEventListener("submit",async event=>{
  const form=event.target;if(current!=="sources"||!form.matches("#sources-search,#source-import-form,#source-metadata-form"))return;
  event.preventDefault();const revision=requestRevision,editRevision=sourceState.editRevision,request=form.id==="sources-search"?++sourceState.searchRequest:null,data=Object.fromEntries(new FormData(form)),button=event.submitter;
  if(button)button.disabled=true;
  try{
    if(form.id==="sources-search"){
      const query=new URLSearchParams(Object.entries(data).filter(([,v])=>v));
      const result=await api(`/source-discovery?${query}`);
      if(revision!==requestRevision||request!==sourceState.searchRequest)return;
      sourceState.compareRequest++;
      document.querySelector("#source-comparison").innerHTML="";
      document.querySelector("#source-results").innerHTML=sourceCards(result.results);
      communityFocus(document.querySelector("#source-results"));
      document.querySelector("#source-search-status").textContent=uiFormat("s.results",{count:result.results.length,at:communityMoment(result.searched_at)});
    }
    if(form.id==="source-import-form"){
      const text=data.text.trim(),topic=data.claim_topic.trim(),value=data.claim_value.trim();
      if(Boolean(topic)!==Boolean(value))throw new Error(uiText("s.claimPair"));
      if(topic&&!text)throw new Error(uiText("s.claimText"));
      await api("/source-documents",sourceJson("POST",{title:data.title,url:data.url,text,kind:data.kind,skill_ids:data.skill_id?[data.skill_id]:[],declared_type:data.declared_type,author:data.author,published_on:data.published_on||null,authorization_scope:data.authorization_scope,claims:topic?[{topic,value,start:0,end:Array.from(text).length}]:[],rights_confirmed:true,confirmed:true}));
      await refreshSources(revision,editRevision,"s.importedNotice");
    }
    if(form.id==="source-metadata-form"){
      await api("/consents/public_web",sourceJson("PUT",{granted:true}));
      if(revision!==requestRevision)return;
      await api("/source-github-metadata",sourceJson("POST",{url:data.url,skill_ids:data.skill_id?[data.skill_id]:[],confirmed:true}));
      await refreshSources(revision,editRevision,"s.metadataSaved");
    }
  }catch(e){if(revision===requestRevision&&(!request||request===sourceState.searchRequest))message(e.message,true);}finally{if(button&&(!request||request===sourceState.searchRequest))button.disabled=false;}
});

document.addEventListener("click",async event=>{
  if(current!=="sources")return;const button=event.target.closest("button");if(!button)return;
  if(button.id!=="source-compare-button"&&!button.dataset.deleteSource)return;
  const revision=requestRevision,editRevision=sourceState.editRevision,request=button.id==="source-compare-button"?++sourceState.compareRequest:null;button.disabled=true;
  try{
    if(button.dataset.deleteSource){await api(`/source-documents/${button.dataset.deleteSource}`,sourceJson("DELETE",undefined,button.dataset.version));if(revision===requestRevision){sourceState.compareRequest++;document.querySelector("#source-comparison").replaceChildren();button.closest("[data-source-id]")?.remove();await refreshSources(revision,editRevision,"s.deleted");}}
    if(button.id==="source-compare-button"){
      const ids=Array.from(document.querySelectorAll('[name="source_compare"]:checked')).map(e=>e.value);
      if(ids.length<2||ids.length>4)throw new Error(uiText("s.chooseSources"));
      document.querySelector("#source-comparison").replaceChildren();
      const result=await api("/source-comparisons",sourceJson("POST",{source_ids:ids}));
      if(revision!==requestRevision||request!==sourceState.compareRequest)return;
      document.querySelector("#source-comparison").innerHTML=`<article class="card"><h3>${uiText("s.comparison")}</h3><p>${communityFixed(result.notice)}</p>${result.groups.map(g=>`<section class="evidence-row"><h3>${uiOriginal(g.topic)} · ${{conflicting:uiText("s.status.conflicting"),agreement_unverified:uiText("s.status.agreement_unverified"),single_source:uiText("s.status.single_source")}[g.status]}</h3><p>${uiFormat("s.hosts",{count:g.distinct_hosts})}</p>${g.claims.map(c=>`<p>${uiOriginal(result.sources.find(r=>r.id===c.source_id)?.title)}<br>${uiText("s.annotated")}：${uiOriginal(c.value)}${c.url?`<br><a href="${textValue(c.url)}" target="_blank" rel="noopener noreferrer">${uiText("s.compareUrl")}</a>`:""}<br>${uiText("s.original")}：${uiOriginal(c.quote)}<br>${uiText("s.published")}：${c.published_on?escapeHTML(uiDate(c.published_on)):uiText("s.unknown")} · ${textValue(sourceTypes[c.source_type])}</p>`).join("")}</section>`).join("")||`<p>${uiText("s.noClaims")}</p>`}</article>`;
      communityFocus(document.querySelector("#source-comparison"));
    }
  }catch(e){if(revision===requestRevision&&(!request||request===sourceState.compareRequest))message(e.message,true);}finally{if(!request||request===sourceState.compareRequest)button.disabled=false;}
});

async function refreshSources(revision,editRevision,key){
  if(revision!==requestRevision)return;
  if(editRevision!==sourceState.editRevision){message(uiText(key)+" "+uiText("s.inputRetained"));return;}
  await refreshCommunity(revision,key);
}
function invalidateSourceInputs(event){
  if(current!=="sources")return;
  if(event.target.closest('#sources-search,#source-import-form,#source-metadata-form'))sourceState.editRevision++;
  if(event.target.closest('#sources-search')){sourceState.searchRequest++;document.querySelector('#sources-search button').disabled=false;}
  if(event.target.closest('#sources-search')||event.target.matches('[name="source_compare"]')){
    sourceState.compareRequest++;document.querySelector('#source-comparison').replaceChildren();document.querySelector('#source-compare-button').disabled=false;
  }
}
document.addEventListener('input',invalidateSourceInputs);
document.addEventListener('change',invalidateSourceInputs);
