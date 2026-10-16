// ======================================================
// Memory Frame Controller
// Raspberry Pi Touchscreen Edition
// ======================================================


// ======================================================
// Settings
// ======================================================

// MF-17: slide time, transition and shuffle come from the phone's Settings via /show-now.
let IMAGE_DURATION = 10000;

const REFRESH_INTERVAL = 10000; // MF-14: new uploads appear within ~10 s

let FADE_TIME = 800;

const FADE_TIME_DEFAULT = 800;

let SHUFFLE = true;

let libraryOrder = [];   // filenames in /media order (by name)

const SWIPE_THRESHOLD = 80;

const BRIGHTNESS_SPEED = 0.7;
const BRIGHTNESS_START = 12;   // px of vertical movement that starts a brightness drag (no hold)

const BRIGHTNESS_DISPLAY_TIME = 1500;

const MIN_BRIGHTNESS = 40;

// At most one backlight write per BRIGHTNESS_WRITE_INTERVAL ms (< 10/s, with margin for timer jitter).
// (Restored: lost in the MF-02/MF-03 merge 54a2e5b.)
const BRIGHTNESS_WRITE_INTERVAL = 110;

// Delay before skipping a file that failed to load (keeps a folder
// full of broken files from spinning the CPU).
const SKIP_DELAY = 1000;


// ======================================================
// Elements
// ======================================================

const imageElement =
    document.getElementById("image");


const videoElement =
    document.getElementById("video");


const brightnessOverlay =
    document.getElementById("brightnessOverlay");


const brightnessValue =
    document.getElementById("brightnessValue");


// ======================================================
// Variables
// ======================================================

let playlist = [];

let currentIndex = 0;

let isPlaying = false;

let slideTimer = null;

let pendingPlaylist = null;

let playlistUpdatePending = false;

// Watchdog: skips an item that never finishes loading/playing.
let loadWatchdog = null;

// Incremented every time a new item is shown, so late callbacks
// from a previous (e.g. broken) item are ignored.
let showToken = 0;


// Gesture variables

let startX = 0;

let startY = 0;

let lastY = 0;

let brightnessDragging = false;

// Hold-to-adjust brightness
let swipeAxis = null;          // "x" (swipe) or "y" (brightness) once decided for this touch
let brightnessMode = false;


// Brightness

let brightness = 128;

let maxBrightness = 255;

let brightnessTimer = null;

let brightnessActive = false;

// Set from GET /brightness; brightness gestures stay disabled until true.
let brightnessSupported = false;

let minBrightness = MIN_BRIGHTNESS;

let lastBrightnessWrite = 0;

let brightnessWriteTimer = null;

let lastSentBrightness = null;



// ======================================================
// Shuffle playlist
// Every item shown once before repeat
// ======================================================

function inLibraryOrder(array){
    const pos = new Map(libraryOrder.map((f, i)=> [f, i]));
    return [...array].sort((a, b)=>{
        const pa = pos.has(a.filename) ? pos.get(a.filename) : Infinity;
        const pb = pos.has(b.filename) ? pos.get(b.filename) : Infinity;
        return pa !== pb ? pa - pb : (a.filename < b.filename ? -1 : a.filename > b.filename ? 1 : 0);
    });
}

function shuffle(array){

    if(!SHUFFLE){
        return inLibraryOrder(array);    // MF-17: shuffle off -> library (name) order
    }


    let shuffled = [...array];


    for(
        let i = shuffled.length - 1;
        i > 0;
        i--
    ){


        let j =
            Math.floor(
                Math.random() *
                (i + 1)
            );


        [
            shuffled[i],
            shuffled[j]

        ] =
        [
            shuffled[j],
            shuffled[i]
        ];

    }


    return shuffled;

}




// ======================================================
// Create playlist
// ======================================================

function createPlaylist(files){

    libraryOrder = files.map((x)=> x.filename);


    playlist =
        shuffle(files);


    currentIndex = 0;

}





// ======================================================
// Start slideshow
// ======================================================

