// #126: shared by Photos (library.js) and the home preview (home.js). A video that isn't ready yet is drawn the
// same way on both (templates/_video_tile.html renders the first paint; this rebuilds it per poll), and both ask
// the frame about those tiles every 2 s (/media/<id>/status). DOM only, no innerHTML, no inline styles (CSP).
(function(){
    "use strict";
    function el(tag, cls, text){ const e = document.createElement(tag); if(cls){ e.className = cls; } if(text){ e.textContent = text; } return e; }
    function mmss(ms){ const s = Math.round((ms || 0) / 1000); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
    function icon(d, w){                    // inline SVG (the font has no glyphs for these)
        const ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg"), p = document.createElementNS(ns, "path");
        s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", "22"); s.setAttribute("height", "22"); s.setAttribute("aria-hidden", "true");
        [["d", d], ["fill", "none"], ["stroke", "currentColor"], ["stroke-width", w], ["stroke-linecap", "round"], ["stroke-linejoin", "round"]]
            .forEach(([k, v])=> p.setAttribute(k, v));
        s.append(p); return s;
    }
    function ring(p){                       // 40 px progress ring; the arc is set per poll (no animation)
        const ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg");
        s.setAttribute("class", "ring"); s.setAttribute("viewBox", "0 0 40 40"); s.setAttribute("aria-hidden", "true");
        ["bg", "fg"].forEach((k)=>{
            const c = document.createElementNS(ns, "circle");
            [["class", k], ["cx", "20"], ["cy", "20"], ["r", "17"]].forEach(([a, v])=> c.setAttribute(a, v));
            if(k === "fg"){
                c.setAttribute("stroke-dasharray", "106.8"); c.setAttribute("transform", "rotate(-90 20 20)");
                c.setAttribute("stroke-dashoffset", (106.8 * (100 - p) / 100).toFixed(1));
            }
            s.append(c);
        });
        return s;
    }
    // A not-ready video's tile: failed ("Couldn't add") or waiting / preparing NN % / cancelling.
    // opts.actions (Photos): Retry / Remove, or the × cancel; opts.disabled greys them (select mode).
    // opts.href (home): no buttons, the tile is a link. Returns the nodes for the <li>.
    function stateTile(m, opts){
        const o = opts || {}, t = el(o.href ? "a" : "div", "tile vtile");
        if(o.href){ t.href = o.href; }
        if(m.state === "failed"){
            if(o.href){ t.setAttribute("aria-label", "Video couldn't be added"); }
            const st = el("div", "state fail"); st.append(el("span", "", "Couldn't add"));
            if(o.actions){
                const acts = el("div", "acts");
                const r = el("button", "vretry"), x = el("button", "vremove");
                r.type = x.type = "button"; r.dataset.id = x.dataset.id = m.id; r.disabled = x.disabled = !!o.disabled;
                r.setAttribute("aria-label", "Retry video"); x.setAttribute("aria-label", "Remove video");
                r.append(icon("M19 12a7 7 0 1 1-2.05-4.95M19 4v4h-4", "2.4")); x.append(icon("M6 6l12 12M18 6L6 18", "2.6"));
                acts.append(r, x); st.append(acts);
            }
            t.append(st);
            return [t];
        }
        const p = Math.max(0, Math.min(99, Number(m.progress) || 0)), c = m.state === "cancelling";
        if(!o.href){ t.setAttribute("role", "img"); }
        t.setAttribute("aria-label", c ? "Video cancelling" : m.state === "processing" ? "Video preparing, " + p + "%" : "Video waiting");
        const st = el("div", "state");
        if(m.state === "processing"){ st.append(ring(p)); }
        st.append(el("span", "lbl", c ? "Cancelling…" : m.state === "processing" ? "Preparing " + p + "%" : "Waiting"));
        if(m.resumed && !c){ st.append(el("span", "resumed", "Resumed after restart")); }
        t.append(st);
        if(!o.actions){ return [t]; }
        const x = el("button", "vx"); x.type = "button"; x.dataset.id = m.id; x.disabled = c || !!o.disabled;
        x.setAttribute("aria-label", "Cancel preparing video");
        x.append(icon("M6 6l12 12M18 6L6 18", "2.6"));
        return [t, x];
    }
    // 25 / 50 / 75 % once each (same rule as upload.js / trim.js)
    function milestone(prev, pct){ for(const m of [75, 50, 25]){ if(prev < m && pct >= m){ return m; } } return 0; }
    // While any <li data-state> under root is waiting, preparing or cancelling, poll those tiles every 2 s.
    // apply(li, m) redraws one tile; after() runs after each round. Returns poll(now) to restart it early.
    function watch(root, apply, after){
        let timer = null;
        const live = el("p", "sr-only"); live.setAttribute("aria-live", "polite"); document.body.append(live);
        function pending(){ return root.querySelectorAll('li[data-state="queued"], li[data-state="processing"], li[data-state="cancelling"]'); }
        async function check(li){
            const id = Number(li.dataset.id);
            const r = await fetch("/api/v1/media/" + id + "/status", {credentials: "same-origin", cache: "no-store"});
            if(r.status === 404){ li.remove(); return; }
            if(!r.ok){ return; }
            const m = await r.json(), was = Number(li.dataset.pct || 0);
            if(m.state === "processing"){
                const ms = milestone(was, m.progress);
                if(ms){ live.textContent = "Video " + ms + "% prepared."; }
                li.dataset.pct = m.progress;
            }
            if(m.state === "ready"){ live.textContent = "Video ready."; }
            if(li.dataset.state !== m.state || m.state === "processing"){ apply(li, m); }
        }
        function poll(now){
            if(now){ clearTimeout(timer); timer = null; }
            if(timer || !pending().length){ return; }
            timer = setTimeout(async ()=>{
                timer = null;
                try{ for(const li of pending()){ await check(li); } }catch(e){ /* offline: try again on the next tick */ }
                if(after){ after(); }
                poll();
            }, now ? 0 : 2000);
        }
        poll();
        return poll;
    }
    window.mfVideoTile = {el, mmss, icon, ring, stateTile, milestone, watch};
})();
