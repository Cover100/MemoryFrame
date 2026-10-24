// #68: home (developer) Wi-Fi from the phone, Wren's 12a-12f. The password is never shown after submit or kept.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var ssid = $("hwSsid"), pw = $("hwPassword"), err = $("hwError"), show = $("hwShow");
  var MSG = {
    invalid_ssid: "Name must be 1-32 characters.",
    invalid_password: "Password must be 8-63 letters, numbers or symbols (or 64 hex digits).",
    cooldown: "Try again in a few minutes.",
    busy: "Try again in a few minutes."
  };
  var STATES = ["formStep", "hwTesting", "hwSaved", "hwFailed"];
  function view(id) { STATES.forEach(function (s) { $(s).hidden = s !== id; }); }
  function bytes(s) { return new TextEncoder().encode(s).length; }
  function check() {
    if (bytes(ssid.value) < 1 || bytes(ssid.value) > 32 || /[\x00-\x1f\x7f]/.test(ssid.value)) return ["invalid_ssid", ssid];
    if (!(/^[\x20-\x7e]{8,63}$/.test(pw.value) || /^[0-9a-fA-F]{64}$/.test(pw.value))) return ["invalid_password", pw];
    return null;
  }
  function setPwShown(on) {
    pw.type = on ? "text" : "password"; show.textContent = on ? "Hide" : "Show";
    show.setAttribute("aria-label", on ? "Hide password" : "Show password"); show.setAttribute("aria-pressed", on ? "true" : "false");
  }
  function showErr(code, field) {
    ssid.classList.remove("bad"); pw.classList.remove("bad");
    err.textContent = code ? MSG[code] || MSG.busy : ""; err.hidden = !code;
    if (code) { pw.value = ""; setPwShown(false); if (field) field.classList.add("bad"); }
  }
  show.onclick = function () { setPwShown(pw.type === "password"); };
  // 12c: confirm sheet, Cancel is the default.
  $("homeForm").onsubmit = function (e) {
    e.preventDefault();
    var c = check(); showErr(c && c[0], c && c[1]); if (c) return;
    $("hwConfirmName").textContent = ssid.value; $("hwConfirm").hidden = false; $("hwCancel").focus();
  };
  $("hwCancel").onclick = function () { $("hwConfirm").hidden = true; pw.value = ""; setPwShown(false); $("hwSave").focus(); };
  $("hwConfirm").addEventListener("keydown", function (e) { if (e.key === "Escape") $("hwCancel").onclick(); });
  function failed(rateLimited) {
    // 429: "Try again in a few minutes." in the 12f layout, without the Try again button.
    $("hwFailedLine").textContent = rateLimited ? MSG.cooldown : "The frame is back on its own Wi-Fi. Your saved network did not change.";
    $("hwFailedSteps").hidden = rateLimited; $("hwRetry").hidden = rateLimited;
    view("hwFailed");
  }
  var testing = "";
  function poll() {     // 12d: no countdown; keeps polling while the phone is off the frame's Wi-Fi
    fetch("/admin/home-wifi/status", {credentials: "same-origin", cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.state === "testing") { setTimeout(poll, 3000); return; }
        if (s.state === "saved") { $("hwSavedName").textContent = s.ssid || testing; view("hwSaved"); $("hwDone").focus(); }
        else { failed(false); $("hwRetry").focus(); }
      })
      .catch(function () { setTimeout(poll, 3000); });
  }
  $("hwDone").onclick = function () { location.href = "/admin/"; };
  $("hwRetry").onclick = function () { showErr(""); view("formStep"); ssid.focus(); };
  $("hwGo").onclick = function () {
    var name = ssid.value, body = JSON.stringify({ssid: name, password: pw.value, confirm: true});
    pw.value = ""; setPwShown(false); $("hwConfirm").hidden = true;
    var tok = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    fetch("/admin/home-wifi", {method: "POST", credentials: "same-origin",
                               headers: {"Content-Type": "application/json", "X-CSRF-Token": tok}, body: body})
      .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); })
      .then(function (a) {
        if (a[0] === 429 || (a[1] && a[1].error === "cooldown")) { failed(true); return; }
        if (a[0] !== 202) { showErr(a[1] && a[1].error, a[1] && a[1].error === "invalid_ssid" ? ssid : pw); return; }
        testing = name; $("hwTestName").textContent = name; view("hwTesting"); setTimeout(poll, 3000);
      })
      .catch(function () { showErr("busy"); });
  };
  // Back on the page while a test runs (phone rejoined): resume the testing view.
  fetch("/admin/home-wifi/status", {credentials: "same-origin", cache: "no-store"})
    .then(function (r) { return r.json(); })
    .then(function (s) { if (s.state === "testing") { testing = s.ssid || ""; $("hwTestName").textContent = testing; view("hwTesting"); setTimeout(poll, 3000); } })
    .catch(function () {});
})();
