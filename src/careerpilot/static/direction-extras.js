async function mountDirectionExtras(revision){
  const [channels,profiles]=await Promise.all([api('/source-channels'),api('/brand-profiles')]);
  if(revision!==requestRevision)return;
  const card=document.createElement('article');card.className='card growth-detail';card.id='direction-extras';
  card.innerHTML=`<h2>${uiText("r.126")}</h2><p>${remainingFixed(channels.notice)}</p>${channels.channels.map(c=>`<details><summary>${remainingFixed(c.title)} · ${c.status==='available'?uiText("r.127"):uiText("r.128")}</summary>${c.notice?`<p>${remainingFixed(c.notice)}</p>`:''}${c.items.map(i=>`<p>${uiOriginal(i.title||uiText("r.129"))} · v${i.version}</p>`).join('')||`<p>${uiText("r.130")}</p>`}</details>`).join('')}
    <h2>${uiText("r.131")}</h2><p>${uiText("r.132")}</p>${v4.translations.filter(t=>t.sources_current).map(t=>`<form class="brand-create" data-translation="${t.id}"><label>${uiText("r.133")}<input name="headline" required maxlength="120"></label><fieldset><legend>${uiText("r.134")}${uiOriginal(t.locale)}</legend>${t.source_facts.filter(f=>!['name','contact'].includes(f.kind)).map(f=>`<label><input type="checkbox" name="fact_ids" value="${f.id}">${uiOriginal(f.text)} → ${uiOriginal(t.mappings.find(m=>m.fact_id===f.id)?.text||'')}</label>`).join('')}</fieldset><label><input type="checkbox" required>${uiText("r.135")}</label><button>${uiText("r.136")}</button></form>`).join('')||`<p>${uiText("r.137")}</p>`}${profiles.map(p=>`<details><summary>${uiOriginal(p.headline)}</summary>${p.items.map(i=>`<p>${uiOriginal(i.original)} → ${uiOriginal(i.translated)}</p>`).join('')}<p>${remainingFixed(p.notice)}</p><a href="#collaboration">${uiText("r.138")}</a></details>`).join('')}`;
  view.append(card);uiFormFields(card);
}
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('.brand-create'))return;
  event.preventDefault();const revision=requestRevision,values=new FormData(form),button=event.submitter;button.disabled=true;
  try{await api('/brand-profiles',v1Json('POST',{translation_id:form.dataset.translation,fact_ids:values.getAll('fact_ids'),headline:values.get('headline'),privacy_reviewed:true,confirmed:true}));if(revision===requestRevision){if(!await remainingRefresh(revision,'directions'))return;message(uiText("r.139"));}}
  catch(error){if(revision===requestRevision)message(error.message,true);}finally{if(button.isConnected)button.disabled=false;}
});
