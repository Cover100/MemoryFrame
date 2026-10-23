// MF-14 phone upload page: one request per file, in order, with per-file progress.
(function(){
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const input = document.getElementById("files");
    const list = document.getElementById("list");
    const summary = document.getElementById("summary");
    const storageEl = document.getElementById("storage");

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
                room.textContent = "Room for about " + s.photos_remaining_estimate.toLocaleString() + " more photos.";
            }
        }catch(e){ /* keep the last numbers */ }
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
        return {li, bar, msg, th};
    }

    function send(file, ui){
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
        if(busy || !files.length){ return; }
        busy = true;
        input.disabled = true;
        drop.classList.add("busy");
        drop.setAttribute("aria-disabled", "true");          // #79: looks and is disabled while sending
        pickLabel.textContent = "Uploading...";
        list.textContent = "";
        const rows = files.map(row);
        let done = 0, good = 0;
        summary.textContent = "Sending " + files.length + " photo" + (files.length === 1 ? "" : "s") + "...";
        for(let i = 0; i < files.length; i++){
            if(await send(files[i], rows[i])){ good++; }
            done++;
            summary.textContent = done + " of " + files.length + " done (" + good + " added or already there).";
        }
        summary.textContent += " They appear on the frame within a few seconds.";
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
