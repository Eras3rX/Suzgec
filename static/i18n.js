/*
 * Выбор языка интерфейса: азербайджанский (по умолчанию), русский, английский.
 *
 * Как устроено:
 *  - Исходный язык страниц азербайджанский. Русский и английский лежат в static/i18n-dict.js как словарь
 *    «азербайджанская фраза -> перевод». Текстовые узлы, а также title, placeholder, aria-label и alt находятся
 *    автоматически; что не найдено в словаре, остаётся на азербайджанском.
 *  - Фразы, которые страница строит скриптом, берутся через SG.t("азербайджанская фраза", {параметры}).
 *    Если такой текст ставится в элемент и должен меняться при смене языка, используйте SG.setText(el, фраза, параметры).
 *  - Абзацы с разметкой внутри (жирный, ссылки) помечаются data-i18n-rich="id"; перевод берётся из словаря по ключу
 *    "#id" и содержит заранее написанную нами разметку.
 *  - Выбор запоминается только в адресе (?lang=ru) и в ссылках между страницами: ни localStorage, ни куки.
 *  - Серверу язык сообщается заголовком X-Lang (SG.headers()), чтобы сообщения об ошибках приходили на этом же языке.
 */
(function (root) {
  "use strict";

  var LANGS = ["az", "ru", "en"];
  var NAMES = { az: "Azərbaycan dili", ru: "Русский", en: "English" };
  var LOCALES = { az: "az", ru: "ru", en: "en" };
  var DICT = root.SG_DICT || { ru: {}, en: {} };

  var lang = initialLang();
  var texts = new WeakMap();      // текстовый узел -> исходный (азербайджанский) текст
  var attrs = new WeakMap();      // элемент -> {атрибут: исходное значение}
  var rich = new WeakMap();       // элемент -> исходные дочерние узлы
  var hrefs = new WeakMap();      // ссылка -> исходный href
  var dynamic = [];               // элементы, чей текст ставится скриптом: {el, key, params}
  var listeners = [];

  function initialLang() {
    try {
      var q = new URLSearchParams(root.location.search).get("lang");
      if (LANGS.indexOf(q) >= 0) return q;
    } catch (e) { /* адрес без параметров */ }
    return "az";
  }

  function norm(s) { return s.replace(/\s+/g, " ").trim(); }

  function lookup(key) {
    if (lang === "az") return key;
    var d = DICT[lang];
    return d && Object.prototype.hasOwnProperty.call(d, key) ? d[key] : key;
  }

  function fmt(s, params) {
    if (!params) return s;
    return s.replace(/\{(\w+)\}/g, function (m, k) { return params[k] !== undefined ? String(params[k]) : m; });
  }

  /** Фраза на текущем языке. key: азербайджанский текст; {имя} в нём заменяется значением из params. */
  function t(key, params) { return fmt(lookup(key), params); }

  /** Поставить текст в элемент так, чтобы он перевёлся при смене языка. */
  function setText(el, key, params) {
    el.textContent = t(key, params);
    var found = null;
    for (var i = 0; i < dynamic.length; i++) if (dynamic[i].el === el) { found = dynamic[i]; break; }
    if (found) { found.key = key; found.params = params; } else dynamic.push({ el: el, key: key, params: params });
    el.setAttribute("data-sg-dyn", "");
  }

  function clearDynamic(el) {
    dynamic = dynamic.filter(function (d) { return d.el !== el; });
    el.removeAttribute("data-sg-dyn");
  }

  function refreshDynamic() {
    dynamic = dynamic.filter(function (d) { return d.el.isConnected; });
    dynamic.forEach(function (d) { d.el.textContent = t(d.key, d.params); });
  }

  // ---------- Перевод готовой разметки ----------

  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1 };

  function skipped(node) {
    for (var el = node.parentElement; el; el = el.parentElement) {
      if (SKIP[el.tagName] || el.hasAttribute("data-sg-dyn") || el.hasAttribute("data-no-i18n") || el.hasAttribute("data-i18n-rich")) return true;
    }
    return false;
  }

  function translateTexts(scope) {
    var walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      if (skipped(node)) return;
      var original = texts.get(node);
      if (original === undefined) { original = node.nodeValue; texts.set(node, original); }
      var key = norm(original);
      if (!key) return;
      var lead = original.match(/^\s*/)[0], trail = original.match(/\s*$/)[0];
      var next = lang === "az" ? original : lead + lookup(key) + trail;
      if (node.nodeValue !== next) node.nodeValue = next;
    });
  }

  var ATTRS = ["title", "placeholder", "aria-label", "alt"];

  function translateAttrs(scope) {
    var selector = ATTRS.map(function (a) { return "[" + a + "]"; }).join(",") + ",meta[name=description]";
    Array.prototype.forEach.call(scope.querySelectorAll(selector), function (el) {
      if (el.hasAttribute("data-sg-dyn") || el.hasAttribute("data-no-i18n")) return;
      var saved = attrs.get(el);
      if (!saved) { saved = {}; attrs.set(el, saved); }
      var names = el.tagName === "META" ? ["content"] : ATTRS;
      names.forEach(function (a) {
        if (!el.hasAttribute(a)) return;
        if (saved[a] === undefined) saved[a] = el.getAttribute(a);
        el.setAttribute(a, lang === "az" ? saved[a] : lookup(norm(saved[a])));
      });
    });
  }

  function translateRich(scope) {
    Array.prototype.forEach.call(scope.querySelectorAll("[data-i18n-rich]"), function (el) {
      if (!rich.has(el)) rich.set(el, Array.prototype.slice.call(el.childNodes).map(function (n) { return n.cloneNode(true); }));
      var source = lang === "az" ? null : (DICT[lang] || {})["#" + el.getAttribute("data-i18n-rich")];
      if (source) {
        // Разметка в словаре написана нами (статичная), пользовательских данных в ней нет.
        var doc = new DOMParser().parseFromString("<body>" + source + "</body>", "text/html");
        el.replaceChildren.apply(el, Array.prototype.slice.call(doc.body.childNodes).map(function (n) { return document.importNode(n, true); }));
      } else {
        el.replaceChildren.apply(el, rich.get(el).map(function (n) { return n.cloneNode(true); }));
      }
    });
  }

  function translateLinks(scope) {
    Array.prototype.forEach.call(scope.querySelectorAll("a[href^='/']"), function (a) {
      var href = hrefs.get(a);
      if (href === undefined) { href = a.getAttribute("href"); hrefs.set(a, href); }
      if (href.indexOf("/static/") === 0 || href.indexOf("/api/") === 0) return;
      a.setAttribute("href", lang === "az" ? href : href + (href.indexOf("?") >= 0 ? "&" : "?") + "lang=" + lang);
    });
  }

  function apply(scope) {
    scope = scope || document;
    document.documentElement.setAttribute("lang", lang);
    translateTexts(scope === document ? document.documentElement : scope);
    translateAttrs(scope === document ? document.documentElement : scope);
    translateRich(scope === document ? document.documentElement : scope);
    translateLinks(scope === document ? document.documentElement : scope);
    refreshSwitch();
  }

  // ---------- Переключатель в шапке (справа сверху) ----------

  function refreshSwitch() {
    Array.prototype.forEach.call(document.querySelectorAll("#lang-switch button"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === lang));
    });
  }

  function mountSwitch() {
    var box = document.getElementById("lang-switch");
    if (!box || box.children.length) return;
    LANGS.forEach(function (code) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("data-lang", code);
      b.setAttribute("lang", code);
      b.setAttribute("title", NAMES[code]);
      b.setAttribute("data-no-i18n", "");
      b.textContent = code.toUpperCase();
      b.addEventListener("click", function () { setLang(code); });
      box.appendChild(b);
    });
  }

  function setLang(next) {
    if (LANGS.indexOf(next) < 0 || next === lang) return;
    lang = next;
    try {
      var url = new URL(root.location.href);
      if (lang === "az") url.searchParams.delete("lang"); else url.searchParams.set("lang", lang);
      root.history.replaceState(null, "", url.pathname + url.search + url.hash);
    } catch (e) { /* адрес не обязателен */ }
    apply(document);
    refreshDynamic();
    listeners.slice().forEach(function (fn) { try { fn(lang); } catch (e) { /* страница сама следит за своими ошибками */ } });
  }

  function start() { mountSwitch(); apply(document); }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  root.SG = {
    t: t,
    setText: setText,
    clearDynamic: clearDynamic,
    lang: function () { return lang; },
    locale: function () { return LOCALES[lang]; },
    setLang: setLang,
    onLang: function (fn) { listeners.push(fn); },
    apply: apply,
    headers: function (extra) { return Object.assign({ "X-Lang": lang }, extra || {}); },
    languages: LANGS,
  };
})(typeof window !== "undefined" ? window : this);
