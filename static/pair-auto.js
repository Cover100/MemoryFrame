// #161 auto-pair: the QR landing (live token) submits its own "Pair this phone" form once, so a scan needs
// no tap. Plain ES5 on purpose (Samsung Internet, older iPhone Safari): no arrow functions, classList,
// promises or fetch. The server never pairs on GET; this POST is the same one the button sends (CSRF,
// Origin, single use and the #153 device reuse all apply unchanged). Without JS the button is the page.
(function(){
    "use strict";
    var form = document.getElementById("tokenForm");
    var status = document.getElementById("pairStatus");
    if(!form || !status || typeof form.submit !== "function"){ return; }
    var HIDE = ["tokenButton", "orCode", "codeForm", "helpCard"];   // Wren state 1: hero, heading, status only
    var SLOW_MS = 4000;
    var submitted = false;

    function setHidden(on){
        for(var i = 0; i < HIDE.length; i++){
            var el = document.getElementById(HIDE[i]);
            if(el){ el.hidden = on; }
        }
    }
    function slow(){                                   // still here after ~4 s: everything back, tap to pair
        form.className = form.className.replace(/(^|\s)is-pairing(?=\s|$)/g, "");
        setHidden(false);
        status.textContent = "This is taking a while. Tap the button to pair.";
    }
    function viewed(){                                 // only a page the user is really looking at pairs
        if(document.prerendering === true){ return false; }
        return !document.visibilityState || document.visibilityState === "visible";
    }
    function go(){
        if(submitted || !viewed()){ return; }
        submitted = true;
        form.className += " is-pairing";
        status.textContent = "Pairing this phone\u2026";
        setHidden(true);
        setTimeout(slow, SLOW_MS);
        var raf = window.requestAnimationFrame || function(f){ return setTimeout(f, 16); };
        raf(function(){ setTimeout(function(){ form.submit(); }, 0); });   // after the first paint
    }
    document.addEventListener("visibilitychange", go, false);
    document.addEventListener("prerenderingchange", go, false);
    window.addEventListener("pageshow", function(e){
        if(e && e.persisted && submitted){ slow(); }   // back/forward cache: never submit again
    }, false);
    go();
})();
