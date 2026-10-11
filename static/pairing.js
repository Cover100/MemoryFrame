// MF-12 + MF-19: on-device onboarding and long-press menu.
// - No photos and no phone paired: full onboarding (Wi-Fi join QR on the hotspot -> site QR + code).
// - Photos but no phone paired: the slideshow runs; only a small "Hold 3 s for menu" hint.
// - Paired but no photos: "No photos yet" screen.
// - Pairing is always available from the long-press menu ("Pair new device").
// The site URL/QR follow the frame's current network (hotspot 10.42.0.1 or home Wi-Fi .local + IP).
// - Still long-press >= 3 s: menu (IP, SSID, free storage, version, pair new device, sign out all).
// Only localhost routes are used (/pairing/*, /frame/status).
(function(){
    const PAIR_HOLD = 3000;
    const POLL = 2000;
    const MOVE_TOLERANCE = 20;
    const $ = (id)=>document.getElementById(id);

    const pairOverlay = $("pairOverlay"), emptyOverlay = $("emptyOverlay"), menuOverlay = $("menuOverlay");

    let onDemand = false;     // "Pair new device" chosen in the menu
    // Close dismisses the onboarding / empty-library screens for this browser session
    // (kiosk restarts and reboots start a new session). Pairing stays in the long-press menu.
    let dismissed = sessionStorage.getItem("mfOverlayDismissed") === "1";

    function dismiss(){
        dismissed = true;
        onDemand = false;
        sessionStorage.setItem("mfOverlayDismissed", "1");
        tick();
    }
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

    // MF-24: never show "null"/"undefined" as an address; fall back per network.
    function okUrl(u){
        return typeof u === "string" && /^https?:\/\/[^\/\s]+/.test(u)
            && !/^https?:\/\/(null|none|undefined|nan)?([:\/]|$)/i.test(u);
    }
    function fallbackBase(st){
        return st && st.network === "hotspot" ? "http://10.42.0.1/" : "http://memoryframe.local/";
    }

    function render(st){
        paired = st.paired;
        const base = fallbackBase(st);
        $("pairUrl").textContent = okUrl(st.url) ? st.url : base + "pair";
        $("pairUrlAlt").textContent = okUrl(st.url_fallback) ? "or " + st.url_fallback : "";
        $("emptyUrl").textContent = okUrl(st.site_url) ? st.site_url : base;
        $("pairSsid").textContent = st.ssid || "";
        const wifi = !!st.wifi_qr_svg;
        $("wifiStep").hidden = !wifi;
        if(wifi && typeof st.wifi_qr_svg === "string"){ $("wifiQr").innerHTML = st.wifi_qr_svg; }     // SVG from our own server
        $("siteTitle").textContent = wifi ? "2. Open site" : "Pair your phone";
        if(st.round && typeof st.round.qr_svg === "string" && typeof st.round.code === "string"){
            $("pairCode").textContent = st.round.code.slice(0, 3) + " " + st.round.code.slice(3);
            $("pairQr").innerHTML = st.round.qr_svg;
        }
        $("pairClose").hidden = false;              // always dismissable
    }

    // ---------- MF-36: network mode (dev banner, fallback toast, hotspot error) ----------
    const ERROR_SNOOZE = 10 * 60 * 1000;
    const TOAST_MS = 30 * 1000;
    let errorSnoozedUntil = 0;
    let toastTimer = null;

    function hideToast(){
        const t = $("devToast");
        if(t.hidden){ return; }
        t.classList.add("fading");
        setTimeout(()=>{ t.hidden = true; t.classList.remove("fading"); }, 600);
    }

    function renderNetmode(nm){
        nm = nm || {};
        const banner = $("devBanner");
        banner.hidden = !nm.dev;
        if(nm.dev){
            const ip = typeof nm.lan_ip === "string" && /^[0-9.]+$/.test(nm.lan_ip) ? nm.lan_ip : null;
            banner.textContent = "Developer mode \u00b7 " + (ip || "no network");
        }
        if(nm.dev_fallback){
            const key = "mf-devtoast-" + (nm.boot_id || "boot");
            let seen = false;
            try{ seen = localStorage.getItem(key) === "1"; }catch(e){}
            if(!seen){                                       // once per boot
                try{ localStorage.setItem(key, "1"); }catch(e){}
                $("devToast").hidden = false;
                clearTimeout(toastTimer);
                toastTimer = setTimeout(hideToast, TOAST_MS);
            }
        }
        $("hotspotError").hidden = !(nm.hotspot_error && Date.now() >= errorSnoozedUntil);
    }

    $("devToast").addEventListener("click", (e)=>{ e.stopPropagation(); hideToast(); });
    $("devToast").addEventListener("pointerdown", (e)=> e.stopPropagation());
    $("hotspotError").addEventListener("pointerdown", (e)=> e.stopPropagation());
    $("hotspotErrorClose").addEventListener("click", (e)=>{
        e.stopPropagation();
        errorSnoozedUntil = Date.now() + ERROR_SNOOZE;      // reappears while the failure persists
        $("hotspotError").hidden = true;
    });

    // ---------- MF-18: hotspot change ----------
    const CANCELLED_TIMEOUT = 60 * 1000;
    let wifiDeadline = 0, cancelledClosedId = null, cancelledShownAt = 0, cancelledId = null;
    function mmss(ms){
        const s = Math.max(0, Math.ceil(ms / 1000));
        return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    }
    function wifiRejoinActive(){ return !$("wifiRejoin").hidden; }
    function renderWifi(wc){
        const pending = !!(wc && wc.state === "pending");
        $("wifiRejoin").hidden = !pending;
        if(pending){
            $("wifiRejoinName").textContent = wc.new_ssid || "";
            if(typeof wc.qr_svg === "string"){ $("wifiRejoinQr").innerHTML = wc.qr_svg; }
            wifiDeadline = Date.now() + (Number(wc.remaining) || 0) * 1000;    // server deadline
            $("wifiCountdown").textContent = mmss(wifiDeadline - Date.now());
            closeMenu();
        }
        $("wifiUpdated").hidden = !(wc && wc.state === "committed");
        if(wc && wc.state === "committed"){ $("wifiUpdatedName").textContent = wc.new_ssid || ""; }
        const rolled = !!(wc && wc.state === "rolled_back" && wc.id !== cancelledClosedId);
        if(rolled && cancelledId !== wc.id){ cancelledId = wc.id; cancelledShownAt = Date.now(); }
        const showCancelled = rolled && Date.now() - cancelledShownAt < CANCELLED_TIMEOUT;
        $("wifiCancelled").hidden = !showCancelled;
        if(showCancelled){
            $("wifiCancelledName").textContent = wc.old_ssid || "";
            if(typeof wc.qr_svg === "string"){ $("wifiCancelledQr").innerHTML = wc.qr_svg; }
        }
    }
    setInterval(()=>{ if(wifiRejoinActive()){ $("wifiCountdown").textContent = mmss(wifiDeadline - Date.now()); } }, 250);
    function closeCancelled(){ cancelledClosedId = cancelledId; $("wifiCancelled").hidden = true; }
    for(const id of ["wifiRejoin", "wifiUpdated"]){
        $(id).addEventListener("pointerdown", (e)=> e.stopPropagation());     // not dismissable, no menu
        $(id).addEventListener("click", (e)=> e.stopPropagation());
    }
    $("wifiCancelled").addEventListener("pointerdown", (e)=> e.stopPropagation());
    $("wifiCancelled").addEventListener("click", (e)=>{
        e.stopPropagation();
        if(e.target.id === "wifiCancelled" || e.target.id === "wifiCancelledClose"){ closeCancelled(); }   // outside tap / ×
    });

    async function tick(){
        try{
            let st = await getJSON("/pairing/state");
            renderNetmode(st.netmode);
            renderWifi(st.wifi_change);
            const firstRun = st.paired === 0 && st.media === 0 && !dismissed;
            const pairing = firstRun || onDemand;
            if(pairing && !st.round){
                st = (await startRound()) || st;      // first round, expired or used
            }
            if(onDemand && st.paired > paired){
                onDemand = false;                    // the new phone just paired
            }
            render(st);
            const showPair = firstRun || onDemand;
            const showEmpty = !showPair && !dismissed && st.paired > 0 && st.media === 0;
            pairOverlay.classList.toggle("visible", showPair);
            emptyOverlay.classList.toggle("visible", showEmpty);
            // Small corner hint whenever pairing is possible but nothing covers the screen.
            $("pairHint").classList.toggle("visible", !showPair && !showEmpty && st.paired === 0);
        }catch(e){
            console.warn("pairing state unavailable", e);
        }
    }

    function gb(n){ return (n / 1073741824).toFixed(1) + " GB"; }

    async function openMenu(){
        if(wifiRejoinActive()){ return; }            // MF-18: the rejoin screen ignores the menu
        menuOverlay.classList.add("visible");
        try{
            const s = await getJSON("/frame/status");
            $("menuIp").textContent = (s.ips || []).length ? s.ips.join(", ") : "not connected";
            $("menuSsid").textContent = s.network === "hotspot" ? (s.ssid || "hotspot") + " (hotspot)"
                                                                : "home Wi-Fi";
            const site = okUrl(s.site) ? s.site : fallbackBase(s);
            $("menuSite").textContent = site.replace("http://", "") +
                (okUrl(s.site_fallback) ? "  or  " + s.site_fallback.replace("http://", "") : "");
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
    button("pairClose", dismiss);
    button("emptyClose", dismiss);

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

    window.memoryFramePairing = {tick: tick, openMenu: openMenu, snoozeMs: ERROR_SNOOZE, toastMs: TOAST_MS};
    tick();
    setInterval(tick, POLL);
})();
