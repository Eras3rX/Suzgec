/*
 * Контент-скрипт для chatgpt.com: при вставке текста отправляет его на локальный сервер SiftGuard,
 * получает замаскированную версию и вставляет её вместо исходной.
 *
 * Принципы:
 *  - вмешиваемся во вставку текста в поля ввода и в добавление картинок и PDF (вставка, кнопка «прикрепить»,
 *    перетаскивание): файл уходит на локальный сервер, а на сайт попадает уже закрашенная копия.
 *    Остальные файлы, набор текста и отправка — как раньше;
 *  - вставку замаскированного текста делаем через то же событие "paste", что и браузер, поэтому редактор
 *    сайта обрабатывает её своей обычной логикой (переносы строк, длинные вставки и т.д.);
 *  - если расширение выключено или перестало работать (обновили, удалили), вставка идёт как обычно;
 *  - текст не логируется и не сохраняется; он уходит только в фоновый скрипт расширения -> 127.0.0.1.
 */
(function () {
  "use strict";

  if (window.__siftguardLoaded) return;
  window.__siftguardLoaded = true;

  var CHUNK = 4500; // сервер принимает до 5000 символов за раз
  var MAX_TOTAL = 100000; // больше не обрабатываем: слишком долго и странно для чата
  var settings = { enabled: true, blockOnError: true };
  var ownEvent = null; // наше собственное событие paste: его не перехватываем повторно

  var MESSAGES = {
    protected: function (n) { return "SiftGuard: " + n + " məlumat qorundu"; },
    classified: function (category) {
      return "SiftGuard: " + (category ? "«" + category + "»" : "Məxfi/Gizli") + " qrifli mətn bloklandı, yapışdırılmadı. İnzibatçıya bildiriş göndərildi";
    },
    too_long: "SiftGuard: mətn çox uzundur (maks. 100 000 simvol), yapışdırılmadı",
    rate_limited: "SiftGuard: çox sorğu göndərildi, bir az gözləyin. Mətn yapışdırılmadı",
    blocked: "SiftGuard serveri əlçatmazdır, mətn yapışdırılmadı. Serveri işə salın",
    passed: "SiftGuard serveri əlçatmazdır: mətn qorunmadan yapışdırıldı",
    processing: "SiftGuard: sənəd emal olunur, bir neçə saniyə gözləyin…",
    file_done: function (counts) {
      var visual = (counts.UZ || 0) + (counts.IMZA || 0);
      var data = Object.keys(counts).reduce(function (s, k) { return k === "UZ" || k === "IMZA" ? s : s + counts[k]; }, 0);
      if (!data && !visual) return "SiftGuard: sənəddə məlumat tapılmadı, qaralanmış surət əlavə olundu";
      return "SiftGuard: sənəd qaralandı (" + data + " məlumat" + (counts.UZ ? ", " + counts.UZ + " üz" : "") + (counts.IMZA ? ", " + counts.IMZA + " imza" : "") + ")";
    },
    file_classified: function (category) {
      return "SiftGuard: " + (category ? "«" + category + "»" : "Məxfi/Gizli") + " qrifli sənəd bloklandı, əlavə olunmadı. İnzibatçıya bildiriş göndərildi";
    },
    file_too_big: "SiftGuard: fayl çox böyükdür (maks. 10 MB), əlavə olunmadı",
    file_unsupported: function (detail) {
      return "SiftGuard: " + (detail || "fayl emal oluna bilmədi") + ". Fayl əlavə olunmadı";
    },
    file_unavailable: "SiftGuard: sənəd qoruması serverdə quraşdırılmayıb, fayl əlavə olunmadı",
    file_blocked: "SiftGuard serveri əlçatmazdır, fayl əlavə olunmadı. Serveri işə salın",
    file_passed: "SiftGuard serveri əlçatmazdır: fayl qorunmadan əlavə olundu",
  };
  var FILE_TIMEOUT = 120000; // первый запуск распознавания на сервере может быть долгим
  var MAX_FILE = 10 * 1024 * 1024; // как на сервере
  var FILE_NAME = /\.(pdf|png|jpe?g|webp|bmp|gif|tiff?|heic|heif|avif)$/i;

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
    var host = document.getElementById("siftguard-toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "siftguard-toast-host";
      var shadow = host.attachShadow({ mode: "open" }); // стили сайта на нас не влияют
      var style = document.createElement("style");
      style.textContent =
        ".box{position:fixed;right:20px;bottom:90px;z-index:2147483647;max-width:360px;padding:10px 14px;" +
        "border-radius:10px;font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;color:#fff;" +
        "box-shadow:0 4px 16px rgba(0,0,0,.25);opacity:0;transform:translateY(8px);" +
        "transition:opacity .2s,transform .2s;pointer-events:none}" +
        ".show{opacity:1;transform:none}.ok{background:#067647}.warn{background:#b54708}.error{background:#b42318}.info{background:#0b2545}";
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
    host._timer = setTimeout(function () { el.className = "box " + kind; }, kind === "ok" ? 3500 : kind === "info" ? 60000 : 7000);
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

  // ---------- Файлы: картинки и PDF ----------

  function isCandidate(file) {
    if (!file) return false;
    var type = file.type || "";
    return type === "application/pdf" || type.indexOf("image/") === 0 || FILE_NAME.test(file.name || "");
  }

  function toBase64(buffer) {
    var bytes = new Uint8Array(buffer);
    var parts = [];
    for (var i = 0; i < bytes.length; i += 0x8000) parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)));
    return btoa(parts.join(""));
  }

  function fromBase64(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  function sendFileMessage(msg) {
    var timeout = new Promise(function (resolve, reject) {
      setTimeout(function () { reject(new Error("timeout")); }, FILE_TIMEOUT);
    });
    return Promise.race([chrome.runtime.sendMessage(msg), timeout]);
  }

  // Закрасить один файл: {ok, file, counts} или {ok:false, error, ...}. Исходный файл дальше не используется.
  async function protectFile(file) {
    if (file.size > MAX_FILE) return { ok: false, error: "file_too_big" };
    var res;
    try {
      res = await sendFileMessage({ type: "redact", data: toBase64(await file.arrayBuffer()) });
    } catch (e) {
      return { ok: false, error: "unreachable" };
    }
    if (!res || !res.ok) return res || { ok: false, error: "unreachable" };
    var base = (file.name || "sened").replace(/\.[^.]+$/, "") || "sened";
    var ext = res.mime === "application/pdf" ? ".pdf" : ".png";
    return { ok: true, counts: res.counts || {}, file: new File([fromBase64(res.data)], base + "-qaralanmis" + ext, { type: res.mime }) };
  }

  // Обработать список файлов. Картинки и PDF заменяются закрашенными копиями; если закрасить нельзя, файл не
  // пропускаем (кроме режима «вставлять как есть» при недоступном сервере). Остальные файлы идут как были.
  async function processFiles(list) {
    var out = [];
    var totals = {};
    var done = 0;
    var problem = null;
    for (var i = 0; i < list.length; i++) {
      var file = list[i];
      if (!isCandidate(file)) { out.push(file); continue; }
      var res = await protectFile(file);
      if (res.ok) {
        out.push(res.file);
        done++;
        Object.keys(res.counts).forEach(function (k) { totals[k] = (totals[k] || 0) + res.counts[k]; });
      } else if ((res.error === "unreachable" || res.error === "server_error") && !settings.blockOnError) {
        out.push(file);
        problem = problem || { toast: MESSAGES.file_passed, kind: "warn" };
      } else {
        problem = problem || fileProblem(res);
      }
    }
    return { files: out, done: done, totals: totals, problem: problem };
  }

  function fileProblem(res) {
    var e = res.error;
    if (e === "classified") return { toast: MESSAGES.file_classified(res.category), kind: "error" };
    if (e === "file_too_big") return { toast: MESSAGES.file_too_big, kind: "error" };
    if (e === "unsupported") return { toast: MESSAGES.file_unsupported(res.detail), kind: "error" };
    if (e === "unavailable") return { toast: MESSAGES.file_unavailable, kind: "error" };
    if (e === "rate_limited") return { toast: MESSAGES.rate_limited.replace("Mətn yapışdırılmadı", "Fayl əlavə olunmadı"), kind: "warn" };
    return { toast: MESSAGES.file_blocked, kind: "error" };
  }

  // Если среди файлов есть картинки или PDF: обработать и отдать сайту результат через deliver(files).
  // Возвращает true, если событие надо перехватить (файлы наши).
  function handleFiles(files, deliver) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.some(isCandidate)) return false;
    toast(MESSAGES.processing, "info");
    processFiles(list)
      .catch(function () { return { files: [], done: 0, totals: {}, problem: { toast: MESSAGES.file_blocked, kind: "error" } }; })
      .then(function (r) {
        if (r.problem) toast(r.problem.toast, r.problem.kind);
        else if (r.done) toast(MESSAGES.file_done(r.totals), "ok");
        deliver(r.files);
      });
    return true;
  }

  function dataTransferOf(files) {
    var dt = new DataTransfer();
    files.forEach(function (f) { dt.items.add(f); });
    return dt;
  }

  function onFilePaste(event, target) {
    var node = event.target;
    return handleFiles(event.clipboardData && event.clipboardData.files, function (files) {
      if (!files.length) return;
      var ev = new ClipboardEvent("paste", { clipboardData: dataTransferOf(files), bubbles: true, cancelable: true });
      ownEvent = ev;
      try { (node && node.isConnected ? node : target).dispatchEvent(ev); } finally { ownEvent = null; }
    });
  }

  function onFileInput(event) {
    if (event === ownEvent || !settings.enabled) return;
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") return;
    if (!extensionAlive()) { removeListeners(); return; }
    var files = Array.prototype.slice.call(input.files || []);
    if (!files.some(isCandidate)) return;
    event.stopImmediatePropagation(); // сайт не должен увидеть исходные файлы
    if (event.type !== "change") return; // «input» только глушим, обрабатываем один раз — по «change»
    handleFiles(files, function (out) {
      try { input.files = dataTransferOf(out).files; } catch (e) { /* не страшно */ }
      if (!out.length) { try { input.value = ""; } catch (e2) { /* не страшно */ } return; }
      var ev = new Event("change", { bubbles: true });
      ownEvent = ev;
      try { input.dispatchEvent(ev); } finally { ownEvent = null; }
    });
  }

  function onDrop(event) {
    if (event === ownEvent || !settings.enabled) return;
    var dt = event.dataTransfer;
    if (!dt || !dt.files || !dt.files.length) return;
    if (!extensionAlive()) { removeListeners(); return; }
    var target = event.target;
    var x = event.clientX, y = event.clientY;
    var mine = Array.prototype.slice.call(dt.files).some(isCandidate);
    if (!mine) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    handleFiles(dt.files, function (files) {
      if (!files.length) return;
      var ev = new DragEvent("drop", { dataTransfer: dataTransferOf(files), bubbles: true, cancelable: true, clientX: x, clientY: y });
      ownEvent = ev;
      try { (target && target.isConnected ? target : document.body).dispatchEvent(ev); } finally { ownEvent = null; }
    });
  }

  function removeListeners() {
    document.removeEventListener("paste", onPaste, true);
    document.removeEventListener("change", onFileInput, true);
    document.removeEventListener("input", onFileInput, true);
    document.removeEventListener("drop", onDrop, true);
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
    if (error === "classified") return toast(MESSAGES.classified(result.category), "error");
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
      removeListeners();
      return;
    }
    var target = editableTarget(event.target);
    if (!target) return;
    var text = event.clipboardData ? event.clipboardData.getData("text/plain") : "";
    if (!text || !text.trim()) {
      // Нет текста: возможно, вставляют картинку или PDF. Свои файлы закрашиваем, остальное идёт как обычно.
      if (onFilePaste(event, target)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      return;
    }

    event.preventDefault();
    event.stopImmediatePropagation();
    var state = saveSelection(target, event.target);
    protect(text)
      .catch(function () { return { ok: false, error: "unreachable" }; })
      .then(function (result) { finish(target, state, text, result); });
  }

  loadSettings();
  document.addEventListener("paste", onPaste, true); // фаза перехвата: раньше обработчиков сайта
  document.addEventListener("change", onFileInput, true); // кнопка «прикрепить файл»
  document.addEventListener("input", onFileInput, true);
  document.addEventListener("drop", onDrop, true); // перетаскивание файла в окно чата

  window.__siftguardTest = { splitText: splitText, renumber: renumber, isCandidate: isCandidate }; // для проверок; странице сайта не виден

  // Короткое уведомление при загрузке страницы: видно, что именно эта версия расширения работает на вкладке
  // (после обновления расширения вкладку нужно перезагрузить, иначе на ней остаётся старый код).
  if (!window.__siftguardQuiet) {
    setTimeout(function () {
      try { toast("SiftGuard " + chrome.runtime.getManifest().version + " aktivdir: mətn, şəkil və PDF qorunur", "ok"); } catch (e) { /* не страшно */ }
    }, 800);
  }
})();
