// MF-31 / MF-33: admin home (phone).
(function(){
    "use strict";
    const MSG = "Sign out all phones? Every paired phone, including this one, will need to pair again. Photos stay on the frame.";
    const form = document.getElementById("signOutForm");
    if(form){
        form.addEventListener("submit", (e)=>{ if(!window.confirm(MSG)){ e.preventDefault(); } });
    }
    // The "You're paired!" banner is shown once: drop ?paired=1 so a reload doesn't show it again.
    if(document.getElementById("pairedBanner") && window.history && history.replaceState){
        history.replaceState(null, "", location.pathname);
    }
    window.memoryFrameAdmin = {signOutMessage: MSG};
})();
