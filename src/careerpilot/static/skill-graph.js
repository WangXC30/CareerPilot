"use strict";
const graphCategories=["common","shared","unique","prerequisite"];
let graphSelection=0;
function mountSkillGraph(jobs){
  graphSelection++;
  const active=jobs.filter(j=>!["closed","merged"].includes(j.status));
  document.querySelector("#gap-form").closest("article").insertAdjacentHTML("afterend",`<article class="card growth-detail" id="skill-graph-card" lang="${uiLocale}"><h2>${uiText("graphTitle")}</h2><p>${uiText("graphHelp")}</p><form id="skill-graph-form"><fieldset><legend>${uiText("graphJobs")}</legend>${active.map(j=>`<label class="job-choice"><input type="checkbox" name="graph_job" value="${textValue(j.id)}"> ${j.fields.title.value?uiOriginal(j.fields.title.value):uiText("jobToVerify")} · ${textValue(j.id.slice(0,8))}</label>`).join("")||`<p>${uiText("graphNeedsJobs")}</p>`}</fieldset><button type="submit" ${active.length<3?"disabled":""}>${uiText("graphGenerate")}</button></form><div id="skill-graph-result" aria-live="polite"></div></article>`);
}

function skillGraphHTML(graph){
  const labels=Object.fromEntries(graph.nodes.map(n=>[n.skill_id,n.name]));
  const jobs=Object.fromEntries(graph.jobs.map(j=>[j.id,j.title]));
  const byLevel=new Map(),positions={};
  graph.nodes.forEach(n=>{if(!byLevel.has(n.level))byLevel.set(n.level,[]);byLevel.get(n.level).push(n);});
  const width=Math.max(420,...[...byLevel.values()].map(nodes=>nodes.length*165+30)),height=Math.max(120,(byLevel.size)*100+30);
  byLevel.forEach((nodes,level)=>nodes.forEach((n,i)=>{positions[n.skill_id]={x:30+i*165,y:20+level*100};}));
  const svg=`<div class="skill-graph-scroll" tabindex="0" aria-label="${uiText("graphScroll")}"><svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${uiText("graphImage")}"><defs><marker id="skill-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#687d73"/></marker></defs>${graph.edges.map(e=>{const a=positions[e.from],b=positions[e.to];return `<path d="M${a.x+65},${a.y+45} L${b.x+65},${b.y}" stroke="#687d73" fill="none" marker-end="url(#skill-arrow)"/>`;}).join("")}${graph.nodes.map(n=>{const p=positions[n.skill_id];return `<g><rect x="${p.x}" y="${p.y}" width="140" height="45" rx="9" fill="${n.category==="common"?"#dcecdf":n.category==="prerequisite"?"#f0f1ee":"#e8edf4"}" stroke="#687d73"/><text x="${p.x+70}" y="${p.y+27}" text-anchor="middle" fill="#243b32" lang="" data-original-content>${textValue(n.name)}</text></g>`;}).join("")}</svg></div>`;
  return `<h3 tabindex="-1">${uiText("graphTitle")}</h3><p>${uiText("graphNotice")}</p><p>${uiText("graphVersions")}：${graph.jobs.map(j=>`${uiOriginal(j.title)} v${j.version}`).join("; ")}。${uiText("graphRegenerate")}</p>${graph.jobs.filter(j=>!j.recognized_skill_ids.length).map(j=>`<p role="status">${uiOriginal(j.title)}：${uiText("graphUnrecognized")}</p>`).join("")}<div class="graph-summary">${graphCategories.map(key=>`<span class="tag">${uiText('graph.'+key)}：${graph.nodes.filter(n=>n.category===key).length}</span>`).join(" ")}</div>${graph.nodes.length?svg:`<p>${uiText("graphNoSkills")}</p>`}<p>${uiText("graphEdgesHelp")}</p><div class="skill-graph-nodes">${graph.nodes.map(n=>`<details data-graph-skill="${textValue(n.skill_id)}"><summary>${uiOriginal(n.name)} · ${uiText('graph.'+n.category)} · ${uiFormat("graphJobCount",{count:n.job_count,total:graph.jobs.length})}</summary><p>${uiText("basicStatus")}：${skillLabel(n.status)}</p><p>${uiText("directPrerequisites")}：${n.requires.map(k=>uiOriginal(labels[k])).join(", ")||uiText("none")}；${uiText("prerequisiteOrder")}：${n.prerequisite_path.map(k=>uiOriginal(labels[k])).join(" → ")||uiText("none")}</p><p>${uiText("prerequisiteBasis")} · v${n.catalogue_version}</p>${n.sources.map(s=>`<blockquote><strong>${uiOriginal(jobs[s.job_id])} · v${s.job_version} · ${textValue(uiText('requirement.'+s.requirement))}</strong><p>${uiOriginal(typeof s.evidence==="string"?s.evidence:s.evidence.quote)}</p></blockquote>`).join("")||`<p>${uiText("graphIndirect")}</p>`}<h3>${uiText("learningMaterials")}</h3>${n.resources.map(r=>`<p>${r.status==="available"?`<a href="${textValue(r.url)}" target="${r.url.startsWith("/")?"_self":"_blank"}" rel="noopener noreferrer">${uiOriginal(r.title)}</a>`:uiOriginal(r.title)} · ${uiText(r.status==="available"?"registeredNotProbed":"replaceUnavailable")} · ${textValue(r.language)}${r.stale?" · "+uiText("needsReview"):""}</p>`).join("")||`<p>${uiText("noLearningMaterials")}</p>`}<p>${uiText("graphVerification")}</p>${n.verification.kind==="objective_quiz"?`<button class="secondary" data-quiz="${textValue(n.skill_id)}">${textValue(uiFormat("quizStart",{name:n.name}))}</button>`:`<p>${uiText("noQuiz")}</p>`}</details>`).join("")}</div>`;
}

document.addEventListener("change",event=>{
  if(!event.target.matches("#skill-graph-form input"))return;
  graphSelection++;
  document.querySelector("#skill-graph-result").textContent=uiText("graphSelectionChanged");
});
document.addEventListener("submit",async event=>{
  if(event.target.id!=="skill-graph-form")return;
  event.preventDefault();
  const revision=requestRevision,selection=++graphSelection,button=event.submitter;
  const ids=[...event.target.querySelectorAll("input:checked")].map(el=>el.value);
  if(ids.length<3||ids.length>20){message(uiText("graphSelectionRequired"),true);return;}
  button.disabled=true;
  try{
    const graph=await api("/skill-graph",v1Json("POST",{job_ids:ids}));
    if(revision!==requestRevision||selection!==graphSelection)return;
    const box=document.querySelector("#skill-graph-result");box.innerHTML=skillGraphHTML(graph);focusLearningResult(box);
  }catch(error){if(revision===requestRevision&&selection===graphSelection)message(error.message,true);}
  finally{if(button.isConnected)button.disabled=false;}
});
