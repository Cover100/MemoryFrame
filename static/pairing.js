// MF-12 + MF-19 + MF-34: on-device onboarding and the menu (double tap).
// - No photos and no phone paired: full onboarding (Wi-Fi join QR on the hotspot -> site QR + code).
// - Photos but no phone paired: the slideshow runs; only a small "Double-tap for menu" hint.
// - Paired but no photos: "No photos yet" screen.
// - Pairing is always available from the menu ("Pair new device").
// The site URL/QR follow the frame's current network (hotspot 10.42.0.1 or home Wi-Fi .local + IP).
// - Double tap opens the menu (IP, SSID, free storage, version, pair new device, sign out all).
//   A tap: down->up < 250 ms, moved < 20 px. Double: 2nd down <= 300 ms after the 1st up, within
//   40 px. Primary pointer only; multi-touch resets. Ignored while an overlay is open.
//   Still long-press >= 3 s also opens it (kept as an unadvertised fallback).
// Only localhost routes are used (/pairing/*, /frame/status).
(function(){
    const PAIR_HOLD = 3000;
    const POLL = 2000;
    const MOVE_TOLERANCE = 20;
    const $ = (id)=>document.getElementById(id);

    const pairOverlay = $("pairOverlay"), emptyOverlay = $("emptyOverlay"), menuOverlay = $("menuOverlay");

    let onDemand = false;     // "Pair new device" chosen in the menu
    let onDemandRound = false;  // its one code has been started (no auto-renew)
    const SUCCESS_MS = 3000;
    let successUntil = 0;
    // Close dismisses the onboarding / empty-library screens for this browser session
    // (kiosk restarts and reboots start a new session). Pairing stays in the menu.
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

    async function tick(){
        try{
            let st = await getJSON("/pairing/state");
            renderNetmode(st.netmode);
            const firstRun = st.paired === 0 && st.media === 0 && !dismissed;
            const wasPairing = pairOverlay.classList.contains("visible") && Date.now() >= successUntil;
            if(onDemand && onDemandRound && !st.round){
                onDemand = false;                    // MF-32: on-demand code expired -> close, no renewal
            }
            const pairing = firstRun || onDemand;
            if(pairing && !st.round && (firstRun || !onDemandRound)){
                st = (await startRound()) || st;      // first-run renews; on demand: once
                if(onDemand && st.round){ onDemandRound = true; }
            }
            if(st.paired > paired && wasPairing){
                successUntil = Date.now() + SUCCESS_MS;   // MF-33: "Phone paired" for 3 s
                onDemand = false;
            }
            render(st);
            const success = Date.now() < successUntil;
            $("pairSuccess").hidden = !success;
            $("pairBox").hidden = success;
            if(success){ setTimeout(tick, Math.max(50, successUntil - Date.now() + 20)); }
            const showPair = success || (st.paired === 0 && st.media === 0 && !dismissed) || onDemand;
            const showEmpty = !showPair && !dismissed && st.paired > 0 && st.media === 0;
            pairOverlay.classList.toggle("visible", showPair);
            if(showEmpty && !emptyOverlay.classList.contains("visible")){ openedAt.set(emptyOverlay, Date.now()); }
            emptyOverlay.classList.toggle("visible", showEmpty);
            // Small corner hint whenever pairing is possible but nothing covers the screen.
            $("pairHint").classList.toggle("visible", !showPair && !showEmpty && st.paired === 0);
        }catch(e){
            console.warn("pairing state unavailable", e);
        }
    }

    function gb(n){ return (n / 1073741824).toFixed(1) + " GB"; }

    async function openMenu(){
        showOverlay(menuOverlay);
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
    button("menuPair", ()=>{ closeMenu(); onDemand = true; onDemandRound = false; tick(); });
    // ---------- MF-31: confirm "Sign out all phones" ----------
    const confirmOverlay = $("confirmOverlay");
    let confirmBusy = false;
    function openConfirm(){
        closeMenu();
        $("confirmAsk").hidden = false;
        $("confirmDone").hidden = true;
        showOverlay(confirmOverlay);
    }
    function closeConfirm(){ if(!confirmBusy){ confirmOverlay.classList.remove("visible"); } }
    button("menuSignOut", openConfirm);
    button("confirmCancel", closeConfirm);
    button("confirmSignOut", async ()=>{
        confirmBusy = true;
        try{ await fetch("/pairing/sign-out-all", {method: "POST"}); }catch(e){ console.warn(e); }
        $("confirmAsk").hidden = true;
        $("confirmDone").hidden = false;
        setTimeout(()=>{ confirmBusy = false; confirmOverlay.classList.remove("visible"); tick(); }, 2000);
    });

    // ---------- MF-32: outside tap and 60 s idle close (menu, confirm, empty library) ----------
    let IDLE_CLOSE = 60 * 1000;
    let lastTouch = Date.now();
    const openedAt = new Map();
    function showOverlay(o){ o.classList.add("visible"); openedAt.set(o, Date.now()); }
    const closers = new Map([[menuOverlay, closeMenu], [confirmOverlay, closeConfirm], [emptyOverlay, dismiss]]);
    document.addEventListener("pointerdown", ()=>{ lastTouch = Date.now(); }, true);
    for(const [o, close] of closers){
        let downOutside = false;
        o.addEventListener("pointerdown", (e)=>{ downOutside = e.target === o; });
        o.addEventListener("pointerup", (e)=>{
            if(downOutside && e.target === o){ e.stopPropagation(); close(); }
            downOutside = false;
        });
    }
    setInterval(()=>{
        for(const [o, close] of closers){
            if(!o.classList.contains("visible")){ continue; }
            const since = Math.max(lastTouch, openedAt.get(o) || 0);
            if(Date.now() - since >= IDLE_CLOSE){ close(); }
        }
    }, 1000);
    button("pairClose", dismiss);
    button("emptyClose", dismiss);

    // ---------- MF-34: double tap ----------
    const TAP_MAX_MS = 250, TAP_MOVE = 20, DOUBLE_GAP_MS = 300, DOUBLE_DIST = 40;
    let tapDown = null;        // {x, y, t} of the current press, if it can still be a tap
    let lastTap = null;        // {x, y, t (pointerup time)} of the previous completed tap
    let pointers = 0;
    function overlayOpen(){
        return [menuOverlay, pairOverlay, emptyOverlay, $("confirmOverlay")].some((o)=> o && o.classList.contains("visible"))
            || !!document.querySelector(".mf-modal:not([hidden])");
    }
    document.addEventListener("pointerdown", (e)=>{
        pointers += 1;
        if(!e.isPrimary || pointers > 1){ tapDown = null; lastTap = null; return; }   // multi-touch resets
        const now = performance.now();
        if(lastTap && (now - lastTap.t > DOUBLE_GAP_MS
                       || Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) > DOUBLE_DIST)){
            lastTap = null;
        }
        tapDown = {x: e.clientX, y: e.clientY, t: now};
    }, true);
    document.addEventListener("pointermove", (e)=>{
        if(tapDown && e.isPrimary && Math.hypot(e.clientX - tapDown.x, e.clientY - tapDown.y) >= TAP_MOVE){
            tapDown = null; lastTap = null;                  // a swipe or drag, not a tap
        }
    }, true);
    function pointerEnd(e, cancelled){
        pointers = Math.max(0, pointers - 1);
        const d = tapDown;
        tapDown = null;
        if(cancelled || !d || !e.isPrimary){ lastTap = null; return; }
        const now = performance.now();
        const isTap = now - d.t < TAP_MAX_MS && Math.hypot(e.clientX - d.x, e.clientY - d.y) < TAP_MOVE;
        if(!isTap){ lastTap = null; return; }
        if(lastTap){
            lastTap = null;
            if(!overlayOpen()){ openMenu(); }
            return;
        }
        lastTap = {x: e.clientX, y: e.clientY, t: now};
    }
    document.addEventListener("pointerup", (e)=> pointerEnd(e, false), true);
    document.addEventListener("pointercancel", (e)=> pointerEnd(e, true), true);

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

    window.memoryFramePairing = {tick: tick, openMenu: openMenu, snoozeMs: ERROR_SNOOZE, toastMs: TOAST_MS,
        idleCloseMs: ()=> IDLE_CLOSE, setIdleCloseForTests: (ms)=>{ IDLE_CLOSE = ms; }};
    tick();
    setInterval(tick, POLL);
})();
