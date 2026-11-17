/*
 * Контент-скрипт для chatgpt.com: при вставке текста отправляет его на локальный сервер Süzgəc,
 * получает замаскированную версию и вставляет её вместо исходной.
 *
 * Принципы:
 *  - вмешиваемся ТОЛЬКО во вставку текста в поля ввода; файлы, картинки, набор текста, отправка — как раньше;
 *  - вставку замаскированного текста делаем через то же событие "paste", что и браузер, поэтому редактор
 *    сайта обрабатывает её своей обычной логикой (переносы строк, длинные вставки и т.д.);
 *  - если расширение выключено или перестало работать (обновили, удалили), вставка идёт как обычно;
 *  - текст не логируется и не сохраняется; он уходит только в фоновый скрипт расширения -> 127.0.0.1.
 */
(function () {
  "use strict";

  if (window.__suzgecLoaded) return;
  window.__suzgecLoaded = true;

  var CHUNK = 4500; // сервер принимает до 5000 символов за раз
  var MAX_TOTAL = 100000; // больше не обрабатываем: слишком долго и странно для чата
  var settings = { enabled: true, blockOnError: true };
  var ownEvent = null; // наше собственное событие paste: его не перехватываем повторно

  var MESSAGES = {
    protected: function (n) { return "Süzgəc: " + n + " məlumat qorundu"; },
    classified: "Süzgəc: Məxfi/Gizli qeydli mətn bloklandı, yapışdırılmadı",
    too_long: "Süzgəc: mətn çox uzundur (maks. 100 000 simvol), yapışdırılmadı",
    rate_limited: "Süzgəc: çox sorğu göndərildi, bir az gözləyin. Mətn yapışdırılmadı",
    blocked: "Süzgəc serveri əlçatmazdır, mətn yapışdırılmadı. Serveri işə salın",
    passed: "Süzgəc serveri əlçatmazdır: mətn qorunmadan yapışdırıldı",
  };

  // ---------- Настройки ----------

  function loadSettings() {
    try {
      chrome.storage.local.get({ enabled: true, blockOnError: true }, function (s) {
        settings.enabled = s.enabled !== false;
        settings.blockOnError = s.blockOnError !== false;
      });
      chrome.storage.onChanged.addListener(function (changes, area) {
        if (area !== "local") return;
        if (changes.enabled) settings.enabled = changes.enabled.newValue !== false;
        if (changes.blockOnError) settings.blockOnError = changes.blockOnError.newValue !== false;
      });
    } catch (e) {
      /* без настроек работаем с настройками по умолчанию */
    }
  }

  function extensionAlive() {
    try {
      return !!(chrome.runtime && chrome.runtime.id);
    } catch (e) {
      return false;
    }
  }

  // ---------- Уведомление ----------

  function toast(message, kind) {
    var host = document.getElementById("suzgec-toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "suzgec-toast-host";
      var shadow = host.attachShadow({ mode: "open" }); // стили сайта на нас не влияют
      var style = document.createElement("style");
      style.textContent =
        ".box{position:fixed;right:20px;bottom:90px;z-index:2147483647;max-width:360px;padding:10px 14px;" +
        "border-radius:10px;font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;color:#fff;" +
        "box-shadow:0 4px 16px rgba(0,0,0,.25);opacity:0;transform:translateY(8px);" +
        "transition:opacity .2s,transform .2s;pointer-events:none}" +
        ".show{opacity:1;transform:none}.ok{background:#067647}.warn{background:#b54708}.error{background:#b42318}";
      var box = document.createElement("div");
      box.className = "box";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      shadow.append(style, box);
      (document.documentElement || document).appendChild(host);
    }
    var el = host.shadowRoot.querySelector(".box");
    el.textContent = message;
    el.className = "box show " + kind;
    clearTimeout(host._timer);
    host._timer = setTimeout(function () { el.className = "box " + kind; }, kind === "ok" ? 3500 : 7000);
  }

  // ---------- Разбиение длинного текста ----------

  function splitText(text, limit) {
    var chunks = [];
    var rest = text;
    while (rest.length > limit) {
      var window_ = rest.slice(0, limit);
      var cut = 0;
      var seps = ["\n\n", "\n", ". ", "; ", ", ", " "];
      for (var i = 0; i < seps.length && !cut; i++) {
        var idx = window_.lastIndexOf(seps[i]);
        if (idx > limit * 0.5) cut = idx + seps[i].length;
      }
      if (!cut) cut = limit;
      var code = rest.charCodeAt(cut - 1);
      if (code >= 0xd800 && code <= 0xdbff) cut--; // не режем суррогатную пару пополам
      chunks.push(rest.slice(0, cut));
      rest = rest.slice(cut);
    }
    chunks.push(rest);
    return chunks;
  }

  // Каждый кусок сервер нумерует с [AD_1]. Сдвигаем номера, чтобы в склеенном тексте метки не повторялись.
  function renumber(masked, offsets) {
    var maxes = {};
    var fixed = masked.replace(/\[([A-Z]+)_(\d+)\]/g, function (m, cat, n) {
      var num = Number(n);
      if (!(cat in maxes) || num > maxes[cat]) maxes[cat] = num;
      return "[" + cat + "_" + (num + (offsets[cat] || 0)) + "]";
    });
    Object.keys(maxes).forEach(function (cat) { offsets[cat] = (offsets[cat] || 0) + maxes[cat]; });
    return fixed;
  }

  // ---------- Обращение к серверу (через фоновый скрипт) ----------

  function sendMessage(msg) {
    var timeout = new Promise(function (resolve, reject) {
      setTimeout(function () { reject(new Error("timeout")); }, 15000);
    });
    return Promise.race([chrome.runtime.sendMessage(msg), timeout]);
  }

  async function protect(text) {
    if (text.length > MAX_TOTAL) return { ok: false, error: "too_long" };
    var chunks = splitText(text, CHUNK);
    var offsets = {};
    var out = [];
    var count = 0;
    for (var i = 0; i < chunks.length; i++) {
      var res;
      try {
        res = await sendMessage({ type: "mask", text: chunks[i] });
      } catch (e) {
        return { ok: false, error: "unreachable" };
      }
      if (!res || !res.ok) return res || { ok: false, error: "unreachable" };
      out.push(renumber(res.masked, offsets));
      count += res.count;
    }
    return { ok: true, masked: out.join(""), count: count };
  }

  // ---------- Вставка ----------

  function editableTarget(node) {
    var el = node instanceof Element ? node : node && node.parentElement;
    if (!el) return null;
    var ta = el.closest("textarea");
    if (ta) return ta;
    var input = el.closest("input");
    if (input) {
      var type = (input.getAttribute("type") || "text").toLowerCase();
      return type === "text" || type === "search" ? input : null; // пароли, e-mail и т.п. не трогаем
    }
    return el.closest('[contenteditable]:not([contenteditable="false"])');
  }

  function isTextControl(el) {
    return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
  }

  function saveSelection(target, eventTarget) {
    var state = { eventTarget: eventTarget };
    if (isTextControl(target)) {
      state.start = target.selectionStart;
      state.end = target.selectionEnd;
    } else {
      var sel = window.getSelection();
      if (sel && sel.rangeCount) state.range = sel.getRangeAt(0).cloneRange();
    }
    return state;
  }

  function restoreSelection(target, state) {
    if (document.activeElement !== target && !target.contains(document.activeElement)) target.focus();
    if (state.start !== undefined && state.start !== null) {
      try { target.setSelectionRange(state.start, state.end); } catch (e) { /* не страшно */ }
    } else if (state.range) {
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(state.range);
    }
  }

  function insert(target, state, text) {
    restoreSelection(target, state);

    if (!isTextControl(target)) {
      // Редактор сайта (ProseMirror и т.п.) сам обработает «вставку» по своей обычной логике.
      var data = new DataTransfer();
      data.setData("text/plain", text);
      var ev = new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true });
      var node = state.eventTarget && state.eventTarget.isConnected ? state.eventTarget : target;
      ownEvent = ev;
      try { node.dispatchEvent(ev); } finally { ownEvent = null; }
      if (ev.defaultPrevented) return true;
    }

    var ok = false;
    try { ok = document.execCommand("insertText", false, text); } catch (e) { ok = false; }
    if (!ok && isTextControl(target)) {
      var s = target.selectionStart || 0;
      var e2 = target.selectionEnd || 0;
      target.setRangeText(text, s, e2, "end");
      target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertFromPaste", data: text }));
      ok = true;
    }
    return ok;
  }

  function finish(target, state, original, result) {
    if (result && result.ok) {
      insert(target, state, result.masked);
      if (result.count > 0) toast(MESSAGES.protected(result.count), "ok");
      return;
    }
    var error = result && result.error;
    // Текст с грифом, слишком большой текст и превышение лимита: оригинал не вставляем никогда.
    if (error === "classified") return toast(MESSAGES.classified, "error");
    if (error === "too_long") return toast(MESSAGES.too_long, "error");
    if (error === "rate_limited") return toast(MESSAGES.rate_limited, "warn");
    // Сервер недоступен: по настройке либо блокируем (безопаснее), либо вставляем как есть.
    if (settings.blockOnError) return toast(MESSAGES.blocked, "error");
    insert(target, state, original);
    toast(MESSAGES.passed, "warn");
  }

  function onPaste(event) {
    if (event === ownEvent || !settings.enabled) return;
    if (!extensionAlive()) {
      // Расширение обновили или удалили: больше не вмешиваемся, сайт работает как обычно.
      document.removeEventListener("paste", onPaste, true);
      return;
    }
    var target = editableTarget(event.target);
    if (!target) return;
    var text = event.clipboardData ? event.clipboardData.getData("text/plain") : "";
    if (!text || !text.trim()) return; // файлы и картинки идут как обычно

    event.preventDefault();
    event.stopImmediatePropagation();
    var state = saveSelection(target, event.target);
    protect(text)
      .catch(function () { return { ok: false, error: "unreachable" }; })
      .then(function (result) { finish(target, state, text, result); });
  }

  loadSettings();
  document.addEventListener("paste", onPaste, true); // фаза перехвата: раньше обработчиков сайта

  window.__suzgecTest = { splitText: splitText, renumber: renumber }; // для проверок; странице сайта не виден
})();
