// MF-14 phone upload page: one request per file, in order, with per-file progress.
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

    async function send(file, ui){
        if(file.size >= SHRINK_MIN_BYTES){
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
                const ok = xhr.status === 201 || xhr.status === 200;
                ui.bar.value = 100;
                ui.li.className = ok ? (xhr.status === 201 ? "ok" : "ok dup") : "bad";
                if(xhr.status === 201 && Number.isInteger(body.media_id)){
                    const img = document.createElement("img");
                    img.className = "th"; img.alt = ""; img.width = 52; img.height = 52; img.decoding = "async";
                    img.src = "/api/v1/media/" + body.media_id + "/thumb";
                    ui.th.replaceWith(img);
                }
                mfSetText(ui.msg, ok ? (xhr.status === 201 ? "Added" : "Already on the frame")
                                        : (body.error || ("Failed (" + xhr.status + ")")));
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
            ui.msg.textContent = "Uploading 0%";
            ui.li.className = "sending";
            xhr.send(form);
        });
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
        let done = 0, good = 0, added = 0, stopped = false;
        summary.textContent = "Sending " + files.length + " photo" + (files.length === 1 ? "" : "s") + "...";
        for(let i = 0; i < files.length; i++){
            const r = await send(files[i], rows[i]);
            if(r === "full"){                        // #96: stop the batch at the first refusal
                for(let j = i + 1; j < files.length; j++){ rows[j].li.className = "bad"; mfSetText(rows[j].msg, "Not sent"); }
                summary.textContent = added + " photo" + (added === 1 ? " was" : "s were") + " added; the frame is almost full.";
                stopped = true;
                break;
            }
            if(r){ good++; if(rows[i].li.className === "ok"){ added++; } }
            done++;
            summary.textContent = done + " of " + files.length + " done (" + good + " added or already there).";
        }
        if(!stopped){ summary.textContent += " They appear on the frame within a few seconds."; }
        input.value = "";
        input.disabled = false;
        drop.classList.remove("busy");
        drop.removeAttribute("aria-disabled");
        drop.classList.add("again");
        pickLabel.textContent = "Choose more photos";
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
