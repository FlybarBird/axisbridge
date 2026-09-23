'use strict';
// The hardware display uses the selected group; the choice travels with the show.
(() => {
  const nav = document.createElement('button');
  nav.className = 'nav'; nav.dataset.view = 'screen';
  nav.innerHTML = '<span>▣</span> Slate screen';
  nav.addEventListener('click', () => view('screen'));
  document.querySelector('nav').append(nav);
  const section = document.createElement('section');
  section.id = 'view-screen'; section.className = 'view'; section.hidden = true;
  section.innerHTML = `<div class="panel"><h2>Blocks on the home screen</h2>
    <p class="muted">Choose which blocks the physical <b>Update Low</b> and <b>Update High</b> buttons capture. Each button needs a continuous four-second hold. Release or slide away to cancel.</p>
    <p class="muted">All selected blocks update together. Output must be held and every selected block must have a fresh signal. Low = 0% · High = 100%.</p>
    <form id="screen-form"><fieldset id="screen-options" style="border:0;padding:0;margin:20px 0"></fieldset>
    <div class="network-actions"><button class="primary" type="submit" id="screen-save">Save screen selection</button>
    <button type="button" class="quiet" id="screen-reset">Reset edits</button></div></form>
    <p id="screen-summary" class="muted" role="status"></p></div>`;
  document.querySelector('.page').append(section);
  let dirty = false, editRevision = 0, saving = false;
  function render() {
    if (!show || dirty) return;
    editRevision = revision;
    $('screen-options').innerHTML = show.blocks.length ? show.blocks.map(b =>
      `<label class="checkbox-label" style="display:flex;align-items:center;gap:12px;margin:0;padding:14px 0;border-bottom:1px solid var(--line)">
      <input type="checkbox" name="screen-block" value="${esc(b.id)}" ${b.on_screen?'checked':''} style="width:auto;margin:0">
      <span><b>${esc(b.name)}</b><br><small>${esc(b.source || 'Unassigned')} · ${b.tracker_id == null ? 'No entity' : 'Entity '+b.tracker_id} · ${esc(b.axis.toUpperCase())}${b.enabled?'':' · Disabled'}</small></span></label>`
    ).join('') : '<p>Add control blocks first.</p>';
    $('screen-summary').textContent = `${show.blocks.filter(b=>b.on_screen).length} blocks selected on the Slate home screen. Changes appear on the router immediately after saving.`;
    update();
  }
  function update() {
    if (!state) return;
    $('screen-options').disabled = state.armed || saving;
    $('screen-save').disabled = state.armed || saving;
  }
  $('screen-form').addEventListener('change', () => {dirty=true; $('screen-summary').textContent='Unsaved screen selection';});
  $('screen-reset').addEventListener('click', () => {dirty=false; render();});
  $('screen-form').addEventListener('submit', e => {
    e.preventDefault();
    run(async () => {
      requireHeld();
      const ids = new Set([...$('screen-options').querySelectorAll('input:checked')].map(el=>el.value));
      const draft = structuredClone(show);
      for (const b of draft.blocks) b.on_screen=ids.has(b.id);
      saving=true; update();
      try {
        const result = await api('config', {show:draft, revision:editRevision});
        show=result.show; revision=result.revision; dirty=false;
        renderConfig(); render(); toast('Slate home screen updated');
      } finally {saving=false; update();}
    });
  });
  document.addEventListener('axisbridge-config', render);
  document.addEventListener('axisbridge-state', update);
  render();
})();
