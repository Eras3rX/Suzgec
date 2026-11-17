/*
 * Клиент локального сервера SiftGuard. Общий для фонового скрипта расширения и для тестов.
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
    department: "", // отдел для статистики: maliyye | hr | huquq | "" (не выбран)
  };
  var TIMEOUT_MS = 8000;
  var REDACT_TIMEOUT_MS = 120000; // распознавание документа: секунды, а при первом запуске сервера дольше
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

  async function request(server, path, options, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || TIMEOUT_MS);
    try {
      var init = Object.assign({ cache: "no-store", credentials: "omit", signal: controller.signal }, options || {});
      return await fetch(base(server) + path, init);
    } finally {
      clearTimeout(timer);
    }
  }

  /** Замаскировать текст. Возвращает {ok, masked, count} или {ok:false, error}. */
  async function maskText(text, server, department) {
    if (!isLoopback(server)) return { ok: false, error: "bad_server" };
    try {
      var r = await request(server, "/api/mask", {
        method: "POST",
        // X-SiftGuard-Client: чтобы в панели администратора был виден канал «расширение» (текст не передаётся)
        headers: { "Content-Type": "application/json", "X-SiftGuard-Client": "extension" },
        body: JSON.stringify({ text: text, department: department || null }),
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

  /**
   * Закрасить документ (изображение или PDF). bytes: Uint8Array с файлом.
   * Возвращает {ok, name, mime, data (base64), counts} или {ok:false, error, category?, detail?}.
   * Файл уходит только на локальный сервер и нигде не сохраняется; в ответе нет значений данных.
   */
  async function redactFile(bytes, server, department) {
    if (!isLoopback(server)) return { ok: false, error: "bad_server" };
    try {
      var r = await request(server, "/api/redact", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-SiftGuard-Client": "extension", "X-Department": department || "" },
        body: bytes,
      }, REDACT_TIMEOUT_MS);
      if (r.status === 403) {
        var blocked = await r.json().catch(function () { return {}; });
        return { ok: false, error: "classified", category: blocked.category || "" };
      }
      if (r.status === 413) return { ok: false, error: "file_too_big" };
      if (r.status === 429) return { ok: false, error: "rate_limited" };
      if (r.status === 503) return { ok: false, error: "unavailable" };
      if (r.status === 422) {
        var bad = await r.json().catch(function () { return {}; });
        return { ok: false, error: "unsupported", detail: bad.detail || "" };
      }
      if (!r.ok) return { ok: false, error: "server_error" };
      var data = await r.json();
      return { ok: true, name: data.file.name, mime: data.file.mime, data: data.file.data, counts: data.counts || {} };
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

  root.SiftGuardClient = { DEFAULTS: DEFAULTS, isLoopback: isLoopback, maskText: maskText, redactFile: redactFile, status: status };
})(typeof self !== "undefined" ? self : this);
