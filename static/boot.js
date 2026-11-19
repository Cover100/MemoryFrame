// #162 first-paint hand-off for the app page opened with ?boot=1 (see static/boot.css). Fades #bootSplash out on the
// FIRST of: the first photo shown (script.js calls mfBootDone), a video's first frame (script.js), the pairing or
// empty-library overlay becoming visible, or a 10 s failsafe (ink never stays over a working app). Then two
// animation frames so the page is really on screen, a 600 ms fade, and the node is removed after 700 ms.
(function(){
    "use strict";
    const el = document.getElementById("bootSplash");
    let done = false;
    window.mfBootDone = function(){};
    if(!el){ return; }
    const q = new URLSearchParams(location.search);
    // Continue the splash page's breath where it was (bp = ms into the 3600 ms cycle); CSSOM, so no inline style.
    const bp = Number(q.get("bp"));
    const dot = el.querySelector(".bsDot");
    if(dot && Number.isInteger(bp) && bp >= 0 && bp <= 3600){
        dot.style.animationDelay = (-bp) + "ms";
    }
    const fs = Number(q.get("bootfs"));                            // test override, 100 ms .. 10 s
    const FAILSAFE = Number.isFinite(fs) && fs >= 100 && fs <= 10000 ? fs : 10000;
    let observer = null;
    function finish(why){
        if(done){ return; }
        done = true;
        window.mfBootWhy = why;
        clearTimeout(failsafe);
        if(observer){ observer.disconnect(); }
        requestAnimationFrame(()=>requestAnimationFrame(()=>{
            el.classList.add("handoff");
            setTimeout(()=>{ el.remove(); window.mfBootGone = true; }, 700);
        }));
    }
    window.mfBootDone = finish;
    const failsafe = setTimeout(()=>finish("failsafe"), FAILSAFE);
    const watched = ["pairOverlay", "emptyOverlay"].map(id=>document.getElementById(id)).filter(Boolean);
    const shown = ()=>watched.find(o=>o.classList.contains("visible"));
    if(watched.length && window.MutationObserver){
        observer = new MutationObserver(()=>{ const o = shown(); if(o){ finish(o.id); } });
        watched.forEach(o=>observer.observe(o, {attributes: true, attributeFilter: ["class"]}));
    }
    const o = shown();
    if(o){ finish(o.id); }
})();
