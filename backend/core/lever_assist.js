(async () => {
  // The extension loads this on every Lever page; the bookmarklet only when clicked.
  const AUTO = /[?&]auto=1\b/.test(document.currentScript?.src || '');
  const API = 'http://127.0.0.1:__PORT__', TOKEN = '__TOKEN__';
  const FIELDS = __FIELDS__;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const call = async (path, body) => {
    const r = await fetch(API + path, {method: body ? 'POST' : 'GET', headers: {'X-LangHire-Assist': TOKEN, 'Content-Type': 'application/json'},
      body: body && JSON.stringify(body)});
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'HTTP ' + r.status);
    return path === '/resume' ? r.blob() : r.json();
  };
  let box = document.getElementById('langhire-assist');
  if (!box) { box = document.createElement('div'); box.id = 'langhire-assist'; }
  box.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:380px;background:#111;color:#fff;font:13px/1.45 system-ui;padding:12px 14px;border-radius:8px;box-shadow:0 4px 18px #0007';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const say = html => { box.innerHTML = html; };
  const go = async (path, body) => {
    const r = await call(path, body);
    if (r.state) {
      say(r.state === 'confirmed' ? '✓ Candidatura confirmada pelo Lever. Abrindo a próxima…'
        : 'Lever não confirmou (registrada como incerta, sem reenvio). Abrindo a próxima…');
      await sleep(r.state === 'confirmed' ? 1500 : 4000);
    }
    if (r.url) location.href = r.url; else say('Fila do Lever vazia. 🎉');
  };
  const form = document.getElementById('application-form');
  const text = document.body.innerText;
  const finished = /\/thanks\/?$/.test(location.pathname) || (form && /error verifying your application/i.test(text));
  // A closed posting answers its /apply URL with a 404 page and no form; the chain moves past it.
  const closed = !form && /\/apply\/?$/.test(location.pathname) && /not found|404/i.test(document.title + ' ' + text.slice(0, 500));
  if (AUTO && !finished && !closed && !(form && /\/apply\/?$/.test(location.pathname))) return;
  if (!document.getElementById('langhire-assist')) document.body.append(box);
  try {
    if (finished) {
      say('Registrando o resultado…'); await go('/done', {url: location.href.replace(/\/thanks\/?$/, ''), text: text.slice(0, 4000)}); return;
    }
    if (closed) { say('Vaga fechada no Lever. Abrindo a próxima…'); await go('/skip', {url: location.href}); return; }
    if (!form) { say('Abrindo a próxima vaga…'); await go('/next'); return; }
    say('Preenchendo… perguntas abertas podem levar ~1 min.');
    const fields = FIELDS(form);
    const plan = await call('/plan', {url: location.href, fields});
    const pick = t => form.querySelector(`[data-langhire-external="${t}"]`);
    const fire = e => { e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); };
    const check = [];
    for (const f of plan.fills) {
      const e = pick(f.targets[0]);
      if (f.type === 'file') {
        const transfer = new DataTransfer();
        transfer.items.add(new File([await call('/resume')], f.value, {type: 'application/pdf'}));
        e.files = transfer.files; fire(e);
      } else if (f.type === 'radio' || f.type === 'checkbox-group') {
        const wanted = [].concat(f.value).map(String);
        f.targets.map(pick).forEach(x => { if (wanted.includes(x.value) !== x.checked) x.click(); });
      } else if (f.type === 'checkbox') {
        if (e.checked !== !!f.value) e.click();
      } else if (f.type === 'location') {
        e.focus(); e.value = f.value; fire(e); e.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
        let chosen = false;
        for (let i = 0; i < 30 && !chosen; i++) {
          await sleep(300);
          const options = [...document.querySelectorAll('.dropdown-results > *')]
            .filter(o => o.innerText.includes(f.value) && /Brazil|Brasil/i.test(o.innerText) && (!f.state || o.innerText.includes(f.state)));
          if (options.length) { options[0].click(); chosen = true; }
        }
        if (!chosen) check.push(f.label || 'Localização');
      } else {
        e.focus(); e.value = String(f.value); fire(e); e.blur();
      }
    }
    const missing = [...plan.pending, ...check];
    say(`<b>${esc(plan.company || '')}</b> — ${esc(plan.title || '')}<br>${plan.fills.length} campos preenchidos.` +
      (missing.length ? `<br><b>Preencha à mão:</b><ul style="margin:4px 0 4px 18px">${missing.map(m => '<li>' + esc(m) + '</li>').join('')}</ul>` : '') +
      '<br>Confira, resolva o CAPTCHA e clique <b>Submit</b>.' + (AUTO ? ' A próxima abre sozinha.' : ' Na página de confirmação, clique no favorito de novo.') +
      '<br><button id="langhire-skip" style="margin-top:8px;padding:4px 10px;border-radius:4px;border:0;cursor:pointer">Pular esta vaga</button>');
    document.getElementById('langhire-skip').onclick = () => go('/skip', {url: location.href});
  } catch (error) {
    say('LangHire: ' + esc(error.message) + (AUTO ? '' : '<br><small>O assistente está rodando? (cli.external lever-assist)</small>'));
  }
})();
