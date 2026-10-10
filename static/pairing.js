// MF-12 + MF-19: on-device onboarding and long-press menu.
// - No phone paired: Wi-Fi join QR (D3, if enabled) -> site QR + 6-digit code.
// - Paired but no photos: "No photos yet" screen.
// - Still long-press >= 3 s: menu (IP, SSID, free storage, version, pair new device, sign out all).
// Only localhost routes are used (/pairing/*, /frame/status).
(function(){
    const PAIR_HOLD = 3000;
    const POLL = 2000;
    const MOVE_TOLERANCE = 20;
    const $ = (id)=>document.getElementById(id);

    const pairOverlay = $("pairOverlay"), emptyOverlay = $("emptyOverlay"), menuOverlay = $("menuOverlay");

    let onDemand = false;     // "Pair new device" chosen in the menu
    let paired = 0;
    let starting = false;
    let holdTimer = null;
    let sx = 0, sy = 0;

    async function getJSON(url, opts){
        const r = await fetch(url, Object.assign({cache: "no-store"}, opts || {}));
        if(!r.ok){ throw new Error(url + " " + r.status); }
        return r.json();
    }

    async function startRound(){
        if(starting){ return null; }
        starting = true;
        try{ return await getJSON("/pairing/start", {method: "POST"}); }
        catch(e){ return null; }
        finally{ starting = false; }
    }

    function render(st){
        paired = st.paired;
        $("pairUrl").textContent = st.url;
        $("emptyUrl").textContent = st.site_url;
        $("pairSsid").textContent = st.ssid || "";
        const wifi = !!st.wifi_qr_svg;
        $("wifiStep").hidden = !wifi;
        if(wifi){ $("wifiQr").innerHTML = st.wifi_qr_svg; }     // SVG from our own server
        $("siteTitle").textContent = wifi ? "2. Open site" : "Pair your phone";
        if(st.round){
            $("pairCode").textContent = st.round.code.slice(0, 3) + " " + st.round.code.slice(3);
            $("pairQr").innerHTML = st.round.qr_svg;
        }
        $("pairClose").hidden = paired === 0;
    }

    async function tick(){
        try{
            let st = await getJSON("/pairing/state");
            const pairing = st.paired === 0 || onDemand;
            if(pairing && !st.round){
                st = (await startRound()) || st;      // first round, expired or used
            }
            if(onDemand && st.paired > paired){
                onDemand = false;                    // the new phone just paired
            }
            render(st);
            const showPair = st.paired === 0 || onDemand;
            pairOverlay.classList.toggle("visible", showPair);
            emptyOverlay.classList.toggle("visible", !showPair && st.media === 0);
        }catch(e){
            console.warn("pairing state unavailable", e);
        }
    }

    function gb(n){ return (n / 1073741824).toFixed(1) + " GB"; }

    async function openMenu(){
        menuOverlay.classList.add("visible");
        try{
            const s = await getJSON("/frame/status");
            $("menuIp").textContent = s.ips.length ? s.ips.join(", ") : "not connected";
            $("menuSsid").textContent = s.ssid || "unknown";
            $("menuFree").textContent = gb(s.storage.free) + " of " + gb(s.storage.total);
            $("menuPaired").textContent = String(s.paired);
            $("menuVersion").textContent = s.version;
        }catch(e){
            console.warn("frame status unavailable", e);
        }
    }

    function closeMenu(){ menuOverlay.classList.remove("visible"); }

    function button(id, fn){
        $(id).addEventListener("click", (e)=>{ e.stopPropagation(); fn(); });
        $(id).addEventListener("pointerdown", (e)=> e.stopPropagation());
    }
    button("menuClose", closeMenu);
    button("menuPair", ()=>{ closeMenu(); onDemand = true; tick(); });
    button("menuSignOut", async ()=>{
        await fetch("/pairing/sign-out-all", {method: "POST"});
        closeMenu();
        tick();
    });
    button("pairClose", ()=>{ onDemand = false; tick(); });

    document.addEventListener("pointerdown", (e)=>{
        sx = e.clientX; sy = e.clientY;
        clearTimeout(holdTimer);
        holdTimer = setTimeout(openMenu, PAIR_HOLD);
    });
    document.addEventListener("pointermove", (e)=>{
        if(Math.abs(e.clientX - sx) > MOVE_TOLERANCE || Math.abs(e.clientY - sy) > MOVE_TOLERANCE){
            clearTimeout(holdTimer);
        }
    });
    document.addEventListener("pointerup", ()=> clearTimeout(holdTimer));
    document.addEventListener("pointercancel", ()=> clearTimeout(holdTimer));

    window.memoryFramePairing = {tick: tick, openMenu: openMenu};
    tick();
    setInterval(tick, POLL);
})();
