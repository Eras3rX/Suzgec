/*
 * Клиент локального сервера Süzgəc. Общий для фонового скрипта расширения и для тестов.
 *
 * Правила приватности:
 *  - текст отправляется ТОЛЬКО на адрес из loopback (127.0.0.1 / localhost / [::1]);
 *  - ничего не логируется, текст нигде не сохраняется;
 *  - значения персональных данных сервер не возвращает: только замаскированный текст и счётчики.
 */
(function (root) {
  "use strict";

  var DEFAULTS = {
    enabled: true, // выключатель расширения
    blockOnError: true, // если сервер недоступен: true — не вставлять текст, false — вставить как есть
    server: "http://127.0.0.1:8000",
  };
  var TIMEOUT_MS = 8000;
  var LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]"];

  function isLoopback(url) {
    try {
      var u = new URL(url);
      return (u.protocol === "http:" || u.protocol === "https:") && LOOPBACK_HOSTS.indexOf(u.hostname) >= 0;
    } catch (e) {
      return false;
    }
  }

  function base(server) {
    return String(server || DEFAULTS.server).replace(/\/+$/, "");
  }

  async function request(server, path, options) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
    try {
      var init = Object.assign({ cache: "no-store", credentials: "omit", signal: controller.signal }, options || {});
      return await fetch(base(server) + path, init);
    } finally {
      clearTimeout(timer);
    }
  }

  /** Замаскировать текст. Возвращает {ok, masked, count} или {ok:false, error}. */
  async function maskText(text, server) {
    if (!isLoopback(server)) return { ok: false, error: "bad_server" };
    try {
      var r = await request(server, "/api/mask", {
        method: "POST",
        // X-Suzgec-Client: чтобы в панели администратора был виден канал «расширение» (текст не передаётся)
        headers: { "Content-Type": "application/json", "X-Suzgec-Client": "extension" },
        body: JSON.stringify({ text: text }),
      });
      if (r.status === 403) {
        var blocked = await r.json().catch(function () { return {}; });
        return { ok: false, error: "classified", category: blocked.category || "" };
      }
      if (r.status === 413) return { ok: false, error: "too_long" };
      if (r.status === 429) return { ok: false, error: "rate_limited" };
      if (!r.ok) return { ok: false, error: "server_error" };
      var data = await r.json();
      return { ok: true, masked: data.masked_text, count: (data.spans || []).length };
    } catch (e) {
      return { ok: false, error: "unreachable" };
    }
  }

  /** Состояние сервера для всплывающего окна. */
  async function status(server) {
    if (!isLoopback(server)) return { reachable: false, error: "bad_server" };
    try {
      var r = await request(server, "/api/config");
      if (!r.ok) return { reachable: false, error: "server_error" };
      var c = await r.json();
      return { reachable: true, model: c.model || "off" };
    } catch (e) {
      return { reachable: false, error: "unreachable" };
    }
  }

  root.SuzgecClient = { DEFAULTS: DEFAULTS, isLoopback: isLoopback, maskText: maskText, status: status };
})(typeof self !== "undefined" ? self : this);
