// Talks to the local LangHire server (cli.external assist). Pages cannot reach it: only this extension's origin is allowed.
const API = 'http://127.0.0.1:8799';

async function call(path, body) {
  let r;
  try {
    r = await fetch(API + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  } catch (e) {
    return {error: 'Assistente local parado. Rode: uv run python -m cli.external assist'};
  }
  if (!r.ok) return {error: (await r.json().catch(() => ({}))).error || 'HTTP ' + r.status};
  if (path === '/resume') {
    const bytes = new Uint8Array(await r.arrayBuffer());
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return {data: btoa(s)};
  }
  return {data: await r.json()};
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  call(msg.path, msg.body).then(reply);
  return true;
});

// Toolbar button: fill the open job page, or start the chain from the next queued job.
chrome.action.onClicked.addListener(async tab => {
  try {
    const r = await chrome.tabs.sendMessage(tab.id, {run: true});
    if (r && r.handled) return;
  } catch (e) { /* not a supported page */ }
  const r = await call('/next');
  if (r.data && r.data.url) chrome.tabs.update(tab.id, {url: r.data.url});
});
