'use strict';
const $ = id => document.getElementById(id);
let offset = 0, busy = false;
function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
function node(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
function bytes(n) { let i = 0; while(n >= 1024 && i < 4) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${['B','KiB','MiB','GiB','TiB'][i]}`; }
async function api(path, options={}) { const r = await fetch(path, {credentials:'same-origin', ...options}); const data = await r.json(); if(!r.ok) throw new Error(data.error || 'Service unavailable.'); return data; }
async function action(data) { await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); await refresh(); }
function button(label, fn, style) { const b = node('button',label,style || (/^(Cancel|Clean|Block)/.test(label)?'danger':/^(Retry|Authorize)/.test(label)?'success':'')); b.onclick = async () => { b.disabled = true; try { await fn(); } catch(e) { notice(e.message); } finally { b.disabled = false; } }; return b; }
function link(url, label) { const a = node('a',label,'button'); if(url.startsWith('https://huggingface.co/')) a.href = url; a.target='_blank'; a.rel='noopener noreferrer'; return a; }
function renderActive(tasks) {
  $('active').replaceChildren();
  if(!tasks.length) $('active').append(node('p','All caught up. No transfers are running.','empty'));
  for(const t of tasks) {
    const card=node('article',undefined,'task');
    card.append(node('h3',t.name),node('span',t.operation || t.phase,'badge'));
    for(const group of t.groups || [t]) {
      const bar=node('progress'); bar.max=group.total || 1; if(group.total)bar.value=group.done;
      card.append(node('p',`${group.phase}${group.count>1?' ×'+group.count:''}`));
      if(['Download','Upload','Extract'].includes(group.phase)) card.append(bar,node('p',`${bytes(group.done)}${group.total?' / '+bytes(group.total):''} · ${bytes(group.speed)}/s`));
    }
    if(t.wait_reason)card.append(node('p',t.wait_reason));
    card.append(node('small',`${t.uploaded_files || 0} files uploaded`),button('Cancel',()=>action({action:'cancel',id:t.id})));
    $('active').append(card);
  }
}
function renderHistory(records) {
  $('history').replaceChildren();
  if(records.length) $('history').append(button('Clear finished history',()=>action({action:'clear_history'}),'danger'));
  if(!records.length) $('history').append(node('p','Your completed and interrupted transfers will appear here.','muted'));
  for(const t of records) { const row=node('div',undefined,'history-row'), main=node('div',undefined,'row-main'), controls=node('div',undefined,'row-actions'); main.append(node('strong',t.name),node('small',`${t.phase} · ${new Date(t.created*1000).toLocaleString()}`)); if(t.error) main.append(node('small',t.error)); for(const url of t.links) controls.append(link(url,'Open file')); if(['Failed','Cancelled','Interrupted'].includes(t.phase)) { controls.append(button('Retry',()=>action({action:'retry',id:t.id})),button('Clean files',()=>action({action:'clean',id:t.id}))); } row.append(main,controls); $('history').append(row); }
}
function renderAdmin(data) {
  $('admin').hidden = !data.user.owner; if(!data.user.owner) return;
  if($('admin').contains(document.activeElement) && document.activeElement.tagName==='SELECT') return;
  $('settings').replaceChildren();
  const fields = [['public_mode','Allow everyone',[false,true]],['public_status','Public statistics',[false,true]],['download_slots','Concurrent downloads',[1,2,4,6,8]],['upload_slots','Concurrent uploads',[1,2,4,6,8]],['pipeline_files','Files prepared per task',[1,2,3,4]],['disk_reserve_mb','Keep disk free (MiB)',[512,1024,2048,4096]],['failed_file_minutes','Keep failed files (minutes)',[0,15,60]],['history_days','Keep history (days)',[1,7,30]]];
  for(const [key,title,choices] of fields) { const label=node('label',title), select=node('select'); for(const value of choices) { const option=node('option',typeof value === 'boolean' ? (value?'On':'Off') : value); option.value=JSON.stringify(value); option.selected=data.settings[key]===value; select.append(option); } select.onchange=async()=>{ try{await action({action:'setting',key,value:JSON.parse(select.value)});}catch(e){notice(e.message);} }; label.append(select); $('settings').append(label); }
  $('users').replaceChildren();
  for(const u of data.users) { const row=node('div',undefined,'user-row'), main=node('div',undefined,'row-main'), controls=node('div',undefined,'row-actions'); main.append(node('strong',u.name||String(u.user_id)),node('small',`${u.user_id} · ${u.blocked?'Blocked':u.authorized?'Authorized':'Public policy'}`)); if(u.user_id!==data.user.id) for(const [cmd,title] of [['allow','Authorize'],['block','Block'],['reset','Reset']]) controls.append(button(title,()=>action({action:cmd,user:u.user_id}))); row.append(main,controls); $('users').append(row); }
  $('previous-users').disabled=offset===0; $('next-users').disabled=data.users.length<8;
}
async function refresh() {
  if(busy || document.hidden) return; busy=true;
  try { const publicData=await api('/api/public'); $('health').textContent=publicData.ready?'Online':publicData.status==='waiting_for_previous_instance'?'Waiting for previous instance':'Starting'; if(publicData.bot) $('telegram').href='https://t.me/'+publicData.bot;
    if(publicData.active!==undefined) $('public-stats').textContent=`${publicData.active} active · ${publicData.downloads} downloading · ${publicData.uploads} uploading`;
    if(!publicData.ready){$('portal').hidden=true;$('guest').hidden=false;$('logout').hidden=true;return;}
    const response=await fetch('/api/me?offset='+offset,{credentials:'same-origin'}); if(response.status===401){$('guest').hidden=false;$('portal').hidden=true;$('logout').hidden=true;return;} if(!response.ok)throw new Error('Could not load your transfers.'); const data=await response.json();
    $('portal').hidden=false;$('guest').hidden=true;$('logout').hidden=false;$('welcome').textContent=`${data.user.owner?'OWNER':'YOUR PORTAL'} · ${data.user.name}`;
    const dest=data.destination; $('destination').textContent=dest.namespace&&dest.repo_name ? `${dest.namespace}/${dest.repo_name} · ${dest.repo_type} · ${dest.branch} · /${dest.path}` : 'Choose a destination in /usettings before your first upload.';
    $('summary').replaceChildren(); for(const [value,label] of [[data.active.length,'Active'],[data.active.filter(t=>(t.groups||[t]).some(g=>g.phase==='Download')).length,'Downloading'],[data.active.filter(t=>(t.groups||[t]).some(g=>['Upload','Commit'].includes(g.phase))).length,'Uploading']]) { const box=node('div',undefined,'metric');box.append(node('strong',value),node('span',label));$('summary').append(box); }
    renderActive(data.active);renderHistory(data.history);renderAdmin(data);
  } catch(e){notice(e.message);} finally{busy=false;}
}
async function init(){const params=new URLSearchParams(location.hash.slice(1));const token=params.get('login');if(token){history.replaceState(null,'',location.pathname);try{await api('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});}catch(e){notice(e.message);}}await refresh();setInterval(refresh,5000);}
$('refresh').onclick=()=>refresh();$('logout').onclick=async()=>{await api('/api/session',{method:'DELETE'});await refresh();};$('previous-users').onclick=()=>{offset=Math.max(0,offset-8);refresh();};$('next-users').onclick=()=>{offset+=8;refresh();};document.addEventListener('visibilitychange',refresh);init();
