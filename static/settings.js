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
            status(key, "✓ Saved");
        }catch(e){
            revert();
            if(e instanceof TypeError && window.memoryFramePhone){ memoryFramePhone.offline(); }
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

    // #73: message on the frame. Live preview via textContent (never as HTML); saved on Save.
    const ta = document.getElementById("frameMessage");
    if(ta){
        const max = Number(ta.dataset.max) || 140;
        const count = document.getElementById("messageCount");
        const prev = document.getElementById("messagePreview"), prevText = document.getElementById("messagePreviewText");
        const prevEmpty = document.getElementById("messagePreviewEmpty");
        // Approximates memoryframe/message.clean (the server is authoritative): NFC, strip
        // controls/format/invisible fillers, collapse spaces, count code points.
        const norm = (v)=> v.normalize("NFC")
            .replace(/[\u115F\u1160\u3164\uFFA0\u2800]/g, "")
            .replace(/[\p{Cc}\p{Cf}]/gu, (m)=> (/[\n\r\t\u200C\u200D]/.test(m) ? (m === "\u200C" || m === "\u200D" ? m : " ") : ""))
            .replace(/\s+/g, " ").trim();
        const save = document.getElementById("messageSave"), clear = document.getElementById("messageClear");
        const dirtyEl = document.getElementById("messageDirty"), label = document.getElementById("messagePreviewLabel");
        let saved = norm(ta.dataset.saved || "");
        // #70 mocks 04-settings-v2 / 04b: "Save" + "Not saved yet" while editing, a disabled "Saved" and
        // "Remove message" once the text matches what the frame shows.
        const preview = ()=>{
            const v = norm(ta.value);
            const n = [...v].length;
            count.textContent = `${n} / ${max}`;
            count.classList.toggle("err", n > max);
            prevText.textContent = v;
            prev.hidden = !v; prevEmpty.hidden = !!v;
            const same = v === saved;
            save.disabled = same && !!saved;
            save.textContent = save.disabled ? "Saved" : "Save";
            dirtyEl.hidden = same;
            clear.hidden = !saved;
            label.textContent = same && saved ? "On the frame now" : "Preview: how it will look on the frame";
        };
        ta.addEventListener("input", preview);
        preview();
        async function saveMessage(value){
            try{
                const r = await fetch("/api/v1/message", {method: "PUT", credentials: "same-origin",
                    headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    body: JSON.stringify({message: value})});
                const j = await r.json().catch(()=> ({}));
                if(!r.ok){ throw new Error(j.error || String(r.status)); }
                ta.value = j.message; saved = norm(j.message); preview();
                status("message", j.message ? "✓ Saved" : "Removed");
            }catch(e){
                status("message", "Couldn't save. Try again.", true);
                if(e instanceof TypeError && window.memoryFramePhone){ memoryFramePhone.offline(); }
            }
        }
        save.addEventListener("click", ()=> saveMessage(ta.value));
        clear.addEventListener("click", ()=> saveMessage(""));
    }
})();
