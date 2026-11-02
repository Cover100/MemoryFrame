// #126: home Library preview. A video that isn't ready shows the Photos tile (Preparing NN %, no ×) and is a link
// to Photos; it swaps to its thumbnail when the frame says it's ready, without a reload.
(function(){
    "use strict";
    const grid = document.getElementById("homeGrid");
    if(!grid || !window.mfVideoTile){ return; }
    const V = window.mfVideoTile;
    function apply(li, m){
        li.dataset.state = m.state;
        if(m.state !== "ready"){ li.replaceChildren(...V.stateTile(m, {href: "/admin/library"})); return; }
        const a = V.el("a"); a.href = "/admin/library";
        a.setAttribute("aria-label", "Video " + ([...grid.children].indexOf(li) + 1) + ", " + V.mmss(m.duration_ms));
        const img = new Image(240, 240); img.alt = ""; img.decoding = "async"; img.src = "/api/v1/media/" + m.id + "/thumb";
        a.append(img); li.replaceChildren(a);
    }
    V.watch(grid, apply);
})();
