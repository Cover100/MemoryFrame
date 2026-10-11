// MF-17: each tap saves immediately (PUT /api/v1/settings) and shows "Saved" for 1.5 s.
(function(){
    "use strict";
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    const timers = {};
    function status(key, msg, err){
        const el = document.querySelector(`.status[data-for="${key}"]`);
        el.textContent = msg; el.classList.toggle("err", !!err);
        clearTimeout(timers[key]);
        if(!err){ timers[key] = setTimeout(()=>{ el.textContent = ""; }, 1500); }
    }
    async function save(key, value, revert){
        try{
            const r = await fetch("/api/v1/settings", {method: "PUT", credentials: "same-origin",
                headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
                body: JSON.stringify({[key]: value})});
            if(!r.ok){ throw new Error(String(r.status)); }
            status(key, "Saved");
        }catch(e){
            revert();
            status(key, "Couldn't save. Try again.", true);
        }
    }
    document.querySelectorAll(".seg button").forEach((b)=>{
        b.addEventListener("click", ()=>{
            const group = b.parentElement.querySelectorAll("button");
            const prev = [...group].find((x)=> x.getAttribute("aria-checked") === "true");
            if(prev === b){ return; }
            group.forEach((x)=> x.setAttribute("aria-checked", x === b ? "true" : "false"));
            const key = b.dataset.key;
            const value = key === "slide_seconds" ? Number(b.dataset.value) : b.dataset.value;
            save(key, value, ()=> group.forEach((x)=> x.setAttribute("aria-checked", x === prev ? "true" : "false")));
        });
    });
    const sh = document.getElementById("shuffle");
    sh.addEventListener("change", ()=>{
        const v = sh.checked;
        save("shuffle", v, ()=>{ sh.checked = !v; });
    });
})();