function startSlideshow(){


    if(
        playlist.length === 0
    ){

        console.log(
            "No media found"
        );

        return;

    }


    isPlaying = true;


    showCurrent();

}




// ======================================================
// Show current item
// ======================================================

function showCurrent(){


    if(!isPlaying){

        return;

    }


    if(playlist.length === 0){

        stopSlideshow();

        return;

    }



    if(
        currentIndex >= playlist.length
    ){


        playlist =
            shuffle(playlist);


        currentIndex = 0;

    }



    let item =
        playlist[currentIndex];


    showToken++;

    clearTimeout(loadWatchdog);



    console.log(
        "Showing:",
        item.filename
    );



    if(
        item.type === "image"
    ){

        showImage(item);

    }


    else if(
        item.type === "video"
    ){

        showVideo(item);

    }


}




// ======================================================
// Image display
// ======================================================

function showImage(item){


    clearTimeout(slideTimer);


    hideVideo();


    imageElement.className =
        "media";



    const token = showToken;


    imageElement.onerror =
        function(){

            if(token !== showToken) return;

            skipBroken(item, "image failed to load");

        };


    // Failsafe: if neither onload nor onerror fires in time, move on.
    loadWatchdog =
        setTimeout(()=>{

            if(token !== showToken) return;

            skipBroken(item, "image load timed out");

        },
        IMAGE_DURATION);


    imageElement.onload =
        function(){

            if(token !== showToken) return;

            clearTimeout(loadWatchdog);


            applyKenBurns();



            setTimeout(()=>{


                imageElement.classList.add(
                    "visible"
                );


            },100);


            imageShownAt = Date.now();

            slideTimer =
                setTimeout(()=>{


                    nextMedia();


                },
                IMAGE_DURATION);


        };



    imageElement.src =
        "/uploads/"
        +
        item.filename;


}





// ======================================================
// Video display
// ======================================================

function showVideo(item){


    clearTimeout(slideTimer);


    hideImage();



    videoElement.className =
        "media";



    const token = showToken;


    videoElement.onerror =
        function(){

            if(token !== showToken) return;

            skipBroken(item, "video failed to load or is unsupported");

        };


    // Failsafe: a corrupt/unsupported video may never fire
    // loadeddata or ended. Skip it within IMAGE_DURATION.
    loadWatchdog =
        setTimeout(()=>{

            if(token !== showToken) return;

            skipBroken(item, "video did not start in time");

        },
        IMAGE_DURATION);


    videoElement.onloadeddata =
        function(){

            if(token !== showToken) return;

            videoElement.classList.add(
                "visible"
            );


            let playing = videoElement.play();

            if(playing && playing.then){

                playing
                    .then(()=>{

                        if(token === showToken){

                            clearTimeout(loadWatchdog);

                        }

                    })
                    .catch((error)=>{

                        if(token !== showToken) return;

                        skipBroken(item, "video play() rejected: " + error);

                    });

            }
            else{

                clearTimeout(loadWatchdog);

            }

        };



    videoElement.onended =
        function(){

            if(token !== showToken) return;

            nextMedia();

        };


    videoElement.src =
        "/uploads/"
        +
        item.filename;


}





// ======================================================
// Hide media
// ======================================================

function hideImage(){


    imageElement.classList.remove(
        "visible"
    );

}



function hideVideo(){


    videoElement.classList.remove(
        "visible"
    );


    videoElement.pause();

}





// ======================================================
// Broken media handling
// ======================================================

function skipBroken(item, reason){

    clearTimeout(loadWatchdog);

    clearTimeout(slideTimer);

    console.warn(
        "Skipping broken media:",
        item.filename,
        "-",
        reason
    );

    slideTimer =
        setTimeout(
            nextMedia,
            SKIP_DELAY
        );

}



function stopSlideshow(){

    isPlaying = false;

    clearTimeout(slideTimer);

    clearTimeout(loadWatchdog);

    hideImage();

    hideVideo();

    console.log(
        "No media found - waiting for files"
    );

}




// ======================================================
// Next media
// ======================================================

