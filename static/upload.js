// MF-14 phone upload page: one request per file, in order, with per-file progress.
(function(){
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const input = document.getElementById("files");
    const list = document.getElementById("list");
    const summary = document.getElementById("summary");
    const storageEl = document.getElementById("storage");

    function mb(n){ return (n / 1048576).toFixed(0) + " MB"; }

    async function loadStorage(){
        try{
            const r = await fetch("/api/v1/storage", {cache: "no-store"});
            if(!r.ok){ throw new Error(r.status); }
            const s = await r.json();
            storageEl.textContent = "Free space: " + (s.free / 1073741824).toFixed(1) + " GB (room for about "
                + s.photos_remaining_estimate.toLocaleString() + " more photos). Used: "
                + (s.used / 1073741824).toFixed(1) + " GB.";
        }catch(e){
            storageEl.textContent = "Free space unknown.";
        }
    }

    function row(file){
        const li = document.createElement("li");
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = file.name + " (" + mb(file.size) + ")";
        const bar = document.createElement("progress");
        bar.max = 100; bar.value = 0;
        const msg = document.createElement("span");
        msg.className = "msg";
        msg.textContent = "Waiting...";
        li.append(name, bar, msg);
        list.append(li);
        return {li, bar, msg};
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
                ui.li.className = ok ? "ok" : "bad";
                ui.msg.textContent = ok ? (xhr.status === 201 ? "Added" : "Already on the frame")
                                        : (body.error || ("Failed (" + xhr.status + ")"));
                if(xhr.status === 401){ location.href = "/pair"; }
                resolve(ok);
            };
            xhr.onerror = ()=>{
                ui.li.className = "bad";
                ui.msg.textContent = "Network error. Are you still on the frame's Wi-Fi?";
                resolve(false);
            };
            const form = new FormData();
            form.append("file", file, file.name);
            ui.msg.textContent = "Uploading 0%";
            xhr.send(form);
        });
    }

    input.addEventListener("change", async ()=>{
        const files = Array.from(input.files);
        input.disabled = true;
        list.textContent = "";
        const rows = files.map(row);
        let done = 0, good = 0;
        for(let i = 0; i < files.length; i++){
            if(await send(files[i], rows[i])){ good++; }
            done++;
            summary.textContent = done + " of " + files.length + " done (" + good + " added or already there).";
        }
        summary.textContent += " They appear on the frame within a few seconds.";
        input.value = "";
        input.disabled = false;
        loadStorage();
    });

    loadStorage();
})();
