const callbackParams = new URLSearchParams(location.search);
history.replaceState(null, '', location.pathname);
document.documentElement.lang=uiLocale;
document.title=uiText('ph.callbackTitle');
if (window.opener) {
  window.opener.postMessage({type:'careerpilot-github', state:callbackParams.get('state'), code:callbackParams.get('code'), error:callbackParams.get('error')}, location.origin);
  document.querySelector('#result').textContent = uiText('ph.callbackDone');
} else {
  document.querySelector('#result').textContent = uiText('ph.callbackMissing');
}
