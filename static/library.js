// MF-16 library page: lazy thumbnail grid with Show now / Hide / Unhide / Delete.
(function(){
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const grid = document.getElementById("grid");
    const count = document.getElementById("count");

    function lost(e){                       // network failure (not an HTTP error): #70 mock 08
        if(e instanceof TypeError && window.memoryFramePhone){ memoryFramePhone.offline(); return true; }
        return false;
    }

    async function api(url, method, body){
        const r = await fetch(url, {method: method, headers: {"Content-Type": "application/json", "X-CSRF-Token": CSRF},
                                    body: body === undefined ? undefined : JSON.stringify(body)});
        if(r.status === 401){ location.href = "/pair"; }
        let data = {};
        try{ data = await r.json(); }catch(e){ /* no body */ }
        if(!r.ok){ throw new Error(data.error || ("Failed (" + r.status + ")")); }
        return data;
    }

    function updateCount(){
        const all = grid.children.length;
        const hidden = grid.querySelectorAll("li.hidden").length;
        count.textContent = all + " photos" + (hidden ? " (" + hidden + " hidden)" : "");
    }

    function card(item){
        const li = document.createElement("li");
        li.dataset.id = item.id;
        li.className = item.hidden ? "hidden" : "";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.decoding = "async";
        img.alt = item.name;
        img.width = 240; img.height = 240;
        img.src = item.thumb;
        const name = document.createElement("div");
        name.className = "name";
        name.textContent = item.name;
        const actions = document.createElement("div");
        actions.className = "actions";
        const show = document.createElement("button");
        show.textContent = "Show now";
        const hide = document.createElement("button");
        const del = document.createElement("button");
        del.textContent = "Delete";
        del.className = "del";                      // red text button on its own row (Wren #77 N4)
        function sync(){
            hide.textContent = li.classList.contains("hidden") ? "Unhide" : "Hide";
            show.disabled = li.classList.contains("hidden");
        }
        sync();
        show.addEventListener("click", async ()=>{
            try{ await api("/api/v1/media/" + item.id + "/show-now", "POST"); show.textContent = "Showing"; }
            catch(e){ if(!lost(e)){ alert(e.message); } }
            setTimeout(()=>{ show.textContent = "Show now"; }, 3000);
        });
        hide.addEventListener("click", async ()=>{
            const want = !li.classList.contains("hidden");
            try{
                await api("/api/v1/media/" + item.id + "/hidden", "PUT", {hidden: want});
                li.classList.toggle("hidden", want);
                sync(); updateCount();
            }catch(e){ if(!lost(e)){ alert(e.message); } }
        });
        del.addEventListener("click", async ()=>{
            if(!confirm("Delete \"" + item.name + "\" from the frame? This cannot be undone.")){ return; }
            try{
                await api("/api/v1/media/" + item.id, "DELETE");
                li.remove(); updateCount();
            }catch(e){ if(!lost(e)){ alert(e.message); } }
        });
        actions.append(show, hide, del);
        li.append(img, name, actions);
        return li;
    }

    async function load(){
        try{
            const r = await fetch("/api/v1/media", {cache: "no-store"});
            if(r.status === 401){ location.href = "/pair"; return; }
            const items = await r.json();
            const frag = document.createDocumentFragment();
            items.forEach(i => frag.append(card(i)));
            grid.textContent = "";
            grid.append(frag);
            updateCount();
        }catch(e){
            count.textContent = "Could not load the library.";
            lost(e);
        }
    }

    load();
})();
