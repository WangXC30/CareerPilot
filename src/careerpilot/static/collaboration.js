/* Collaboration never automatically publishes: preview the selected fields first. */
let collaborationPreview = null;
let collaborationProfile=null,collaborationInputRevision=0,collaborationWorkspaceRequest=0;
let collaborationSources = [], collaborationWorkspaceId = null;
const collaborationKindNames=uiLabels("c.kind","brand_profile practice_project plan project_asset project_narrative".split(" "));
const collaborationFieldNames=uiLabels("c.field","headline items locale template milestones status tasks start_date deadline title kind content url situation task action result limitations".split(" "));
async function renderCollaboration(route, revision) {
  if (route !== 'collaboration') return false;
  if (!token) {shellView(empty(uiText('signin.collab'), uiText('signin.collabText'), uiText('signInButton'), 'settings')); return true;}
  collaborationInputRevision++;collaborationWorkspaceRequest++;collaborationPreview=null;collaborationWorkspaceId=null;
  view.lang=uiLocale;view.innerHTML=`<p class="card" role="status">${uiText("c.loading")}</p>`;
  try {
    const [spaces, shares, sources, profile] = await Promise.all([api('/workspaces'), api('/object-shares'), api('/object-share-sources'),api('/profile')]);
    if (revision !== requestRevision) return true;
    collaborationPreview = null;
    collaborationWorkspaceId = null;
    collaborationSources = sources;collaborationProfile=profile;
    view.innerHTML = `<article class="card"><h2>${uiText("c.spaces")}</h2><p>${uiText("c.identity")}：<code>${textValue(spaces.user_id)}</code></p>
      <p>${uiText("c.help")}</p>
      <form id="space-create"><label>${uiText("c.spaceName")}<input name="name" required maxlength="100"></label><button>${uiText("c.create")}</button></form>
      ${spaces.spaces.map(space => `<details><summary>${uiOriginal(space.name)}</summary>
      <button data-space-view="${space.id}">${uiText("c.view")}</button>
      ${space.owner ? `<form class="space-invite" data-space="${space.id}"><label>${uiText("c.recipient")}<input name="recipient_id" required minlength="36" maxlength="36"></label><button>${uiText("c.invite")}</button></form>
      ${space.members.map(id => `<p>${textValue(id)} <button data-space-remove="${space.id}" data-member="${textValue(id)}">${uiText("c.removeMember")}</button></p>`).join('')}
      <button data-space-close="${space.id}">${uiText("c.close")}</button>` : `<button data-space-remove="${space.id}" data-member="${spaces.user_id}">${uiText("c.leave")}</button>`}</details>`).join('')}
      <div id="space-ticket" role="status"></div></article>
      <article class="card growth-detail"><h2>${uiText("c.acceptTitle")}</h2><form id="space-accept"><label>${uiText("c.invitationId")}<input name="invitation_id" required></label><label>${uiText("c.ticket")}<input name="ticket" type="password" required></label><button>${uiText("c.accept")}</button></form></article>
      <article class="card growth-detail"><h2>${uiText("c.chooseShare")}</h2><form id="share-preview-form">
      <label>${uiText("c.source")}<select name="source" required><option value="">${uiText("c.choose")}</option>${sources.map(source=>`<option lang="" data-original-content value="${source.kind}:${source.id}">${textValue(collaborationKindNames[source.kind])} · ${textValue(source.title)} · v${source.version}</option>`).join('')}</select></label>
      <fieldset><legend>${uiText("c.fields")}</legend><div id="share-field-options">${uiText("c.fieldsHelp")}</div></fieldset>
      <button>${uiText("c.preview")}</button></form><div id="share-preview"></div></article>
      <article class="card growth-detail"><h2>${uiText("c.activeShares")}</h2>${shares.map(s => `<p>${textValue(collaborationKindNames[s.kind])} · ${uiText("c.expires")} ${communityMoment(new Date(s.expires_at*1000))} <button data-share-revoke="${s.id}">${uiText("c.revoke")}</button></p>`).join('') || `<p>${uiText("c.noShares")}</p>`}</article>
      <article class="card growth-detail"><h2>${uiText("c.spaceContent")}</h2><div id="space-shares"></div></article>`;
    uiFormFields(view);
    document.querySelector('#share-preview-form').dataset.spaces = JSON.stringify(spaces.spaces);
  } catch (error) {if (revision === requestRevision) message(error.message, true);}
  return true;
}

