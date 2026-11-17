// Всплывающее окно: выключатель, адрес сервера, состояние. Тексты сюда не попадают, только счётчики.
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var MODEL = {
    ready: "AI modeli: hazırdır",
    loading: "AI modeli: yüklənir…",
    off: "AI modeli söndürülüb (yalnız qaydalar işləyir)",
    unavailable: "AI modeli əlçatmazdır (yalnız qaydalar işləyir)",
  };

  async function refresh() {
    var box = $("server-status");
    try {
      var st = await chrome.runtime.sendMessage({ type: "status" });
      if (st && st.reachable) {
        box.className = "status ok";
        box.textContent = "Server işləyir ✓";
        $("model-status").textContent = MODEL[st.model] || "";
      } else {
        box.className = "status bad";
        box.textContent = "Server əlçatmazdır. Süzgəc serverini işə salın.";
        $("model-status").textContent = "";
      }
    } catch (e) {
      box.className = "status bad";
      box.textContent = "Server əlçatmazdır.";
    }
    var c = await chrome.storage.session.get({ pastes: 0, protected: 0 });
    $("count").textContent = "Bu sessiyada: " + c.pastes + " yapışdırma, " + c.protected + " məlumat qorunub";
  }

  async function init() {
    var s = await chrome.storage.local.get(SuzgecClient.DEFAULTS);
    $("enabled").checked = s.enabled;
    $("block").checked = s.blockOnError;
    $("server").value = s.server;
    $("department").value = s.department || "";
    $("department").addEventListener("change", function () {
      chrome.storage.local.set({ department: $("department").value });
    });

    $("enabled").addEventListener("change", function () {
      chrome.storage.local.set({ enabled: $("enabled").checked });
    });
    $("block").addEventListener("change", function () {
      chrome.storage.local.set({ blockOnError: $("block").checked });
    });
    $("save").addEventListener("click", async function () {
      var value = $("server").value.trim().replace(/\/+$/, "");
      var msg = $("msg");
      if (!SuzgecClient.isLoopback(value)) {
        msg.className = "msg bad";
        msg.textContent = "Yalnız yerli server icazəlidir: http://127.0.0.1:8000 və ya http://localhost:8000";
        return;
      }
      await chrome.storage.local.set({ server: value });
      msg.className = "msg";
      msg.textContent = "Saxlanıldı";
      refresh();
    });
    refresh();
  }

  init();
})();
