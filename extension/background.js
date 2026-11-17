/*
 * Фоновый скрипт (service worker). Единственный, кто ходит в сеть: страница chatgpt.com не может
 * обращаться к http://127.0.0.1 сама (смешанное содержимое, CORS), а расширение с host_permissions может.
 * Текст не логируется и не сохраняется; хранятся только настройки и счётчики сессии.
 */
importScripts("mask-client.js");

async function settings() {
  return chrome.storage.local.get(SiftGuardClient.DEFAULTS);
}

async function countProtected(count) {
  var cur = await chrome.storage.session.get({ pastes: 0, protected: 0 });
  await chrome.storage.session.set({ pastes: cur.pastes + 1, protected: cur.protected + count });
}

async function handle(msg) {
  var s = await settings();
  if (msg && msg.type === "mask" && typeof msg.text === "string") {
    var result = await SiftGuardClient.maskText(msg.text, s.server, s.department);
    if (result.ok) await countProtected(result.count);
    return result;
  }
  if (msg && msg.type === "redact" && typeof msg.data === "string") {
    var bin = atob(msg.data);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    var file = await SiftGuardClient.redactFile(bytes, s.server, s.department);
    if (file.ok) {
      var total = Object.keys(file.counts).reduce(function (sum, k) { return sum + file.counts[k]; }, 0);
      await countProtected(total);
    }
    return file;
  }
  if (msg && msg.type === "status") {
    return SiftGuardClient.status(s.server);
  }
  return { ok: false, error: "bad_request" };
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (sender.id !== chrome.runtime.id) return false; // только наши собственные скрипты
  handle(msg)
    .then(sendResponse)
    .catch(function () { sendResponse({ ok: false, error: "server_error" }); });
  return true; // ответ будет асинхронным
});