document.addEventListener('change',event=>{
  const form=event.target.closest('#share-preview-form');if(!form)return;
  collaborationInputRevision++;collaborationPreview=null;document.querySelector('#share-preview').replaceChildren();
  if(event.target.name==='source'){
    const source=collaborationSources.find(item=>item.kind+':'+item.id===event.target.value);
    document.querySelector('#share-field-options').innerHTML=(source?.fields||[]).map(key=>`<label><input type="checkbox" name="fields" value="${key}">${textValue(collaborationFieldNames[key]||key)}</label>`).join('');
  }
});

async function loadWorkspaceShares(key,revision){
  if(revision!==requestRevision||current!=='collaboration')return false;
  const selection=++collaborationWorkspaceRequest;collaborationWorkspaceId=key;
  const container=document.querySelector('#space-shares');
  if(revision===requestRevision)container.textContent=uiText("c.loadingSpace");
  let shares,comments;
  try{
    shares=await api('/object-shares?workspace_id='+encodeURIComponent(key));
    if(revision!==requestRevision||selection!==collaborationWorkspaceRequest)return false;
    comments=await Promise.all(shares.map(share=>api('/object-shares/'+share.id+'/comments')));
  }catch(error){if(revision===requestRevision&&selection===collaborationWorkspaceRequest){container.replaceChildren();throw error;}return false;}
  if(revision!==requestRevision||selection!==collaborationWorkspaceRequest)return false;
  collaborationWorkspaceId=key;
  container.innerHTML=`<h3>${uiText("c.spaceContent")}</h3>`+(shares.map((share,index)=>`<details><summary>${textValue(collaborationKindNames[share.kind])} · v${share.source_version}</summary><pre>${uiOriginal(JSON.stringify(share.content,null,2))}</pre>${comments[index].map(comment=>`<article class="share-feedback"><p>${uiOriginal(comment.text)}</p>${comment.can_hide?`<details><summary>${uiText("c.hide")}</summary><form class="share-moderation" data-comment="${comment.id}"><label>${uiText("c.hideReason")}<input name="reason" required maxlength="200"></label><button>${uiText("c.confirmHide")}</button></form></details>`:''}</article>`).join('')}<form class="share-comment" data-share="${share.id}"><label>${uiText("c.feedback")}<textarea name="text" required maxlength="2000"></textarea></label><button>${uiText("c.submitFeedback")}</button></form></details>`).join('')||`<p>${uiText("c.empty")}</p>`);
  communityFocus(container);return true;
}

