'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let show, revision = 0, state, editing = null, networkDirty = false, activeView = 'blocks', polling = false, lastEvent = '', lastEntities = '';
let toastTimer, warnedRevision = null;
const demoQueued = new Map(), demoSending = new Set();
const fmt = (n, digits=3) => n == null ? '—' : Number(n).toFixed(digits);
function toast(text, error=false) { $('toast').textContent = text; $('toast').className = error ? 'error' : ''; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, error ? 7000 : 3500); }
async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json();
  if (response.status === 401) { $('login').hidden = false; $('app').hidden = true; }
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
async function run(task) { try { return await task(); } catch (error) { toast(error.message, true); } }
async function loadConfig() { const data = await api('config'); show=data.show; revision=data.revision; renderConfig(); }
async function saveShow(draft) { const data=await api('config',{show:draft,revision}); show=data.show;revision=data.revision; renderConfig(); }
async function action(name, fields={}) { await api('action',{action:name,...fields}); await poll(); }
function requireHeld() { if (state?.armed) throw new Error('Hold output before changing control blocks.'); }
function renderConfig() {
  $('side-show-name').textContent=show.name;
  document.title=show.name+' · AxisBridge';
  $('block-count').textContent=show.blocks.length; $('heading-count').textContent=show.blocks.length;
  $('empty-state').hidden=show.blocks.length>0;
  $('block-grid').innerHTML=show.blocks.map((b,i)=>`<article class="block-card ${b.enabled?'':'disabled'}" data-block="${esc(b.id)}"><div class="card-head"><span class="card-number">${String(i+1).padStart(2,'0')}</span><div><h3>${esc(b.name)}</h3><small>ENTITY ${b.tracker_id ?? '—'} / ${esc(b.source || 'UNASSIGNED')}</small></div><button class="icon-button" data-edit="${esc(b.id)}" aria-label="Edit ${esc(b.name)}">⋯</button></div><div class="card-body"><div class="source-line"><span>↳</span><span class="source-name" data-source-name>${esc(entityName(b))}</span><span class="axis">${esc(b.axis.toUpperCase())}</span></div><div class="level-readout"><strong><b data-percent>—</b><span>%</span></strong><div class="raw-value">LIVE POSITION<br><b data-value>—</b></div></div><div class="meter"><div class="meter-fill" data-meter></div></div><div class="range"><span>0%</span><span>50%</span><span>100%</span></div><div class="captures"><div class="capture"><small>BOTTOM → 0%</small><strong>${fmt(b.bottom)}</strong><button data-capture="bottom" data-id="${esc(b.id)}">↓ Capture bottom</button></div><div class="capture"><small>TOP → 100%</small><strong>${fmt(b.top)}</strong><button data-capture="top" data-id="${esc(b.id)}">↑ Capture top</button></div></div><div class="targets"><span>MA FADERS →</span>${b.targets.length?b.targets.map(t=>`<span class="target">${esc(t)}</span>`).join(''):'<span class="muted">Not assigned</span>'}</div></div><div class="card-status"><span data-status class="card-state">Waiting for signal</span><span data-sent>No command sent</span></div></article>`).join('');
  if (!networkDirty) {
    for (const [key,value] of Object.entries(show.network)) { const input=$('network-form').elements.namedItem(key); if(input) {if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value;} }
  }
  const count=show.blocks.filter(b=>b.enabled).reduce((n,b)=>n+b.targets.length,0);
  $('fader-stat').textContent=String(count).padStart(2,'0');
  $('fader-detail').textContent=show.blocks.filter(b=>b.enabled).length+' enabled control blocks';
  if(state) renderState();
}
function entityName(b) { return state?.entities.find(e=>e.source===b.source&&e.tracker_id===b.tracker_id)?.name || (b.tracker_id == null ? 'Choose a PSN entity' : 'Entity '+b.tracker_id); }
function view(name) {
  activeView=name;
  for(const el of document.querySelectorAll('.view')) el.hidden=el.id!=='view-'+name;
  for(const el of document.querySelectorAll('[data-view]')) el.classList.toggle('active',el.dataset.view===name);
  $('view-label').textContent=({blocks:'CONTROL BLOCKS',monitor:'SIGNAL MONITOR',network:'NETWORK'})[name];
  $('page-title').textContent=({blocks:'Position into playback.',monitor:'Every signal. In sight.',network:'Make the connection.'})[name];
  $('page-subtitle').textContent=({blocks:'Map a moving axis to the faders that follow it.',monitor:'Watch incoming positions and the commands leaving your bridge.',network:'Keep your PSN and MA networks on their own adapters.'})[name];
  if(state) renderState();
}
function renderState() {
  $('output-badge').textContent=state.demo?(state.armed?'● DEMO · OUTPUT LIVE':'● DEMO · OUTPUT HELD'):state.armed?'● OUTPUT LIVE':'● OUTPUT HELD';
  $('output-badge').classList.toggle('live',state.armed);
  $('footer-state').textContent=state.demo?(state.armed?'DEMO / OUTPUT LIVE':'DEMO / OUTPUT HELD'):state.armed?'OUTPUT LIVE':'OUTPUT HELD';
  $('psn-stat').textContent=({stopped:'Stopped',listening:'Listening',error:'Input error',demo:'Demo signals'})[state.input.state]||state.input.state;
  $('psn-detail').textContent=state.input.running?`${show.network.psn_interface} · UDP ${show.network.psn_port}`:state.input.message;
  $('ma-stat').textContent=({disconnected:'Disconnected',connecting:'Connecting',login:'Logging in',ready:'Connected',error:'Connection error'})[state.ma.state]||state.ma.state;
  $('ma-detail').textContent=state.ma.state==='ready'?`${show.network.ma_host} · authenticated`:state.ma.message;
  $('entity-stat').textContent=String(state.entities.length).padStart(2,'0');
  const live=state.entities.filter(e=>Object.values(e.ages_ms).some(a=>a<=show.network.timeout_ms)).length;
  $('entity-detail').textContent=`${live} receiving · ${state.entities.length-live} waiting / stale`;
  $('arm-output').textContent=state.demo?'Arm demo to MA':'Arm output';
  $('arm-output').disabled=state.armed||state.ma.state!=='ready'||(!state.demo&&!state.input.running);
  $('hold-output').disabled=!state.armed;
  $('start-psn').disabled=state.input.running||state.demo;
  $('stop-psn').disabled=!state.input.running;
  $('connect-ma').disabled=state.ma.running;
  $('disconnect-ma').disabled=!state.ma.running;
  renderConnectionBoxes();
  $('demo-panel').hidden=!state.demo;
  if(state.demo) for(const [id,level] of Object.entries(state.demo_levels)) {
    const input=$('demo-'+id);
    if(input&&document.activeElement!==input) {input.value=level;$('demo-value-'+id).value=level+'%';}
  }
  for(const b of state.blocks) {
    const card=document.querySelector(`[data-block="${b.id}"]`);if(!card)continue;
    card.querySelector('[data-percent]').textContent=fmt(b.percent,1);
    card.querySelector('[data-value]').textContent=fmt(b.value,4);
    card.querySelector('[data-meter]').style.width=(b.percent??0)+'%';
    card.querySelector('[data-status]').textContent='● '+b.status;
    card.querySelector('[data-status]').style.color=['Live','Demo live','Preview'].includes(b.status)?'var(--accent)':'var(--warn)';
    const sent=Object.values(b.sent);
    card.querySelector('[data-sent]').textContent=sent.length?'Last sent '+fmt(sent[0],1)+'%':'No command sent';
    card.querySelector('[data-source-name]').textContent=entityName(show.blocks.find(row=>row.id===b.id));
    for(const btn of card.querySelectorAll('[data-capture]')) btn.disabled=state.armed||b.value==null||b.age_ms>show.network.timeout_ms;
  }
  if(activeView==='monitor') renderMonitor();
}
function renderConnectionBoxes() {
  const psn=$('psn-status-box'), ma=$('ma-status-box');
  const psnConnected=state.input.running&&!state.demo;
  psn.className='connection-box '+(state.demo?'demo':psnConnected?'connected':'disconnected');
  $('psn-box-value').textContent=state.demo?'DEMO ACTIVE':psnConnected?'CONNECTED':state.input.state==='error'?'INPUT ERROR':'NOT CONNECTED';
  $('psn-box-hint').textContent=state.demo?'Virtual PSN is running · click for Network':psnConnected?`${show.network.psn_interface} · UDP ${show.network.psn_port} · click for Network`:state.input.state==='error'?(state.input.message||'Click to inspect PSN settings'):'Click to start PSN input';
  const maConnected=state.ma.state==='ready', maError=state.ma.state==='error', maWorking=state.ma.running&&!maConnected&&!maError;
  ma.className='connection-box '+(maConnected?'connected':maWorking?'working':'disconnected');
  $('ma-box-value').textContent=maConnected?'CONNECTED':maError?'CONNECTION ERROR':maWorking?'CONNECTING':'NOT CONNECTED';
  $('ma-box-hint').textContent=maConnected?`${show.network.ma_host} · authenticated · click for Network`:maError?`${state.ma.message||'MA connection failed'} · click to reconnect`:maWorking?(state.ma.message||'Connecting to grandMA2…'):'grandMA2 Telnet · click to connect';
}
function renderMonitor() {
  $('entity-table').innerHTML=state.entities.length?state.entities.map(e=>{
    const fresh=Object.values(e.ages_ms).some(a=>a<=show.network.timeout_ms);
    const value=a=>`<span class="${e.ages_ms[a]>show.network.timeout_ms?'signal-stale':''}">${fmt(e.values[a])}</span>`;
    return `<tr><td>${esc(e.name)}<small>${esc(e.source)} · ID ${e.tracker_id}</small></td><td>${value('x')}</td><td>${value('y')}</td><td>${value('z')}</td><td>${value('rx')} / ${value('ry')} / ${value('rz')}</td><td class="${fresh?'signal-live':'signal-stale'}">${fresh?'● Receiving':'● Stale / waiting'}</td></tr>`;
  }).join(''):'<tr><td colspan="6" class="muted">No entities yet. Start PSN in Network or try demo signals.</td></tr>';
  $('sent-list').innerHTML=Object.keys(state.last_sent).length?Object.entries(state.last_sent).map(([target,sent])=>`<div class="sent-row"><span>Fader ${esc(target)}</span><strong>${fmt(sent.level,2)}%</strong></div>`).join(''):'<p class="muted">No commands sent in this output session.</p>';
  const key=JSON.stringify(state.events);
  if(key!==lastEvent){$('event-list').innerHTML=state.events.map(e=>`<div class="event"><time>${esc(e.time)}</time><span>${esc(e.message)}</span></div>`).join('');lastEvent=key;}
  $('packet-stats').textContent=`${state.stats.packets} packets · ${state.stats.bad_packets} malformed · ${state.stats.out_of_order} stale axis updates skipped · ${state.stats.commands} commands sent`;
  $('uptime').textContent='UP '+Math.floor(state.uptime/3600)+'h '+Math.floor(state.uptime%3600/60)+'m';
}
async function poll() {
  if(polling||!show||!$('login').hidden)return;
  polling=true;
  try {
    state=await api('state');
    $('portal-status').textContent='● Portal connected';
    $('connection-banner').hidden=true;
    if(state.revision!==revision) {
      if(!$('block-dialog').open&&!networkDirty) await loadConfig();
      else if(warnedRevision!==state.revision){warnedRevision=state.revision;toast('This show changed in another window. Reload before saving your edits.',true);}
    }
    renderState();
  } catch(e) {
    $('portal-status').textContent='● Portal disconnected';
    $('connection-banner').hidden=false;
    $('connection-banner').textContent='Portal connection lost. Displayed values are stale. The bridge may still be running; reconnect to confirm output status.';
    $('output-badge').textContent='● STATUS UNKNOWN';
    $('output-badge').classList.remove('live');
    for(const id of ['arm-output','hold-output','start-psn','stop-psn','connect-ma','disconnect-ma'])$(id).disabled=true;
  } finally {polling=false;}
}
async function init() {
  await loadConfig();state=await api('state');
  $('login').hidden=true;$('app').hidden=false;
  await loadAdapters();renderState();
}
async function loadAdapters() {
  const addresses=await api('interfaces');
  $('adapters').innerHTML=addresses.map(a=>`<option value="${esc(a.ip)}">${esc(a.name)}${a.up?'':' · down'}</option>`).join('');
}
function openBlock(id=null) {
  requireHeld();
  const b=id?show.blocks.find(row=>row.id===id):{id:crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+Math.random().toString(36).slice(2),name:'',source:'',tracker_id:null,axis:'z',bottom:null,top:null,targets:[],smoothing_ms:0,enabled:true};
  editing=structuredClone(b);
  $('dialog-title').textContent=id?'Edit control block':'Add a control block';
  $('block-name').value=b.name;$('block-source').value=b.source;$('block-tracker').value=b.tracker_id??'';
  $('block-axis').value=b.axis;$('block-bottom').value=b.bottom??'';$('block-top').value=b.top??'';
  $('block-targets').value=b.targets.join(', ');$('block-smoothing').value=b.smoothing_ms;$('block-enabled').checked=b.enabled;
  $('block-entity').innerHTML='<option value="">Choose entity / enter manually below</option>'+(state?.entities||[]).map(e=>`<option value="${esc(e.key)}">${esc(e.name)} · ID ${e.tracker_id} · ${esc(e.source)}</option>`).join('');
  $('block-entity').value=b.source+'/'+b.tracker_id;
  $('delete-block').hidden=!id;$('calibration-hint').textContent='Capture live positions from the saved block, or enter exact values here.';
  $('block-dialog').showModal();
}
function clearCalibration() { $('block-bottom').value='';$('block-top').value='';$('calibration-hint').textContent='Source or axis changed. Capture a new bottom and top for this selection.'; }
$('block-entity').addEventListener('change',()=>{const item=state.entities.find(e=>e.key===$('block-entity').value);if(item){$('block-source').value=item.source;$('block-tracker').value=item.tracker_id;clearCalibration();}});
for(const id of ['block-axis','block-source','block-tracker'])$(id).addEventListener('change',clearCalibration);
$('block-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
  requireHeld();
  const b={...editing,name:$('block-name').value.trim(),source:$('block-source').value.trim(),tracker_id:$('block-tracker').value===''?null:Number($('block-tracker').value),axis:$('block-axis').value,bottom:$('block-bottom').value===''?null:Number($('block-bottom').value),top:$('block-top').value===''?null:Number($('block-top').value),targets:$('block-targets').value.split(/[,\s]+/).filter(Boolean),smoothing_ms:Number($('block-smoothing').value),enabled:$('block-enabled').checked};
  const draft=structuredClone(show), index=draft.blocks.findIndex(row=>row.id===b.id);
  if(index===-1)draft.blocks.push(b);else draft.blocks[index]=b;
  await saveShow(draft);$('block-dialog').close();toast('Control block saved');await poll();
});});
for(const id of ['close-dialog','cancel-dialog'])$(id).addEventListener('click',()=>$('block-dialog').close());
$('delete-block').addEventListener('click',()=>run(async()=>{requireHeld();if(!confirm('Delete this control block?'))return;const draft=structuredClone(show);draft.blocks=draft.blocks.filter(b=>b.id!==editing.id);await saveShow(draft);$('block-dialog').close();toast('Block deleted');}));
$('block-grid').addEventListener('click',e=>{const edit=e.target.closest('[data-edit]');if(edit){run(()=>openBlock(edit.dataset.edit));return;}const capture=e.target.closest('[data-capture]');if(capture)run(async()=>{const result=await api('capture',{block_id:capture.dataset.id,endpoint:capture.dataset.capture,revision});show=result.show;revision=result.revision;renderConfig();toast(capture.dataset.capture==='bottom'?'Bottom captured → 0%':'Top captured → 100%');});});
for(const id of ['add-block','first-block'])$(id).addEventListener('click',()=>run(()=>openBlock()));
for(const el of document.querySelectorAll('[data-view]'))el.addEventListener('click',()=>view(el.dataset.view));
$('login-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('login',{key:$('access-key').value.trim()});$('access-key').value='';await init();}catch(error){$('login-error').textContent=error.message;}});
$('rename-show').addEventListener('click',()=>run(async()=>{requireHeld();const name=prompt('Show name',show.name);if(name!==null){const draft=structuredClone(show);draft.name=name;await saveShow(draft);}}));
$('import-show').addEventListener('click',()=>$('import-file').click());
$('import-file').addEventListener('change',e=>run(async()=>{const file=e.target.files[0];if(!file)return;try{requireHeld();if(state.input.running||state.ma.running)throw new Error('Stop PSN and disconnect MA before importing a show.');if(file.size>1024*1024)throw new Error('Show file is larger than 1 MB.');const draft=JSON.parse(await file.text());if(!confirm('Replace the current show with '+(draft.name||file.name)+'?'))return;await saveShow(draft);networkDirty=false;renderConfig();toast('Show imported. Output remains held.');}finally{e.target.value='';}}));
function openArmDialog(){
  const targets=show.blocks.filter(b=>b.enabled).reduce((n,b)=>n+b.targets.length,0);
  $('arm-dialog-title').textContent=state.demo?'Arm demo output?':'Arm live output?';
  $('arm-dialog-copy').textContent=state.demo?'Moving a demo slider will send live fader commands to grandMA2.':'Current mapped PSN values will be sent to grandMA2.';
  $('arm-dialog-targets').textContent=`${targets} assigned MA fader${targets===1?'':'s'} will be enabled. Output stays held unless you choose Arm output.`;
  $('arm-dialog').showModal();
}
$('arm-output').addEventListener('click',()=>run(()=>openArmDialog()));
$('confirm-arm').addEventListener('click',()=>run(async()=>{await action('arm');$('arm-dialog').close();}));
for(const id of ['close-arm-dialog','cancel-arm'])$(id).addEventListener('click',()=>$('arm-dialog').close());
$('hold-output').addEventListener('click',()=>run(()=>action('hold')));
$('network-form').addEventListener('input',e=>{if(e.target.id!=='ma-password'){networkDirty=true;$('save-status').textContent='Unsaved network edits';}});
$('network-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{const draft=structuredClone(show);for(const key of Object.keys(draft.network)){const input=$('network-form').elements.namedItem(key);if(input)draft.network[key]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value.trim();}await saveShow(draft);networkDirty=false;$('save-status').textContent='Saved on this device';toast('Network settings saved');});});
function checkNetworkSaved(){if(networkDirty)throw new Error('Save network changes before connecting.');}
function openMaConnectDialog(){
  checkNetworkSaved();
  if(!show.network.ma_interface||!show.network.ma_host||!show.network.ma_user){view('network');throw new Error('Set and save the MA adapter, console address, and username first.');}
  $('ma-connect-target').textContent=`${show.network.ma_host}:${show.network.ma_port} · ${show.network.ma_user}`;
  $('ma-connect-password').value=$('ma-password').value;
  $('ma-connect-dialog').showModal();
  $('ma-connect-password').focus();
}
$('start-psn').addEventListener('click',()=>run(async()=>{checkNetworkSaved();await action('start_psn');}));
$('stop-psn').addEventListener('click',()=>run(()=>action('stop_psn')));
$('connect-ma').addEventListener('click',()=>run(()=>openMaConnectDialog()));
$('ma-connect-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{const password=$('ma-connect-password').value;await action('connect_ma',{password});$('ma-connect-password').value='';$('ma-password').value='';$('ma-connect-dialog').close();});});
for(const id of ['close-ma-connect','cancel-ma-connect'])$(id).addEventListener('click',()=>$('ma-connect-dialog').close());
$('disconnect-ma').addEventListener('click',()=>run(()=>action('disconnect_ma')));
$('psn-status-box').addEventListener('click',()=>run(async()=>{
  if(state.demo){view('network');toast('Exit demo before starting live PSN.');return;}
  if(state.input.running){view('network');return;}
  try{checkNetworkSaved();await action('start_psn');}catch(error){view('network');throw error;}
}));
$('ma-status-box').addEventListener('click',()=>run(async()=>{if(state.ma.state==='ready'||(state.ma.running&&state.ma.state!=='error')){view('network');return;}if(state.ma.state==='error'&&state.ma.running)await action('disconnect_ma');openMaConnectDialog();}));
$('refresh-interfaces').addEventListener('click',()=>run(async()=>{await loadAdapters();toast('Adapter list refreshed');}));
async function demoStart(){requireHeld();await action('demo',{enabled:true});toast('Demo signals enabled. Connect MA and arm demo output when ready.');view('blocks');}
for(const id of ['start-demo','monitor-demo'])$(id).addEventListener('click',()=>run(demoStart));
$('exit-demo').addEventListener('click',()=>run(()=>action('demo',{enabled:false})));
$('demo-sliders').innerHTML=['Upstage truss','Center pod','Stage lift'].map((name,i)=>`<div class="demo-row"><label for="demo-${i+1}">${name}</label><input id="demo-${i+1}" data-demo="${i+1}" type="range" min="0" max="100" step="0.1" value="${i*50}"><output id="demo-value-${i+1}">${i*50}%</output></div>`).join('');
async function sendDemoLevels(id){if(demoSending.has(id))return;demoSending.add(id);try{while(demoQueued.has(id)){const level=demoQueued.get(id);demoQueued.delete(id);await api('action',{action:'demo_level',id,level});}}finally{demoSending.delete(id);if(demoQueued.has(id))run(()=>sendDemoLevels(id));}}
$('demo-sliders').addEventListener('input',e=>{if(!e.target.dataset.demo)return;const id=Number(e.target.dataset.demo),level=Number(e.target.value);$('demo-value-'+id).value=level+'%';demoQueued.set(id,level);run(()=>sendDemoLevels(id));});
(async()=>{const hash=new URLSearchParams(location.hash.slice(1));const key=hash.get('key');if(key){history.replaceState(null,'',location.pathname);try{await api('login',{key});}catch(e){$('login-error').textContent=e.message;}}try{await init();}catch(e){$('login').hidden=false;$('app').hidden=true;}setInterval(poll,100);})();
