"use strict";
let userMode = 'login', userNext = null, userSending = false;
function openUser(mode='login', next=null) {
  userMode=mode; userNext=next; $('user-message').hidden=true;
  $('user-password').value=''; $('user-confirm').value='';
  $('user-heading').textContent={login:'Sign in to ROC',register:'Create your ROC account',forgot:'Reset your password',reset:'Choose a new password'}[mode];
  $('user-submit').textContent={login:'Sign in',register:'Create account',forgot:'Email recovery link',reset:'Save password'}[mode];
  $('user-email-label').hidden=mode==='reset'; $('user-email').required=mode!=='reset';
  $('user-password-label').hidden=mode==='forgot'; $('user-password').required=mode!=='forgot';
  $('user-password').autocomplete=mode==='login'?'current-password':'new-password';
  $('user-confirm-label').hidden=!['register','reset'].includes(mode); $('user-confirm').required=['register','reset'].includes(mode);
  $('user-create').hidden=mode!=='login'; $('user-forgot').hidden=mode!=='login'; $('user-back').hidden=mode==='login';
  $('user-help').textContent=mode==='register'?'Use your email and a password of 5–128 characters. No special-character rules. PACER connects separately.':mode==='forgot'?'A single-use link goes to your ROC login email. Keep ROC running and open the link on this computer.':'Your searches and files belong to you, even when you share PACER access.';
  if (!$('user-dialog').open) $('user-dialog').showModal();
}
bind('user-login','click',()=>openUser());
bind('user-close','click',()=>{$('user-dialog').close();userNext=null;});
bind('user-create','click',()=>openUser('register',userNext));
bind('user-forgot','click',()=>openUser('forgot'));
bind('user-back','click',()=>openUser());
bind('user-form','submit',async e=>{
  e.preventDefault(); if(userSending)return; userSending=true;
  $('user-submit').disabled=true; $('user-submit').textContent='Working…';
  const mode=userMode, next=userNext;
  try {
    const values=mode==='forgot'?{email:$('user-email').value}:mode==='reset'?{token:resetToken,password:$('user-password').value,confirmation:$('user-confirm').value}:{email:$('user-email').value,password:$('user-password').value,...(mode==='register'?{confirmation:$('user-confirm').value}:{})};
    const result=await api('/api/account/'+mode,values);
    $('user-password').value='';$('user-confirm').value='';
    if(['login','register'].includes(mode)) {userNext=null;$('user-dialog').close();await refresh();if(next)await next();}
    else {$('user-message').textContent=result.message;$('user-message').hidden=false;}
  } catch(e) {$('user-message').textContent=e.message;$('user-message').hidden=false;}
  finally {userSending=false;$('user-submit').disabled=false;$('user-submit').textContent={login:'Sign in',register:'Create account',forgot:'Email recovery link',reset:'Save password'}[userMode];}
});
bind('import-legacy','click',async()=>{
  if(!confirm('Copy the older searches associated with the connected PACER username into your personal ROC account? These may include work from people sharing that PACER login. The originals will be preserved.'))return;
  await action(async()=>{const result=await api('/api/account/import',{});$('notice').textContent=`Imported ${result.imported} older searches. Originals were preserved.`;$('notice').hidden=false;});
});
$('user-dialog').addEventListener('close',()=>{$('user-password').value='';$('user-confirm').value='';});
if(resetToken)openUser('reset');
