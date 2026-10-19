// Restyle PR C (#70 mock 08): "Can't reach the frame". Pages call memoryFramePhone.offline(detail) when a
// request fails at the network level (not for HTTP errors). Try again pings /healthz and restores the page.
(function(){
    "use strict";
    // #79: free-space meters (_storage.html) fill like the frame menu's (#74): scaleX(used fraction).
    document.querySelectorAll(".store .used[data-used]").forEach((u)=>{
        const f = Math.min(1, Math.max(0, parseFloat(u.dataset.used) || 0));
        u.style.transform = "scaleX(" + f.toFixed(3) + ")";
    });
    const box = document.getElementById("offline");
    const page = document.getElementById("page");
    if(!box || !page){ return; }
    const detail = document.getElementById("offlineDetail");
    const still = document.getElementById("offlineStill");
    const strips = ()=> document.querySelectorAll("body > .strip");
    const lines = [];
    function offline(text){
        if(text && lines.indexOf(text) < 0){ lines.push(text); }
        detail.textContent = lines.join(" ");            // textContent: file names are never parsed as HTML
        detail.hidden = !lines.length;
        page.hidden = true;
        strips().forEach((s)=>{ s.hidden = true; });
        box.hidden = false;
        window.scrollTo(0, 0);
        const h = box.querySelector("h1");                 // move focus so screen readers announce the swap
        h.tabIndex = -1; h.focus();
    }
    function online(){
        lines.length = 0;
        box.hidden = true; still.hidden = true; detail.hidden = true;
        page.hidden = false;
        strips().forEach((s)=>{ s.hidden = false; });
        const h = page.querySelector("h1, h2") || page;
        h.tabIndex = -1; h.focus();
    }
    document.getElementById("offlineRetry").addEventListener("click", async ()=>{
        still.hidden = true;
        try{
            const r = await fetch("/healthz", {cache: "no-store", credentials: "same-origin"});
            if(!r.ok){ throw new Error(String(r.status)); }
            online();
        }catch(e){
            still.hidden = false;
        }
    });
    window.memoryFramePhone = {offline: offline, online: online};
})();