function nextMedia(){

    clearTimeout(slideTimer);

    clearTimeout(loadWatchdog);

    showToken++;

    hideImage();

    hideVideo();

    setTimeout(()=>{

        if(
            playlistUpdatePending
        ){

            playlist =
                pendingPlaylist;

            pendingPlaylist =
                null;

            playlistUpdatePending =
                false;

            currentIndex =
                0;

        }
        else{

            currentIndex++;

            if(
                currentIndex >=
                playlist.length
            ){

                playlist =
                    shuffle(
                        playlist
                    );

                currentIndex =
                    0;

            }

        }

        showCurrent();

    },
    FADE_TIME);

}





// ======================================================
// Previous media
// ======================================================

function previousMedia(){


    clearTimeout(slideTimer);

    clearTimeout(loadWatchdog);

    showToken++;



    hideImage();

    hideVideo();



    setTimeout(()=>{


        currentIndex--;


        if(
            currentIndex < 0
        ){

            currentIndex =
                playlist.length - 1;

        }



        showCurrent();


    },
    FADE_TIME);



}





// ======================================================
// Ken Burns
// ======================================================

function applyKenBurns(){

    // Remove any previous Ken Burns classes
    imageElement.classList.remove(
        "kenburns1",
        "kenburns2",
        "kenburns3"
    );

    let effects = [

        "kenburns1",

        "kenburns2",

        "kenburns3"

    ];

    let effect =
        effects[
            Math.floor(
                Math.random() *
                effects.length
            )
        ];

    imageElement.classList.add(
        effect
    );

}





// ======================================================
// Refresh media folder
// ======================================================

async function refreshMedia(){

    try{

        let response =
            await fetch(
                "/media"
            );

        let newMedia =
            await response.json();

        libraryOrder = newMedia.map((x)=> x.filename);

        let oldFiles =
            playlist
                .map(
                    x => x.filename
                )
                .sort();

        let newFiles =
            newMedia
                .map(
                    x => x.filename
                )
                .sort();

        if(
            JSON.stringify(oldFiles)
            ===
            JSON.stringify(newFiles)
        ){

            return;

        }

        // Recover from an empty (or emptied) folder without a reload.
        if(!isPlaying){

            createPlaylist(newMedia);

            console.log(
                "Media found - starting slideshow"
            );

            startSlideshow();

            return;

        }


        pendingPlaylist =
            shuffle(newMedia);

        playlistUpdatePending =
            true;

        console.log(
            "Media update queued"
        );

        // MF-16: the photo on screen was hidden or deleted -> move on now.
        let current = playlist[currentIndex];
        if(current && !newFiles.includes(current.filename)){
            nextMedia();
        }

    }
    catch(error){

        console.log(
            "Refresh failed"
        );

    }

}





// ======================================================
// Preload next image
// ======================================================

function preloadNext(){


    if(playlist.length === 0){

        return;

    }


    // Wrap around at the end of the playlist.
    let next =
        playlist[(currentIndex + 1) % playlist.length];



    if(!next){

        return;

    }



    if(
        next.type === "image"
    ){


        let img =
            new Image();



        img.src =
            "/uploads/"
            +
            next.filename;


    }


}





// ======================================================
// Gesture handling
// Touchscreen + Mouse
// ======================================================


document.addEventListener(
    "pointerdown",
    function(event){

        startX =
            event.clientX;

        startY =
            event.clientY;

        lastY =
            event.clientY;

        brightnessDragging = false;
        brightnessMode = false;
        window.mfBrightnessDragging = false;
        swipeAxis = null;

    }
);




