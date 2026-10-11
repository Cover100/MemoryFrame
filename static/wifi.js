// MF-18: /admin/wifi form: Show/Hide, client-side validation (mirrors the server), confirm step.
(function(){
    "use strict";
    const $ = (id)=> document.getElementById(id);
    const form = $("wifiForm");
    if(!form){ return; }
    const MSG_NAME = "Name must be 1-32 characters.";
    const MSG_PASSWORD = "Password must be 8-63 letters, numbers or symbols (no emoji or accents).";
    const MSG_SAME = "That's the current Wi-Fi. Change the name or password.";
    let confirmed = false;

    function utf8Len(s){ return new TextEncoder().encode(s).length; }
    function check(){
        const name = $("ssid").value, pw = $("password").value;
        if(utf8Len(name) < 1 || utf8Len(name) > 32 || /[\u0000-\u001f\u007f-\u009f]/.test(name)){ return MSG_NAME; }
        if(!/^[\x20-\x7e]{8,63}$/.test(pw)){ return MSG_PASSWORD; }
        // The current password is never sent to the phone: "unchanged" is decided by the server.
        return null;
    }
    function showError(msg){ $("error").textContent = msg || ""; $("error").hidden = !msg; }

    $("showPw").addEventListener("click", ()=>{
        const p = $("password");
        p.type = p.type === "password" ? "text" : "password";
        $("showPw").textContent = p.type === "password" ? "Show" : "Hide";
    });
    form.addEventListener("submit", (e)=>{
        if(confirmed){ return; }
        e.preventDefault();
        const err = check();
        showError(err);
        if(err){ return; }
        $("confirmName").textContent = $("ssid").value;
        $("formStep").hidden = true;
        $("confirmStep").hidden = false;
    });
    $("cancelBtn").addEventListener("click", ()=>{
        $("password").value = "";                  // values kept, password cleared
        $("confirmStep").hidden = true;
        $("formStep").hidden = false;
    });
    $("confirmBtn").addEventListener("click", ()=>{ confirmed = true; form.submit(); });
    window.memoryFrameWifi = {check: check, MSG_SAME: MSG_SAME};
})();
