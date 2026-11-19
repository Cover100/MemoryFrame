// #154 follow-up: the pair page (phone).
(function(){
    "use strict";
    // iPadOS Safari reports a Mac User-Agent; a touch screen tells them apart. Only a display-name hint:
    // the server accepts just "ipad" with a Macintosh UA, for the stored label, never for auth.
    if(/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1){
        document.querySelectorAll("input.devHint").forEach((i)=>{ i.value = "ipad"; });
    }
    // "This phone was removed" is shown once: drop ?removed=1 so a reload doesn't show it again (as MF-33).
    if(document.getElementById("removedNote") && window.history && history.replaceState){
        const q = new URLSearchParams(location.search);
        q.delete("removed");
        const rest = q.toString();
        history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash);
    }
})();