document.addEventListener(
    "pointermove",
    function(event){

        let deltaY =
            event.clientY -
            lastY;

        let totalY =
            event.clientY -
            startY;

        let totalX =
            event.clientX -
            startX;

        // Brightness starts as soon as the finger moves vertically past BRIGHTNESS_START px (no hold).
        // A mainly horizontal move first is a swipe and never changes brightness.
        if(!brightnessMode && swipeAxis === null){
            if(brightnessSupported && Math.abs(totalY) > BRIGHTNESS_START && Math.abs(totalY) > Math.abs(totalX)){
                swipeAxis = "y";
                brightnessMode = true;
                window.mfBrightnessDragging = true;     // cancels the 3 s still-hold (pairing.js)
                deltaY = event.clientY - startY;        // include the first 12 px
            }else if(Math.abs(totalX) > BRIGHTNESS_START){
                swipeAxis = "x";
            }
        }

        // Vertical drag: adjust brightness.
        if(brightnessMode){

            brightnessDragging = true;

            brightness -=
                deltaY *
                BRIGHTNESS_SPEED;

            brightness =
                Math.max(
                    MIN_BRIGHTNESS,
                    Math.min(
                        brightness,
                        maxBrightness
                    )
                );

            setBrightness();

            lastY =
                event.clientY;

        }

    }
);






document.addEventListener(
    "pointerup",
    function(event){

        window.mfBrightnessDragging = false;

        if(
            brightnessDragging
        ){

            // Always apply the final value of the drag.
            flushBrightness();

            brightnessDragging = false;
            brightnessMode = false;
            return;

        }

        let distance =
            event.clientX -
            startX;

        if(
            distance <
            -SWIPE_THRESHOLD
        ){

            nextMedia();

        }
        else if(
            distance >
            SWIPE_THRESHOLD
        ){

            previousMedia();

        }

        brightnessMode = false;

    }
);






// ======================================================
// Brightness control
// ======================================================

async function loadBrightness(){

    try{

        let response =
            await fetch("/brightness");

        let data =
            await response.json();

        if(
            !response.ok ||
            !data.supported ||
            !Number.isFinite(data.brightness) ||
            !Number.isFinite(data.maximum)
        ){

            brightnessSupported = false;

            console.log("Brightness control unsupported on this device");

            return;

        }

        maxBrightness = data.maximum;

        minBrightness =
            Number.isFinite(data.minimum)
                ? data.minimum
                : Math.min(MIN_BRIGHTNESS, maxBrightness);

        brightness = data.brightness;

        brightnessSupported = true;

        // Bring the Pi up to the minimum if it was set below it.
        if(brightness < minBrightness){

            setBrightness();

        }

    }
    catch(error){

        brightnessSupported = false;

        console.log("Brightness unavailable");

    }

}



function setBrightness(){

    if(!brightnessSupported){

        return;

    }

    brightness =
        Math.round(
            Math.max(
                minBrightness,
                Math.min(brightness, maxBrightness)
            )
        );

    showBrightness();

    // Throttle: send now if allowed, else schedule one trailing write
    // so the latest value is always applied.
    let wait =
        BRIGHTNESS_WRITE_INTERVAL -
        (Date.now() - lastBrightnessWrite);

    if(wait <= 0){

        sendBrightness();

    }
    else if(!brightnessWriteTimer){

        brightnessWriteTimer =
            setTimeout(sendBrightness, wait);

    }

}



function flushBrightness(){

    if(
        brightnessSupported &&
        brightness !== lastSentBrightness &&
        !brightnessWriteTimer
    ){

        let wait =
            BRIGHTNESS_WRITE_INTERVAL -
            (Date.now() - lastBrightnessWrite);

        brightnessWriteTimer =
            setTimeout(sendBrightness, Math.max(0, wait));

    }

}



function sendBrightness(){

    clearTimeout(brightnessWriteTimer);

    brightnessWriteTimer = null;

    if(
        !Number.isFinite(brightness) ||
        brightness === lastSentBrightness
    ){

        return;

    }

    lastBrightnessWrite = Date.now();

    lastSentBrightness = brightness;

    fetch(
        "/brightness",
        {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({value: brightness})
        }
    ).catch(()=>{

        console.log("Brightness write failed");

    });

}



function showBrightness(){

    let percent =
        Math.round(
            (
                (brightness - minBrightness) /
                Math.max(1, maxBrightness - minBrightness)
            ) * 100
        );

    percent = Math.max(
        0,
        Math.min(100, percent)
    );

    brightnessValue.innerText =
        percent + "%";

    brightnessOverlay.style.display =
        "flex";

    clearTimeout(
        brightnessTimer
    );

    brightnessTimer =
        setTimeout(()=>{

            brightnessOverlay.style.display =
                "none";

        },
        BRIGHTNESS_DISPLAY_TIME);

}