document.addEventListener('submit', async event => {
  const form = event.target;
  if(current!=='collaboration')return;
  if (!['space-create','space-accept','share-preview-form','share-confirm-form'].includes(form.id) && !form.matches('.space-invite,.share-comment,.share-moderation')) return;
  event.preventDefault();
  const revision = requestRevision,workspace=collaborationWorkspaceId,workspaceRequest=collaborationWorkspaceRequest, values = Object.fromEntries(new FormData(form));
  const button = form.querySelector('button');let previewRequest=null; button.disabled = true;
  try {
    if (form.id === 'space-create') {await api('/workspaces', v1Json('POST', {...values,confirmed:true})); await refreshCommunity(revision);}
    else if (form.matches('.space-invite')) {
      const value = await api('/workspaces/'+form.dataset.space+'/invitations', v1Json('POST', {...values,confirmed:true}));
      if (revision !== requestRevision) return;
      document.querySelector('#space-ticket').textContent = uiFormat("c.invitation",{id:value.id,ticket:value.ticket});
    } else if (form.id === 'space-accept') {await api('/workspace-invitations/'+encodeURIComponent(values.invitation_id)+'/accept',v1Json('POST',{ticket:values.ticket,confirmed:true})); await refreshCommunity(revision);}
    else if (form.id === 'share-preview-form') {
      const inputRevision=++collaborationInputRevision;previewRequest=inputRevision;
      const [kind, source_id] = values.source.split(':');
      const fields = new FormData(form).getAll('fields');
      collaborationPreview=null;document.querySelector('#share-preview').replaceChildren();
      const preview = await api('/object-shares/preview',v1Json('POST',{kind,source_id,fields}));
      if (revision !== requestRevision||inputRevision!==collaborationInputRevision) return;
      collaborationPreview = preview;
      const spaces = JSON.parse(form.dataset.spaces);
      document.querySelector('#share-preview').innerHTML = `<h3>${uiText("c.previewTitle")}</h3><pre>${uiOriginal(JSON.stringify(preview.content,null,2))}</pre>
      <form id="share-confirm-form"><label>${uiText("c.workspace")}<select name="workspace_id"><option value="">${uiText("c.noWorkspace")}</option>${spaces.map(s=>`<option lang="" data-original-content value="${s.id}">${textValue(s.name)}</option>`).join('')}</select></label>
      ${kind !== 'plan' ? `<label><input type="checkbox" name="public">${uiText("c.publicConsent")}</label>` : ''}
      <label>${uiText("c.lifetime")}<input type="number" name="lifetime_hours" min="1" max="720" value="168" required></label><button>${uiText("c.confirmShare")}</button></form>`;
      communityFocus(document.querySelector('#share-preview'));
    } else if (form.id === 'share-confirm-form') {
      if (!collaborationPreview)throw new Error(uiText("c.previewChanged"));
      const {kind,source_id,source_version,fields} = collaborationPreview;
      const result = await api('/object-shares',v1Json('POST',{kind,source_id,source_version,fields,workspace_id:values.workspace_id||null,public:values.public==='on',lifetime_hours:Number(values.lifetime_hours),confirmed:true}));
      if (revision !== requestRevision) return;
      await render();
      if(requestRevision!==revision+1||current!=='collaboration')return;
      if (result.ticket) {
        const link = location.origin+'/app/share.html#'+encodeURIComponent(result.id)+':'+encodeURIComponent(result.ticket);
        document.querySelector('#space-ticket').textContent = uiFormat("c.publicLink",{link});
      } else message(uiText("c.shared"));
    } else if(form.matches('.share-moderation')){
      await api('/share-comments/'+form.dataset.comment+'/hide',v1Json('POST',{reason:values.reason,confirmed:true}));
      if(revision===requestRevision&&workspaceRequest===collaborationWorkspaceRequest&&await loadWorkspaceShares(workspace,revision))message(uiText("c.hidden"));
    } else {
      await api('/object-shares/'+form.dataset.share+'/comments',v1Json('POST',{text:values.text,confirmed:true}));
      if(revision===requestRevision&&workspaceRequest===collaborationWorkspaceRequest&&await loadWorkspaceShares(workspace,revision))message(uiText("c.feedbackSaved"));
    }
  } catch (error) {if (revision === requestRevision&&(!previewRequest||previewRequest===collaborationInputRevision)&&(!form.matches('.share-comment,.share-moderation')||workspaceRequest===collaborationWorkspaceRequest)) message(error.message,true);}
  finally {if (button.isConnected) button.disabled=false;}
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-space-view],[data-space-remove],[data-space-close],[data-share-revoke]');
  if(current!=='collaboration'||!button)return;
  const revision=requestRevision;button.disabled=true;
  try {
    if(button.dataset.spaceView){
      await loadWorkspaceShares(button.dataset.spaceView,revision);
    } else {
      const path=button.dataset.spaceRemove?'/workspaces/'+button.dataset.spaceRemove+'/members/'+button.dataset.member:button.dataset.spaceClose?'/workspaces/'+button.dataset.spaceClose:'/object-shares/'+button.dataset.shareRevoke;
      await api(path,{method:'DELETE'});await refreshCommunity(revision,"c.revoked");
    }
  }catch(error){if(revision===requestRevision)message(error.message,true);}
  finally{if(button.isConnected)button.disabled=false;}
});
