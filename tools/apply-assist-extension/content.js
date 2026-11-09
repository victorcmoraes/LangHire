// LangHire Apply Assist: fills queued Lever, Greenhouse and LinkedIn Easy Apply forms with answers from the
// local server. It never clicks Submit; it only notices the candidate's own click and reports the portal outcome.
(() => {
  if (window.__langhireAssist) return;
  window.__langhireAssist = true;

  const MARK = 'lh_assist';
  const CONFIRMED = /application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)|we (?:have )?received your application|thank(?:s| you) for (?:applying|your application)|(?:candidatura|aplica[çc][ãa]o|inscri[çc][ãa]o) (?:foi )?(?:enviada|recebida)|agradecemos (?:a )?sua inscri[çc][ãa]o|candidatura (?:foi )?(?:realizada|finalizada|conclu[íi]da)|voc[êe] se candidatou/i;
  // Lever's own receipt for a posting that already holds this candidate's application (e.g. a second Submit).
  const ALREADY = /your application was already submitted|we received your previous application/i;
  const ERROR = /error verifying your application|unable to submit|application (?:was )?not submitted|there was an error|erro ao (?:enviar|verificar)|não foi possível enviar/i;
  const SUBMIT = /^(submit application|submit|enviar candidatura|enviar inscrição|finalizar candidatura|concluir candidatura)$/i;
  const NEXT = /^(next|continue|avançar|continuar|próximo)\b/i;
  const REVIEW = /^(review|revisar|revisar candidatura|avaliar)\b/i;
  const EASY = /\b(easy apply|candidatura simplificada)\b/i;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9+]+/g, ' ').trim();
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const visible = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e.innerText || e.getAttribute('aria-label') || '').trim();

  const api = (path, body) => new Promise((ok, fail) => chrome.runtime.sendMessage({path, body}, r => {
    if (chrome.runtime.lastError) return fail(new Error(chrome.runtime.lastError.message));
    if (!r || r.error) return fail(new Error(r ? r.error : 'sem resposta'));
    ok(r.data);
  }));
  const storage = {
    get: () => new Promise(r => chrome.storage.local.get('lh_active', v => r(v.lh_active || null))),
    set: v => new Promise(r => chrome.storage.local.set({lh_active: v}, r)),
    clear: () => new Promise(r => chrome.storage.local.remove('lh_active', r)),
  };

  // ---------- page identity ----------
  function platform() {
    const h = location.hostname;
    if (h === 'jobs.lever.co' || h === 'jobs.eu.lever.co') return 'lever';
    if (h === 'job-boards.greenhouse.io' || h === 'boards.greenhouse.io') return 'greenhouse';
    if (h === 'www.linkedin.com' && /^\/jobs\/view\/\d+/.test(location.pathname)) return 'linkedin';
    if (/^(?!login\.|portal\.|www\.)[a-z0-9-]+\.gupy\.io$/.test(h)) return 'gupy';
    if (h === 'jobs.ashbyhq.com') return 'ashby';
    if (h === 'apply.workable.com') return 'workable';
    return null;
  }
  function jobKey() {
    const p = location.pathname;
    let m;
    if (platform() === 'ashby') return (m = p.match(/^\/([\w-]+)\/([0-9a-f-]{36})/i)) ? 'ashby:' + m[1] + ':' + m[2].toLowerCase() : null;
    if (platform() === 'workable') return (m = p.match(/^\/([\w-]+)\/j\/([A-Za-z0-9]+)/)) ? 'workable:' + m[1].toLowerCase() + ':' + m[2].toUpperCase() : null;
    if ((m = p.match(/^\/([\w-]+)\/([0-9a-f-]{36})/i))) return 'lever:' + m[1] + ':' + m[2].toLowerCase();
    if ((m = p.match(/^\/([\w-]+)\/jobs\/(\d+)/))) return 'greenhouse:' + m[1] + ':' + m[2];
    if ((m = p.match(/^\/jobs\/view\/(\d+)/))) return 'linkedin:' + m[1];
    if ((m = p.match(/^\/(?:candidates\/)?jobs\/(\d+)/)) && location.hostname.endsWith('.gupy.io')) return 'gupy:' + location.hostname.split('.')[0] + ':' + m[1];
    return null;
  }
  const jobUrl = () => location.origin + location.pathname.replace(/\/(apply|thanks|confirmation|application)\/?$/, '');

  // ---------- panel ----------
  let box;
  function say(html, buttons = []) {
    if (!box) {
      box = document.createElement('div');
      box.id = 'langhire-assist';
      box.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:400px;max-height:60vh;overflow:auto;background:#111;color:#fff;font:13px/1.45 system-ui;padding:12px 14px;border-radius:8px;box-shadow:0 4px 18px #0007';
      document.body.append(box);
    }
    box.innerHTML = '<div style="font-weight:600;margin-bottom:4px;color:#8ab4ff">LangHire</div>' + html +
      (buttons.length ? '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">' + buttons.map((b, i) =>
        `<button data-lh="${i}" style="padding:4px 10px;border-radius:4px;border:0;cursor:pointer">${esc(b[0])}</button>`).join('') + '</div>' : '');
    buttons.forEach((b, i) => { box.querySelector(`[data-lh="${i}"]`).onclick = b[1]; });
  }
  const list = items => items.length ? `<ul style="margin:4px 0 4px 18px;padding:0">${items.map(m => '<li>' + esc(m) + '</li>').join('')}</ul>` : '';

  async function advance(r) {
    if (r.state) {
      say(r.state === 'confirmed' ? '✓ Candidatura confirmada. Abrindo a próxima…' : 'Sem confirmação do portal: registrada como incerta (não reenviar). Abrindo a próxima…');
      await sleep(r.state === 'confirmed' ? 1500 : 4000);
    }
    if (r.url) location.href = r.url;
    else say('Fila vazia. 🎉');
  }
  const skip = async () => {
    const active = await storage.get();
    await storage.clear();
    advance(await api('/skip', {url: active ? active.url : jobUrl()}));
  };

  // ---------- DOM helpers ----------
  function setValue(e, v) {
    e.focus();
    e.value = String(v);  // Isolated-world write bypasses React's value tracker, so React sees the change.
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
    e.blur();
  }
  async function resumeFile(name) {
    // The server tailors one PDF per job; the page URL picks it, and an unmatched URL (LinkedIn) gets the last planned job's.
    const b64 = await api('/resume', {url: location.href});
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    return new File([bytes], name, {type: 'application/pdf'});
  }
  async function attach(input, name) {
    const t = new DataTransfer();
    t.items.add(await resumeFile(name));
    input.files = t.files;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  }
  async function waitFor(fn, ms = 10000, step = 250) {
    for (let t = 0; t < ms; t += step) { const v = fn(); if (v) return v; await sleep(step); }
    return null;
  }
  // Searchable selects (react-select, LinkedIn typeahead): type, then click the single matching option.
  async function choose(input, typed, match) {
    input.focus();
    input.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
    input.click();
    setValueKeep(input, typed);
    const scope = () => document.getElementById(input.getAttribute('aria-controls') || '') || document;
    const option = await waitFor(() => {
      const found = [...scope().querySelectorAll('[role=option],.dropdown-location')].filter(o => visible(o) && match(o.innerText.trim()));
      return found.length ? found[0] : null;
    }, 10000, 300);
    if (!option) {
      // Some widgets (Lever's location) select the single suggestion themselves.
      if (input.value && match(input.value.trim())) return true;
      input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      return false;
    }
    option.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
    option.click();
    await sleep(300);
    return true;
  }
  function setValueKeep(e, v) {
    e.value = String(v);
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
  }
  // Any required control still empty after filling is listed for the candidate.
  function emptyRequired(root) {
    const out = new Set();
    for (const e of root.querySelectorAll('input,select,textarea')) {
      if (e.disabled || ['hidden', 'submit', 'button', 'file'].includes(e.type) || !visible(e)) continue;
      const required = e.required || e.getAttribute('aria-required') === 'true';
      if (!required) continue;
      if (e.type === 'radio' || e.type === 'checkbox') {
        const group = e.name ? [...root.querySelectorAll(`input[name="${CSS.escape(e.name)}"]`)] : [e];
        if (group.some(x => x.checked)) continue;
      } else if (e.getAttribute('role') === 'combobox') {
        const single = e.closest('.select__container')?.querySelector('.select__single-value,.select__multi-value');
        if (single || e.value) continue;
      } else if (String(e.value).trim()) continue;
      const label = [...(e.labels || [])].map(l => l.innerText).join(' ').trim() || e.getAttribute('aria-label') ||
        e.closest('fieldset')?.querySelector('legend')?.innerText || e.closest('.application-question,.field')?.innerText?.split('\n')[0] || e.name || e.id;
      out.add(label.replace(/\s*[*✱]\s*$/, '').trim());
    }
    return [...out];
  }

  // ---------- submit / outcome ----------
  function watchSubmit(active, scope) {
    document.addEventListener('click', async ev => {
      const b = ev.target.closest && ev.target.closest('button,input[type=submit]');
      if (!b || !SUBMIT.test(text(b) || b.value || '')) return;
      if (scope && !scope().contains(b)) return;
      active.submitted = true; await storage.set(active);
      api('/submitted', {url: active.url}).catch(() => {});
      outcome(active);
    }, true);
  }
  let watching = false;
  // Only the portal's own outcome region counts: a Greenhouse description can say "thank you for applying".
  function verdict(p) {
    const path = location.pathname;
    if (p === 'lever') {
      const form = document.getElementById('application-form');
      if (/\/thanks\/?$/.test(path) || (!form && ALREADY.test(document.body.innerText))) return {done: true, region: document.body.innerText};
      return {failed: !!form && ERROR.test(form.innerText), region: form ? form.innerText : ''};
    }
    if (p === 'greenhouse') {
      const form = document.getElementById('application-form');
      if (/\/confirmation\/?$/.test(path) || (!form && CONFIRMED.test(document.body.innerText))) return {done: true, region: document.body.innerText};
      return {failed: !!form && ERROR.test(form.innerText), region: form ? form.innerText : ''};
    }
    if (p === 'gupy') return {done: CONFIRMED.test(document.body.innerText), failed: false, region: document.body.innerText};
    if (p === 'ashby' || p === 'workable') {
      // The form (with its Submit button) is replaced by the confirmation; a description cannot confirm.
      const open = buttonIn(document, SUBMIT);
      return {done: !open && CONFIRMED.test(document.body.innerText), failed: !!open && ERROR.test(document.body.innerText), region: document.body.innerText};
    }
    const dialogs = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')].filter(visible).map(d => d.innerText).join(' \n ');
    return {done: CONFIRMED.test(dialogs), failed: ERROR.test(dialogs), region: dialogs};
  }
  async function outcome(active) {
    if (watching) return;
    watching = true;
    say('Envio detectado. Aguardando a confirmação do portal…<br><small>Se houver CAPTCHA ou código por e-mail, resolva aqui mesmo.</small>',
      [['Portal não confirmou: marcar incerta e seguir', async () => {
        await storage.clear(); advance(await api('/done', {url: active.url, text: verdict(active.platform).region.slice(0, 4000), uncertain: true}));
      }]]);
    for (;;) {
      const v = verdict(active.platform);
      if (v.done || v.failed) {
        await sleep(v.done ? 800 : 0);
        await storage.clear();
        return advance(await api('/done', {url: active.url, text: verdict(active.platform).region.slice(0, 4000)}));
      }
      await sleep(1000);
    }
  }

  // ---------- Lever ----------
  async function lever(active) {
    const form = document.getElementById('application-form');
    if (!form) {
      if (/\/apply\/?$/.test(location.pathname) && /not found|404/i.test(document.title + ' ' + document.body.innerText.slice(0, 500))) {
        say('Vaga fechada no Lever. Abrindo a próxima…'); return skip();
      }
      if (!/\/apply\/?$/.test(location.pathname)) { location.href = jobUrl() + '/apply?' + MARK + '=1'; return; }
      return say('Formulário do Lever não encontrado.', [['Pular esta vaga', skip]]);
    }
    say('Preenchendo… perguntas abertas podem levar ~1 min.');
    const plan = await api('/plan', {url: location.href, fields: LH_FIELDS.lever(form)});
    const pick = t => form.querySelector(`[data-langhire-external="${t}"]`);
    const check = [];
    for (const f of plan.fills) {
      const e = pick(f.targets[0]);
      if (!e) { check.push(f.label); continue; }
      if (f.type === 'file') await attach(e, f.value);
      else if (f.type === 'radio' || f.type === 'checkbox-group') {
        const wanted = [].concat(f.value).map(String);
        f.targets.map(pick).forEach(x => { if (x && wanted.includes(x.value) !== x.checked) x.click(); });
      } else if (f.type === 'checkbox') { if (e.checked !== !!f.value) e.click(); }
      else if (f.type === 'location') {
        const ok = await choose(e, f.value, t => norm(t).startsWith(norm(f.value)) && /\b(BRA|Brazil|Brasil)\b/i.test(t) && (!f.state || norm(t).includes(norm(f.state))));
        if (!ok) check.push(f.label || 'Localização');
      } else setValue(e, f.value);
    }
    ready(plan, [...plan.pending, ...check, ...emptyRequired(form)], 'Confira, resolva o CAPTCHA e clique <b>Submit application</b>.');
    watchSubmit(active, () => form);
  }

  // ---------- Greenhouse ----------
  function greenhouseControls(form) {
    const present = [];
    for (const e of form.querySelectorAll('input[id],select[id],textarea[id]')) {
      if (e.type === 'hidden' || e.disabled) continue;
      const role = e.getAttribute('role');
      const value = e.type === 'file' ? [...e.files].map(f => f.name) : e.type === 'checkbox' ? e.checked :
        role === 'combobox' ? (e.closest('.select__container')?.querySelector('.select__single-value')?.innerText || '') : e.value;
      present.push({id: e.id, render: role || e.tagName.toLowerCase(), value});
    }
    const names = new Set([...form.querySelectorAll('input[type=checkbox][name]')].map(e => e.name));
    for (const name of names) {
      if (present.some(p => p.id === name)) continue;
      const boxes = [...form.querySelectorAll('input[type=checkbox]')].filter(e => e.name === name);
      present.push({id: name, render: 'checkboxes', value: boxes.filter(e => e.checked).map(e => e.value)});
    }
    return present;
  }
  async function greenhouse(active) {
    const form = await waitFor(() => document.getElementById('application-form'), 15000);
    if (!form) return say('Formulário do Greenhouse não encontrado (a vaga pode redirecionar para o site da empresa).', [['Pular esta vaga', skip]]);
    await sleep(1500);  // The uploader configures itself after the form renders.
    say('Preenchendo… perguntas abertas podem levar ~1 min.');
    const plan = await api('/plan', {url: location.href, fields: greenhouseControls(form)});
    const check = [];
    const byId = id => document.getElementById(id);
    for (const f of plan.fills) {
      const e = byId(f.id);
      try {
        if (f.type === 'file') {
          await attach(e, f.value);
          const ok = await waitFor(() => (byId('upload-label-resume')?.closest('[role=group]') || e?.parentElement || form).innerText.includes(f.value), 15000);
          if (!ok) check.push('Currículo (anexe o PDF)');
        } else if (f.type === 'checkbox') {
          if (e.checked !== !!f.value) e.click();
        } else if (f.type === 'checkbox-group' && f.render !== 'combobox') {
          const wanted = f.value.map(String);
          [...form.querySelectorAll('input[type=checkbox]')].filter(x => x.name === f.id)
            .forEach(x => { if (wanted.includes(x.value) !== x.checked) x.click(); });
        } else if (f.type === 'checkbox-group') {
          for (const label of f.text) if (!await choose(e, label, t => norm(t) === norm(label))) check.push(f.label);
        } else if (f.type === 'phone-country') {
          if (!await choose(e, 'Bra', t => /^(brazil|brasil) ?\+55$/.test(norm(t)))) check.push('Código do país (Brasil +55)');
        } else if (f.type === 'location') {
          const ok = await choose(e, f.value, t => norm(t).startsWith(norm(f.value)) && (!f.state || norm(t).includes(norm(f.state))) && /brazil|brasil/.test(norm(t)));
          if (!ok) check.push(f.label);
        } else if (f.render === 'combobox') {
          if (!await choose(e, f.text, t => norm(t) === norm(f.text))) check.push(f.label);
        } else setValue(e, f.value);
      } catch (error) { check.push(f.label); }
    }
    ready(plan, [...plan.pending, ...check, ...emptyRequired(form)], 'Confira e clique <b>Submit application</b>. Se pedir código por e-mail, digite-o aqui.');
    watchSubmit(active, () => document.body);
  }

  // ---------- Gupy ----------
  // The posting page leads to /candidates/jobs/{id}/apply; Gupy's own profile fills most steps after login.
  // Each step: fill empty controls only, list the rest, and let the candidate move on and finalize.
  async function gupy(active) {
    const job = location.pathname.match(/^\/jobs\/(\d+)/);
    if (job) { location.href = `/candidates/jobs/${job[1]}/apply?jobBoardSource=gupy_public_page`; return; }
    const form = await waitFor(() => [...document.querySelectorAll('main form, form, main')].find(f => f.querySelector('input,select,textarea')), 15000);
    if (!form) return say('Gupy: entre na sua conta e avance; clique <b>Preencher esta etapa</b> quando houver perguntas.',
      [['Preencher esta etapa', () => gupy(active)], ['Pular esta vaga', skip]]);
    say('Preenchendo a etapa…');
    const fields = LH_FIELDS.linkedin(form).filter(f => f.value === '' || f.value === false || (Array.isArray(f.value) && !f.value.length));
    const plan = await api('/plan', {url: active.url, fields});
    const pick = t => form.querySelector(`[data-langhire-field="${t}"]`);
    const check = [];
    for (const f of plan.fills) {
      const e = pick(f.targets[0]);
      if (!e) { check.push(f.label); continue; }
      if (f.type === 'file') { await attach(e, f.value); continue; }
      if (f.type === 'radio') {
        const x = pick(f.targets[f.options.findIndex(o => o.value === f.value)]);
        if (x && !x.checked) (x.labels?.[0] || x).click();
      } else if (f.type === 'checkbox') { if (e.checked !== !!f.value) (e.labels?.[0] || e).click(); }
      else if (f.type === 'autocomplete') { if (!await choose(e, f.value, t => norm(t).startsWith(norm(f.value)))) check.push(f.label); }
      else setValue(e, f.value);
    }
    const missing = [...new Set([...plan.pending, ...check, ...emptyRequired(form)].filter(Boolean))];
    say(`<b>${esc(plan.company || '')}</b> — ${esc(plan.title || '')}<br>${plan.fills.length} campos preenchidos nesta etapa.` +
      (missing.length ? `<br><b>Preencha à mão:</b>${list(missing)}` : '') +
      '<br>Avance as etapas; em cada nova etapa clique <b>Preencher esta etapa</b>. No fim, clique <b>Finalizar candidatura</b>.',
      [['Preencher esta etapa', () => gupy(active)], ['Pular esta vaga', skip]]);
    watchSubmit(active, () => document.body);
  }

  // ---------- Ashby / Workable ----------
  // Hosted one-page forms without a dedicated adapter: the generic inspector fills empty labelled controls.
  async function hosted(active) {
    const suffix = active.platform === 'ashby' ? '/application' : '/apply';
    if (!location.pathname.endsWith(suffix)) { location.href = jobUrl() + suffix + '?' + MARK + '=1'; return; }
    // Ashby renders its form without a <form> element; the page body then holds the controls.
    const form = await waitFor(() => [...document.querySelectorAll('form, main')].find(f => f.querySelector('input,textarea,select')) ||
      (buttonIn(document, SUBMIT) && document.querySelector('input,textarea') ? document.body : null), 20000);
    if (!form) return say('Formulário não encontrado (vaga fechada?).', [['Pular esta vaga', skip]]);
    await sleep(1000);
    say('Preenchendo… perguntas abertas podem levar ~1 min.');
    const fields = LH_FIELDS.linkedin(form).filter(f => f.type === 'file' || f.value === '' || f.value === false || (Array.isArray(f.value) && !f.value.length));
    const plan = await api('/plan', {url: active.url, fields});
    const pick = t => form.querySelector(`[data-langhire-field="${t}"]`);
    const check = [];
    for (const f of plan.fills) {
      const e = pick(f.targets[0]);
      if (!e) { check.push(f.label); continue; }
      if (f.type === 'file') { await attach(e, f.value); continue; }
      if (f.type === 'radio') {
        const x = pick(f.targets[f.options.findIndex(o => o.value === f.value)]);
        if (x && !x.checked) (x.labels?.[0] || x).click();
      } else if (f.type === 'checkbox') { if (e.checked !== !!f.value) (e.labels?.[0] || e).click(); }
      else if (f.type === 'autocomplete') { if (!await choose(e, f.value, t => norm(t).startsWith(norm(f.value)))) check.push(f.label); }
      else setValue(e, f.value);
    }
    ready(plan, [...plan.pending, ...check, ...emptyRequired(form)],
      'Confira (botões Sim/Não e listas customizadas podem precisar de clique), resolva o CAPTCHA se houver e clique <b>Submit</b>.');
    watchSubmit(active, () => document.body);
  }

  // ---------- LinkedIn Easy Apply ----------
  const modal = () => document.querySelector('.jobs-easy-apply-modal, [data-test-modal][role=dialog], [role=dialog][aria-labelledby*="easy-apply"], [data-easy-apply]') ||
    [...document.querySelectorAll('[role=dialog]')].find(d => visible(d) && /candidat|apply/i.test(d.innerText.slice(0, 300)));
  const buttonIn = (root, re) => [...root.querySelectorAll('button')].find(b => visible(b) && re.test(text(b)));
  async function linkedin(active, step = 0) {
    let dialog = modal();
    if (!dialog) {
      const open = await waitFor(() => [...document.querySelectorAll('button,a')].find(b => visible(b) && EASY.test(text(b))), 15000);
      if (!open) return say('Botão de candidatura simplificada não encontrado (vaga fechada ou já aplicada?).', [['Pular esta vaga', skip]]);
      open.click();
      dialog = await waitFor(modal, 15000);
      if (!dialog) return say('O formulário do LinkedIn não abriu.', [['Tentar de novo', () => linkedin(active)], ['Pular esta vaga', skip]]);
    }
    watchSubmit(active, () => modal() || document.body);
    for (; step < 15; step++) {
      dialog = modal();
      await waitFor(() => buttonIn(dialog, new RegExp(NEXT.source + '|' + REVIEW.source + '|' + SUBMIT.source, 'i')), 15000);
      say(`Preenchendo a etapa ${step + 1}…`);
      const plan = await api('/plan', {url: jobUrl(), fields: LH_FIELDS.linkedin(dialog), step});
      const check = [];
      const pick = t => dialog.querySelector(`[data-langhire-field="${t}"]`);
      for (const f of plan.fills) {
        const e = pick(f.targets[0]);
        if (!e) { check.push(f.label); continue; }
        if (f.type === 'file') { await attach(e, f.value); await waitFor(() => dialog.innerText.includes(f.value), 15000) || check.push('Currículo'); }
        else if (f.type === 'select-one') setValue(e, f.value);
        else if (f.type === 'radio') {
          const i = f.options.findIndex(o => o.value === f.value);
          const x = pick(f.targets[i]);
          if (x && !x.checked) (x.closest('[role=radio]') || x.labels?.[0] || x).click();
        } else if (f.type === 'checkbox') {
          if (e.checked !== !!f.value) (e.labels?.[0] || e.nextElementSibling || e).click();
        } else if (f.type === 'autocomplete') {
          const ok = await choose(e, f.value, t => f.state ? norm(t).startsWith(norm(f.value)) && norm(t).includes(norm(f.state)) : norm(t) === norm(f.value));
          if (!ok) check.push(f.label);
        } else setValue(e, f.value);
      }
      await sleep(400);
      const missing = [...plan.pending, ...check, ...emptyRequired(dialog)];
      const title = `<b>${esc(plan.company || '')}</b> — ${esc(plan.title || '')}<br>`;
      const resume = [['Continuar preenchimento', () => linkedin(active, step)], ['Pular esta vaga', skip]];
      if (missing.length) {
        return say(title + `Etapa ${step + 1}: <b>preencha à mão</b>:${list(missing)}Depois clique <b>Continuar preenchimento</b> (ou avance você mesmo).`, resume);
      }
      if (buttonIn(dialog, SUBMIT)) {
        return say(title + 'Tudo preenchido. Revise e clique <b>Enviar candidatura</b>. A próxima abre sozinha.', [['Pular esta vaga', skip]]);
      }
      const next = buttonIn(dialog, NEXT) || buttonIn(dialog, REVIEW);
      if (!next) return say(title + 'Etapa desconhecida; continue à mão.', resume);
      const before = dialog.innerText;
      next.click();
      const moved = await waitFor(() => modal() && modal().innerText !== before, 10000);
      const errors = [...(modal() || dialog).querySelectorAll('.artdeco-inline-feedback--error,[role=alert]')].filter(visible).map(text).filter(Boolean);
      if (!moved || errors.length) return say(title + 'O LinkedIn não avançou:' + list(errors.length ? errors : ['sem mudança na etapa']) + 'Corrija e clique <b>Continuar preenchimento</b>.', resume);
    }
    say('Limite de etapas atingido; continue à mão.', [['Pular esta vaga', skip]]);
  }

  function ready(plan, missing, action) {
    const unique = [...new Set(missing.filter(Boolean))];
    say(`<b>${esc(plan.company || '')}</b> — ${esc(plan.title || '')}<br>${plan.fills.length} campos preenchidos.` +
      (unique.length ? `<br><b>Preencha à mão:</b>${list(unique)}` : '<br>') + action + ' A próxima abre sozinha.',
      [['Pular esta vaga', skip]]);
  }

  // ---------- entry ----------
  async function main(manual) {
    const p = platform();
    let active = await storage.get();
    // Gupy's later application steps have no job id in the URL; they belong to the job being applied to.
    const key = jobKey() || (p === 'gupy' && active && active.platform === 'gupy' ? active.key : null);
    if (!p || !key) return false;
    if (active && active.key === key && active.submitted) { outcome(active); return true; }
    const marked = new URLSearchParams(location.search).has(MARK);
    if (!marked && !manual && !(active && active.key === key)) return false;
    const url = jobKey() ? jobUrl() : active.url;
    try {
      const status = await api('/status', {url});
      if (!status.queued) {
        if (manual || marked) say('Esta vaga não está na fila elegível (já enviada, incerta, pulada ou desqualificada).',
          [['Abrir a próxima da fila', async () => advance(await api('/next'))]]);
        return true;
      }
      active = {key, url, platform: p, submitted: false, at: Date.now()};
      await storage.set(active);
      await ({lever, greenhouse, gupy, ashby: hosted, workable: hosted, linkedin})[p](active);
    } catch (error) {
      say('Erro: ' + esc(error.message), [['Tentar de novo', () => main(true)], ['Pular esta vaga', skip]]);
    }
    return true;
  }

  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (!msg.run) return;
    const p = platform();
    reply({handled: !!(p && jobKey())});
    if (p) main(true);
  });
  main(false);
})();
