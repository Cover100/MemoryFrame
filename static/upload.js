// MF-14 phone upload page: one request per file, in order, with per-file progress.

// #96/#102 (Wren): "N photos were added; ..." counted videos as photos. Counts each kind, singular/plural.
function mfAddedSummary(photos, videos){
    const part = (n, w)=> n + " " + w + (n === 1 ? "" : "s");
    const items = [];
    if(photos || !videos){ items.push(part(photos, "photo")); }
    if(videos){ items.push(part(videos, "video")); }
    return items.join(" and ") + (photos + videos === 1 ? " was" : " were") + " added; the frame is almost full.";
}

(function(){
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const input = document.getElementById("files");
    const list = document.getElementById("list");
    const summary = document.getElementById("summary");
    const storageEl = document.getElementById("storage");

    // #96: same text as the server's 507 (upload.FULL); at 0 photos left the picker is disabled.
    const FULL = "The frame is almost full. Delete some photos and try again.";
    let full = input.disabled;
    function setFull(on){
        full = on;
        input.disabled = on;
        const d = document.getElementById("drop");
        d.classList.toggle("busy", on);
        if(on){ d.setAttribute("aria-disabled", "true"); }else{ d.removeAttribute("aria-disabled"); }
    }

    function mb(n){ return (n / 1048576).toFixed(1) + " MB"; }

    function gb(n){ return (n / 1073741824).toFixed(1) + " GB"; }

    // #79: refresh the free-space meter after a batch, with the frame menu's numbers (free / total).
    async function loadStorage(){
        const meter = storageEl.querySelector(".track");
        if(!meter){ return; }
        try{
            const r = await fetch("/api/v1/storage", {cache: "no-store"});
            if(!r.ok){ throw new Error(r.status); }
            const s = await r.json();
            const used = s.total > 0 ? Math.min(1, Math.max(0, 1 - s.free / s.total)) : 0;
            const val = storageEl.querySelector(".storeFree"), of = document.createElement("span");
            val.textContent = gb(s.free) + " free ";
            of.textContent = "of " + gb(s.total);
            val.appendChild(of);
            meter.querySelector(".used").style.transform = "scaleX(" + used.toFixed(3) + ")";
            meter.setAttribute("aria-valuenow", String(Math.round(used * 100)));
            meter.setAttribute("aria-valuetext", gb(s.free) + " free of " + gb(s.total));
            const room = document.getElementById("storeRoom");
            if(room && Number.isInteger(s.photos_remaining_estimate)){
                room.textContent = s.photos_remaining_estimate === 0 ? FULL
                    : "Room for about " + s.photos_remaining_estimate.toLocaleString() + " more photos.";
                setFull(s.photos_remaining_estimate === 0);
            }
        }catch(e){ /* keep the last numbers */ }
    }

    // #82: shrink big photos here, before they cross the Wi-Fi, when the browser can decode them (JPEG,
    // WebP, PNG everywhere; HEIC only e.g. in Safari). Anything that fails returns null and the ORIGINAL
    // file is sent: the frame always shrinks again on its side (and enforces the megapixel cap).
    const STORE_W = Number(input.dataset.storeW) || 1500, STORE_H = Number(input.dataset.storeH) || 900;
    const SHRINK_MIN_BYTES = 3 * 1048576;       // smaller files are cheap for the frame to handle as they are
    const SHRINK_TIMEOUT_MS = 60000;

    function measure(url){                       // header only: does not decode the pixels
        return new Promise((resolve, reject)=>{
            const img = new Image();
            img.onload = ()=> resolve({w: img.naturalWidth, h: img.naturalHeight});   // already upright (EXIF applied)
            img.onerror = ()=> reject(new Error("undecodable"));
            img.src = url;
        });
    }

    async function shrinkNow(file){
        if(!window.createImageBitmap || !HTMLCanvasElement.prototype.toBlob){ return null; }
        if(file.size < SHRINK_MIN_BYTES || /gif/i.test(file.type) || /\.gif$/i.test(file.name)){ return null; }
        const url = URL.createObjectURL(file);
        let bmp = null;
        try{
            const d = await measure(url);
            if(!(d.w > 0 && d.h > 0)){ return null; }
            const k = Math.max(STORE_W / d.w, STORE_H / d.h);       // same "cover" rule as the frame
            if(k >= 0.9){ return null; }                              // not much bigger: send as is
            const w = Math.max(1, Math.round(d.w * k));
            // only the width is given: the browser keeps the aspect ratio (and applies the EXIF rotation)
            bmp = await createImageBitmap(file, {resizeWidth: w, resizeQuality: "high"});
            if(!bmp.width || !bmp.height || bmp.width * bmp.height > 16e6){ return null; }
            const c = document.createElement("canvas");
            c.width = bmp.width; c.height = bmp.height;
            const ctx = c.getContext("2d");
            if(!ctx){ return null; }
            ctx.fillStyle = "#000"; ctx.fillRect(0, 0, c.width, c.height);       // PNG/WebP transparency: black, as on the frame
            ctx.drawImage(bmp, 0, 0);
            const blob = await new Promise((res)=> c.toBlob(res, "image/jpeg", 0.9));
            c.width = c.height = 1;
            if(!blob || blob.size === 0 || blob.size >= file.size){ return null; }
            return new File([blob], file.name.replace(/\.[^.]*$/, "") + ".jpg", {type: "image/jpeg"});
        }catch(e){
            return null;                                              // can't decode here (e.g. HEIC): the frame does it
        }finally{
            if(bmp && bmp.close){ bmp.close(); }
            URL.revokeObjectURL(url);
        }
    }

    function shrink(file){
        let timer;
        const limit = new Promise((res)=>{ timer = setTimeout(()=> res(null), SHRINK_TIMEOUT_MS); });
        return Promise.race([shrinkNow(file), limit]).then((r)=>{ clearTimeout(timer); return r; });
    }

    function row(file){
        const li = document.createElement("li");
        const th = document.createElement("span");          // the frame's thumbnail replaces it once added
        th.className = "th";
        th.setAttribute("aria-hidden", "true");
        const t = document.createElement("div");
        t.className = "t";
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = file.name + " (" + mb(file.size) + ")";
        const bar = document.createElement("progress");
        bar.max = 100; bar.value = 0;
        const msg = document.createElement("span");
        msg.className = "msg";
        msg.textContent = "Waiting...";
        t.append(name, msg, bar);
        li.append(th, t);
        list.append(li);
        return {li, bar, msg, th, name};
    }

    async function send(file, ui, trim){
        if(file.size >= SHRINK_MIN_BYTES && !/^video\//i.test(file.type)){      // #102: videos go as they are
            ui.li.className = "sending";
            ui.msg.textContent = "Making it smaller...";
            const small = await shrink(file);
            if(small){
                ui.name.textContent = file.name + " (" + mb(file.size) + " \u2192 " + mb(small.size) + ")";
                file = small;
            }
        }
        return new Promise((resolve)=>{
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/v1/media");
            xhr.setRequestHeader("X-CSRF-Token", CSRF);
            xhr.upload.onprogress = (e)=>{
                if(e.lengthComputable){
                    ui.bar.value = Math.round(e.loaded * 100 / e.total);
                    ui.msg.textContent = "Uploading " + ui.bar.value + "%";
                }
            };
            xhr.onload = ()=>{
                let body = {};
                try{ body = JSON.parse(xhr.responseText); }catch(e){ /* not JSON */ }
                const queued = xhr.status === 202;                // #102: a video, prepared on the frame
                const ok = xhr.status === 201 || xhr.status === 200 || queued;
                ui.bar.value = 100;
                ui.li.className = ok ? (xhr.status === 200 ? "ok dup" : "ok") : "bad";
                if(xhr.status === 201 && Number.isInteger(body.media_id)){
                    const img = document.createElement("img");
                    img.className = "th"; img.alt = ""; img.width = 52; img.height = 52; img.decoding = "async";
                    img.src = "/api/v1/media/" + body.media_id + "/thumb";
                    ui.th.replaceWith(img);
                }
                mfSetText(ui.msg, ok ? (queued ? "Added. The frame is preparing it." : xhr.status === 201 ? "Added" : "Already on the frame")
                                        : (body.error || ("Failed (" + xhr.status + ")")));
                if(queued && Number.isInteger(body.prep_minutes)){        // #102: over 4K, slow on the Pi
                    const n = body.prep_minutes, note = document.createElement("span");
                    note.className = "slow";
                    note.textContent = "This video is very high resolution and may take about " + n + " minute" + (n === 1 ? "" : "s") + " to prepare.";
                    ui.msg.after(note);
                }
                ui.kind = body.kind === "video" ? "video" : "photo";
                if(queued && Number.isInteger(body.media_id)){ watch(ui, body.media_id); }   // #103
                if(xhr.status === 401){ location.href = "/pair"; }
                if(xhr.status === 507){ resolve("full"); return; }
                resolve(ok);
            };
            xhr.onerror = ()=>{
                ui.li.className = "bad";
                mfSetText(ui.msg, "Network error. Are you still on the frame's Wi-Fi?");
                if(window.memoryFramePhone){
                    memoryFramePhone.offline(file.name + " didn't finish uploading. It will need to be sent again.");
                }
                resolve(false);
            };
            const form = new FormData();
            form.append("file", file, file.name);
            if(trim){                                            // #103: the part picked on the trim screen
                form.append("trim_start_ms", String(trim.start));
                form.append("trim_end_ms", String(trim.end));
            }
            ui.msg.textContent = "Uploading 0%";
            ui.li.className = "sending";
            xhr.send(form);
        });
    }

    // ----- #103: after upload, follow the frame preparing the video (13c) -----
    // Polls /media/<id>/status every 2 s: % bar (99 at most until ready), queue position, Cancel, "Resumed after
    // restart", and Couldn't add + Retry / Remove. Screen readers hear 25/50/75 % and "ready" only.
    const live = document.getElementById("vlive");
    function say(t){ if(live){ live.textContent = t; } }
    function api(path, method){
        return fetch("/api/v1/media/" + path, {method: method || "GET", credentials: "same-origin", cache: "no-store",
                                               headers: method ? {"X-CSRF-Token": CSRF} : {}});
    }
    function button(text, cls){ const b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = text; return b; }
    function watch(ui, id){
        const name = ui.name.textContent.replace(/ \(.*$/, "");
        ui.bar.remove();
        const head = document.createElement("span"); head.className = "vhead";
        const pct = document.createElement("span"); pct.className = "pct";
        const bar = document.createElement("span"); bar.className = "vbar"; bar.setAttribute("role", "progressbar");
        bar.setAttribute("aria-valuemin", "0"); bar.setAttribute("aria-valuemax", "100");
        bar.setAttribute("aria-label", "Preparing " + name);
        const fill = document.createElement("span"); bar.append(fill);
        const resumed = document.createElement("span"); resumed.className = "slow"; resumed.textContent = "Resumed after restart";
        const acts = document.createElement("span"); acts.className = "vacts";
        ui.msg.replaceWith(head); head.append(ui.msg, pct);
        const note = ui.li.querySelector(".slow");
        let last = 0, timer = null, state = "";
        function show(s){
            state = s.state;
            const prep = s.state === "processing", wait = s.state === "queued";
            ui.li.classList.toggle("muted", s.state === "cancelling");
            bar.hidden = !prep; pct.textContent = prep ? s.progress + "%" : "";
            if(prep){
                fill.style.transform = "scaleX(" + (s.progress / 100) + ")";
                bar.setAttribute("aria-valuenow", String(s.progress)); bar.setAttribute("aria-valuetext", s.progress + "%");
                const m = mfMilestone(last, s.progress);
                if(m){ say(name + ": " + m + "% prepared."); }
                last = s.progress;
            }
            ui.msg.textContent = prep ? "Added. The frame is preparing it."
                : wait ? "Waiting to prepare · " + (s.queue_pos === 1 ? "next in line" : s.queue_pos + " in line")
                : s.state === "cancelling" ? "Cancelling…" : ui.msg.textContent;
            resumed.hidden = !s.resumed;
            acts.replaceChildren();
            if(prep || wait || s.state === "cancelling"){
                const c = button("Cancel", "vcancel");
                c.disabled = s.state === "cancelling";
                c.setAttribute("aria-label", "Cancel preparing " + name);
                c.addEventListener("click", cancel);
                acts.append(c);
            }
        }
        async function cancel(){
            show({state: "cancelling", progress: last});
            try{ await api(id + "/cancel", "POST"); }catch(e){ /* the next poll tells */ }
            tick();
        }
        async function tick(){
            clearTimeout(timer); timer = null;
            let s;
            try{
                const r = await api(id + "/status");
                if(r.status === 404){ ui.li.remove(); return; }            // cancelled: the row goes
                if(!r.ok){ throw new Error(r.status); }
                s = await r.json();
            }catch(e){ timer = setTimeout(tick, 2000); return; }       // offline for a moment: keep trying
            if(s.state === "ready"){
                acts.remove(); bar.remove(); resumed.remove(); pct.textContent = "";
                ui.li.className = "ok";
                ui.msg.textContent = "Added";
                const img = document.createElement("img");
                img.className = "th"; img.alt = ""; img.width = 52; img.height = 52; img.decoding = "async";
                img.src = "/api/v1/media/" + id + "/thumb";
                ui.th.replaceWith(img); ui.th = img;
                say(name + " is ready on the frame.");
                return;
            }
            if(s.state === "failed"){
                ui.li.className = "bad"; bar.hidden = true; pct.textContent = ""; resumed.hidden = true;
                ui.msg.textContent = "Couldn't add this video.";
                const r = button("Retry", "vcancel"), x = button("Remove", "vcancel vrm");
                r.addEventListener("click", async ()=>{
                    try{ await api(id + "/retry", "POST"); }catch(e){ /* next poll */ }
                    ui.li.className = "ok"; last = 0; tick();
                });
                x.addEventListener("click", async ()=>{
                    try{ const q = await api(String(id), "DELETE"); if(q.ok){ ui.li.remove(); return; } }catch(e){ /* stays */ }
                });
                acts.replaceChildren(r, x);
                return;
            }
            show(s);
            timer = setTimeout(tick, 2000);
        }
        head.after(bar);                       // name / status + % / bar / hi-res note / resumed / Cancel
        (note || bar).after(resumed);
        resumed.after(acts);
        show({state: "queued", progress: 0, queue_pos: 1});
        tick();
    }

    const drop = document.getElementById("drop");
    const pickLabel = document.getElementById("pickLabel");
    let busy = false;

    async function run(files){
        files = files.filter((f)=> f && f.size >= 0);
        if(busy || full || !files.length){ return; }
        busy = true;
        input.disabled = true;
        drop.classList.add("busy");
        drop.setAttribute("aria-disabled", "true");          // #79: looks and is disabled while sending
        pickLabel.textContent = "Uploading...";
        list.textContent = "";
        const rows = files.map(row);
        let done = 0, good = 0, added = 0, addedVideos = 0, stopped = false;
        const word = input.dataset.videos ? " file" : " photo";
        summary.textContent = "Sending " + files.length + word + (files.length === 1 ? "" : "s") + "...";
        for(let i = 0; i < files.length; i++){
            let trim = null;
            if(window.mfTrimPick && /^video\//i.test(files[i].type)){        // #103: over 30 s, pick the part first
                rows[i].msg.textContent = "Checking length...";
                const dur = await mfVideoDuration(files[i]);
                if(dur && dur > MF_TRIM_MAX){
                    trim = await mfTrimPick(files[i], dur);
                    if(!trim){ rows[i].li.className = "bad"; mfSetText(rows[i].msg, "Not added"); done++; continue; }
                    rows[i].name.textContent = files[i].name + " (" + mfMmss(trim.end - trim.start) + ")";
                }
            }
            const r = await send(files[i], rows[i], trim);
            if(r === "full"){                        // #96: stop the batch at the first refusal
                for(let j = i + 1; j < files.length; j++){ rows[j].li.className = "bad"; mfSetText(rows[j].msg, "Not sent"); }
                summary.textContent = mfAddedSummary(added - addedVideos, addedVideos);
                stopped = true;
                break;
            }
            if(r){ good++; if(rows[i].li.className === "ok"){ added++; if(rows[i].kind === "video"){ addedVideos++; } } }
            done++;
            summary.textContent = done + " of " + files.length + " done (" + good + " added or already there).";
        }
        if(!stopped){ summary.textContent += " They appear on the frame within a few seconds."; }
        input.value = "";
        input.disabled = false;
        drop.classList.remove("busy");
        drop.removeAttribute("aria-disabled");
        drop.classList.add("again");
        pickLabel.textContent = input.dataset.videos ? "Choose more" : "Choose more photos";
        busy = false;
        loadStorage();
    }

    input.addEventListener("change", ()=>{ run(Array.from(input.files)); });
    // Drop area (#79): photos dragged onto it go the same way as picked ones.
    drop.addEventListener("dragover", (e)=>{ e.preventDefault(); if(!busy){ drop.classList.add("over"); } });
    drop.addEventListener("dragleave", ()=>{ drop.classList.remove("over"); });
    drop.addEventListener("drop", (e)=>{
        e.preventDefault();
        drop.classList.remove("over");
        run(Array.from((e.dataTransfer && e.dataTransfer.files) || []));
    });
})();
