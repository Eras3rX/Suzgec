/*
 * Логика режима «копировать и вставить». Чистые функции без обращения к DOM и сети,
 * поэтому их можно проверить отдельно (tests/js/restore.test.html).
 *
 * Таблица «метка -> значение» собирается в браузере и живёт только в памяти вкладки:
 * её нельзя класть в localStorage, куки или отправлять на сервер.
 */
(function (root) {
  "use strict";

  var CATS = "FIN|ID|VOEN|IBAN|KART|TEL|EMAIL|ADRES|AD|TARIX"; // ADRES перед AD: важен порядок
  var CANONICAL = new RegExp("^\\[(" + CATS + ")_(\\d+)\\]$"); // точная форма метки, регистр важен

  // Метка в любом виде: точная [FIN_1], а также испорченная: [fin_1], [ FIN 1 ], [FIN-1], FIN_1, (FIN_1)…
  // Без lookbehind, чтобы работало и на старых телефонах (iOS < 16.4).
  function labelRegex() {
    return new RegExp(
      "\\[\\s*(" + CATS + ")\\s*[_\\- ]\\s*(\\d+)\\s*\\]" +
        "|(^|[^A-Za-z0-9_])(" + CATS + ")_?(\\d+)(?![A-Za-z0-9_])",
      "gi"
    );
  }

  /** Собрать таблицу меток из исходного текста и позиций (spans) от сервера. */
  function buildMapping(text, spans) {
    var chars = Array.from(text); // сервер считает позиции в символах Unicode, не в UTF-16
    var mapping = {};
    spans.forEach(function (s) {
      if (!Object.prototype.hasOwnProperty.call(mapping, s.label)) {
        mapping[s.label] = chars.slice(s.start, s.end).join("");
      }
    });
    return mapping;
  }

  function has(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  /**
   * Вернуть значения на место меток в ответе AI.
   * parts — куски для показа: text | value (восстановлено) | altered (метка испорчена) | unknown (чужая метка).
   * Предупреждения: испорченные метки, метки не из вашего текста, метки, которых в ответе нет.
   */
  function restoreAnswer(answer, mapping) {
    var re = labelRegex();
    var parts = [];
    var last = 0;
    var touched = {}; // метки из mapping, которые встретились в ответе (в любом виде)
    var unknown = [];
    var altered = [];
    var restored = 0;
    var m;

    function pushText(t) {
      if (t) parts.push({ type: "text", text: t });
    }

    while ((m = re.exec(answer)) !== null) {
      var viaBrackets = m[1] !== undefined;
      var prefix = viaBrackets ? "" : m[3];
      var start = m.index + prefix.length;
      var raw = m[0].slice(prefix.length);
      var category = (viaBrackets ? m[1] : m[4]).toUpperCase();
      var label = "[" + category + "_" + (viaBrackets ? m[2] : m[5]) + "]";

      // «Ad1» без скобок и подчёркивания считаем меткой, только если такая метка есть в вашем тексте;
      // иначе это обычное слово вроде «ID5».
      if (!viaBrackets && raw.indexOf("_") < 0 && !has(mapping, label)) continue;

      pushText(answer.slice(last, start));
      last = start + raw.length;

      if (CANONICAL.test(raw)) {
        if (has(mapping, label)) {
          parts.push({ type: "value", text: mapping[label], label: label, category: category });
          touched[label] = true;
          restored++;
        } else {
          parts.push({ type: "unknown", text: raw });
          if (unknown.indexOf(raw) < 0) unknown.push(raw);
        }
      } else {
        var guess = has(mapping, label) ? label : null;
        if (guess) {
          // Метка искажена, но понятно какая: восстанавливаем по смыслу и предупреждаем.
          parts.push({ type: "value", text: mapping[guess], label: guess, category: category, fuzzy: true });
          touched[guess] = true;
          restored++;
        } else {
          parts.push({ type: "altered", text: raw, guess: null });
        }
        altered.push({ text: raw, guess: guess });
      }
    }
    pushText(answer.slice(last));

    var missing = Object.keys(mapping).filter(function (l) {
      return !touched[l];
    });

    var warnings = [];
    var fixed = altered.filter(function (a) { return a.guess; });
    var broken = altered.filter(function (a) { return !a.guess; });
    if (fixed.length) {
      warnings.push(
        "AI etiketlərin yazılışını dəyişib: " +
          fixed.map(function (a) { return "«" + a.text + "» (" + a.guess + " kimi tanındı)"; }).join(", ") +
          ". Məlumatlar mənaya görə bərpa olundu, cavabı yoxlayın."
      );
    }
    if (broken.length) {
      warnings.push(
        "AI etiketləri dəyişib: " +
          broken.map(function (a) { return "«" + a.text + "»"; }).join(", ") +
          ". Onların yerinə məlumat yazılmayıb."
      );
    }
    if (unknown.length) {
      warnings.push(
        "Cavabda sizin mətninizdə olmayan etiketlər var: " + unknown.join(", ") + ". Onlar dəyişdirilmədən saxlanılıb."
      );
    }
    var total = Object.keys(mapping).length;
    if (total > 0 && missing.length === total && !altered.length) {
      warnings.push("Cavabda mətninizdəki etiketlərin heç biri yoxdur: AI onları silib və ya cavabı yenidən yazıb.");
    } else if (missing.length) {
      warnings.push(
        "Cavabda bu etiketlər tapılmadı: " + missing.join(", ") + ". AI onları silmiş və ya xatırlamamış ola bilər. Cavabı yoxlayın."
      );
    }

    return {
      parts: parts,
      text: parts
        .map(function (p) {
          return p.text;
        })
        .join(""),
      restored: restored,
      missing: missing,
      unknown: unknown,
      altered: altered,
      warnings: warnings,
    };
  }

  var api = { buildMapping: buildMapping, restoreAnswer: restoreAnswer };
  root.Suzgec = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