// ======================================================
// MF-16: "Show now" and fast library updates from the phone
// ======================================================

const CONTROL_INTERVAL = 2000;

let controlSeq = null;

let controlRev = null;

async function jumpTo(filename){

    if(playlistUpdatePending){
        playlist = pendingPlaylist;
        pendingPlaylist = null;
        playlistUpdatePending = false;
    }

    let idx = playlist.findIndex(x => x.filename === filename);

    if(idx < 0){
        // Not in the playlist yet (e.g. just uploaded): fetch the list now.
        try{
            let fresh = await (await fetch("/media")).json();
            let found = fresh.find(x => x.filename === filename);
            if(!found){ return; }
            playlist.splice(currentIndex + 1, 0, found);
            idx = currentIndex + 1;
        }catch(e){
            return;
        }
    }

    isPlaying = true;
    currentIndex = idx - 1;   // nextMedia() advances onto it
    nextMedia();

}

// ======================================================
// MF-17: settings applied live (no reload)
// ======================================================

let imageShownAt = 0;

function applySettings(st){

    if(!st || typeof st !== "object"){ return; }

    const secs = [5, 10, 30, 60, 300].includes(st.slide_seconds) ? st.slide_seconds : 10;
    const duration = secs * 1000;
    if(duration !== IMAGE_DURATION){
        IMAGE_DURATION = duration;
        // From the next slide: re-arm the running image timer, measured from now.
        let item = playlist[currentIndex];
        if(isPlaying && item && item.type === "image" && imageElement.classList.contains("visible")){
            clearTimeout(slideTimer);
            slideTimer = setTimeout(()=>{ nextMedia(); }, IMAGE_DURATION);
        }
    }

    const fade = st.transition !== "none";
    FADE_TIME = fade ? FADE_TIME_DEFAULT : 0;
    document.documentElement.classList.toggle("mf-no-fade", !fade);

    const shuffleOn = st.shuffle !== false;
    if(shuffleOn !== SHUFFLE){
        SHUFFLE = shuffleOn;
        let current = playlist[currentIndex];
        if(SHUFFLE){
            // reshuffle what's left; the photo on screen stays where it is
            let rest = playlist.slice(currentIndex + 1);
            SHUFFLE = true;
            playlist = playlist.slice(0, currentIndex + 1).concat(shuffle(rest));
        }else{
            playlist = inLibraryOrder(playlist);
            let i = current ? playlist.findIndex((x)=> x.filename === current.filename) : -1;
            currentIndex = i >= 0 ? i : 0;
        }
        if(playlistUpdatePending && pendingPlaylist){
            pendingPlaylist = shuffle(pendingPlaylist);
        }
    }

    window.memoryFrameSettings = {imageDuration: IMAGE_DURATION, fadeTime: FADE_TIME, shuffle: SHUFFLE,
                                  playlist: playlist.map((x)=> x.filename), index: currentIndex};
}

async function pollControl(){

    try{
        let c = await (await fetch("/show-now", {cache: "no-store"})).json();
        if(controlRev !== null && c.rev !== controlRev){
            refreshMedia();
        }
        controlRev = c.rev;
        applySettings(c.settings);
        if(controlSeq !== null && c.seq !== controlSeq && c.filename){
            console.log("Show now:", c.filename);
            jumpTo(c.filename);
        }
        controlSeq = c.seq;
    }catch(e){
        // the app is restarting; try again next time
    }

}

setInterval(pollControl, CONTROL_INTERVAL);

pollControl();



// ======================================================
// Timers
// ======================================================

setInterval(
    refreshMedia,
    REFRESH_INTERVAL
);



setInterval(
    preloadNext,
    5000
);




// ======================================================
// Start
// ======================================================

createPlaylist(mediaFiles);

// Brightness is independent of media, so load it even with an empty folder.
loadBrightness();

startSlideshow();