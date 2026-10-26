// Keep the current ticket in page memory only; every language change rechecks access.
let publicShareRevision=0,publicShareCredentials=null;
async function loadPublicShare(readLink=false){
  const revision=++publicShareRevision;
  document.documentElement.lang=uiLocale;document.title=uiText('c.publicTitle');
  document.querySelector('#share-title').textContent=uiText('c.publicHeading');
  document.querySelector('#share-locale').value=uiLocale;
  document.querySelector('#shared-content').textContent='';
  const status=document.querySelector('#share-status');status.lang=uiLocale;status.textContent=uiText('c.publicLoading');
  try{
    if(readLink){
      publicShareCredentials=null;
      const fragment=location.hash.slice(1);history.replaceState(null,'',location.pathname);
      let id,ticket;try{[id,ticket]=fragment.split(':').map(decodeURIComponent);}catch{throw new Error(uiText('c.incompleteLink'));}
      if(!id||!ticket)throw new Error(uiText('c.incompleteLink'));
      publicShareCredentials={id,ticket};
    }
    if(!publicShareCredentials)throw new Error(uiText('c.incompleteLink'));
    const {id,ticket}=publicShareCredentials;
    const response=await fetch('/api/v1/public-shares/'+encodeURIComponent(id),{headers:{'X-Share-Ticket':ticket},cache:'no-store'});
    if(!response.ok)throw new Error(uiText('c.invalidLink'));
    const value=await response.json();
    if(revision!==publicShareRevision)return;
    const translated=communityExplanations.get(value.notice);
    status.lang=uiLocale==='en-US'&&translated?'en-US':'zh-CN';
    status.textContent=uiLocale==='en-US'&&translated?translated:value.notice;
    document.querySelector('#shared-content').textContent=JSON.stringify(value.content,null,2);
    document.querySelector('#share-title').focus();
  }catch(error){if(revision===publicShareRevision){status.lang=uiLocale;status.textContent=error.message;}}
}
window.addEventListener('hashchange',()=>loadPublicShare(true));
document.querySelector('#share-locale').addEventListener('change',event=>{
  uiLocale=event.target.value==='en-US'?'en-US':'zh-CN';
  try{localStorage.setItem('careerpilot-ui-locale',uiLocale);}catch{}
  loadPublicShare();
});
loadPublicShare(true);
