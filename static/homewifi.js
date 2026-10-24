// #68: developer (home) Wi-Fi from the phone. The password is never shown after submit or kept.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var ssid = $("hwSsid"), pw = $("hwPassword"), err = $("hwError");
  var MSG = {
    invalid_ssid: "Name must be 1-32 characters.",
    invalid_password: "Password must be 8-63 letters, numbers or symbols (or 64 hex digits).",
    cooldown: "Try again in a few minutes.",
    busy: "Try again in a few minutes."
  };
  var RESULT = {
    saved: "Saved. The frame will use it next time it starts in developer mode.",
    failed: "Couldn't connect. Check the name and password, and that the router is in range."
  };
  function bytes(s) { return new TextEncoder().encode(s).length; }
  function check() {
    if (bytes(ssid.value) < 1 || bytes(ssid.value) > 32 || /[\x00-\x1f\x7f]/.test(ssid.value)) return MSG.invalid_ssid;
    if (!(/^[\x20-\x7e]{8,63}$/.test(pw.value) || /^[0-9a-fA-F]{64}$/.test(pw.value))) return MSG.invalid_password;
    return "";
  }
  function showErr(m) { err.textContent = m; err.hidden = !m; if (m) pw.value = ""; }
  $("hwShow").onclick = function () {
    var show = pw.type === "password"; pw.type = show ? "text" : "password"; this.textContent = show ? "Hide" : "Show";
  };
  $("homeForm").onsubmit = function (e) {
    e.preventDefault();
    var m = check(); showErr(m); if (m) return;
    $("hwConfirmName").textContent = ssid.value; $("hwConfirm").hidden = false; $("hwCancel").focus();
  };
  $("hwCancel").onclick = function () { $("hwConfirm").hidden = true; pw.value = ""; };
  function poll() {
    fetch("/admin/home-wifi/status", {credentials: "same-origin", cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.state === "testing") { setTimeout(poll, 3000); return; }
        $("hwProgress").hidden = true; $("formStep").hidden = false;
        var res = $("hwResult"); res.textContent = RESULT[s.state] || ""; res.hidden = !res.textContent;
      })
      .catch(function () { setTimeout(poll, 3000); });   // phone off the hotspot for a while: keep trying
  }
  $("hwGo").onclick = function () {
    var name = ssid.value, body = JSON.stringify({ssid: name, password: pw.value, confirm: true});
    pw.value = ""; $("hwConfirm").hidden = true;
    var tok = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    fetch("/admin/home-wifi", {method: "POST", credentials: "same-origin",
                               headers: {"Content-Type": "application/json", "X-CSRF-Token": tok}, body: body})
      .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); })
      .then(function (a) {
        if (a[0] !== 202) { showErr(MSG[a[1].error] || "Try again in a few minutes."); return; }
        $("formStep").hidden = true; $("hwResult").hidden = true;
        $("hwTestName").textContent = name; $("hwProgress").hidden = false; setTimeout(poll, 3000);
      })
      .catch(function () { showErr("Try again in a few minutes."); });
  };
})();
