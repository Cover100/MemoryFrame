// ======================================================
// Memory Frame Controller
// Raspberry Pi Touchscreen Edition
// ======================================================


// ======================================================
// Settings
// ======================================================

const IMAGE_DURATION = 8000;

const REFRESH_INTERVAL = 30000;

const FADE_TIME = 1500;

const SWIPE_THRESHOLD = 80;

const BRIGHTNESS_SPEED = 0.7;

const BRIGHTNESS_DISPLAY_TIME = 1500;

const MIN_BRIGHTNESS = 40;


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


// Gesture variables

let startX = 0;

let startY = 0;

let lastY = 0;

let brightnessDragging = false;


// Brightness

let brightness = 128;

let maxBrightness = 255;

let brightnessTimer = null;



// ======================================================
// Shuffle playlist
// Every item shown once before repeat
// ======================================================

function shuffle(array){


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


    loadBrightness();


    showCurrent();

}




// ======================================================
// Show current item
// ======================================================

function showCurrent(){


    if(!isPlaying){

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



    imageElement.onload =
        function(){


            applyKenBurns();



            setTimeout(()=>{


                imageElement.classList.add(
                    "visible"
                );


            },100);



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



    videoElement.src =
        "/uploads/"
        +
        item.filename;



    videoElement.onloadeddata =
        function(){


            videoElement.classList.add(
                "visible"
            );


            videoElement.play();


        };



    videoElement.onended =
        function(){


            nextMedia();


        };


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
// Next media
// ======================================================

function nextMedia(){


    clearTimeout(slideTimer);



    hideImage();

    hideVideo();



    setTimeout(()=>{


        currentIndex++;


        if(
            currentIndex >= playlist.length
        ){


            playlist =
                shuffle(playlist);


            currentIndex = 0;

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


    let effects = [

        "kenburns1",

        "kenburns2",

        "kenburns3"

    ];



    let effect =
        effects[
            Math.floor(
                Math.random()
                *
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
            await fetch("/media");


        let newMedia =
            await response.json();



        let oldFiles =
            playlist
            .map(
                x=>x.filename
            )
            .sort();



        let newFiles =
            newMedia
            .map(
                x=>x.filename
            )
            .sort();



        if(
            JSON.stringify(oldFiles)
            !==
            JSON.stringify(newFiles)
        ){


            playlist =
                shuffle(newMedia);


            currentIndex = 0;


            console.log(
                "Media updated"
            );

        }


    }
    catch(error){


        console.log(
            error
        );


    }


}





// ======================================================
// Preload next image
// ======================================================

function preloadNext(){


    let next =
        playlist[currentIndex + 1];



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



        /*
        Detect vertical brightness drag
        */

        if(
            Math.abs(totalY) > 20 &&
            Math.abs(totalY)
            >
            Math.abs(totalX)
        ){


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


        if(
            brightnessDragging
        ){

            brightnessDragging = false;

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


    }
);






// ======================================================
// Brightness control
// ======================================================

async function loadBrightness(){

    try{

        let response =
            await fetch(
                "/brightness"
            );

        let data =
            await response.json();

        brightness =
            data.brightness;

        maxBrightness =
            data.maximum;

        brightness =
            Math.max(
                MIN_BRIGHTNESS,
                Math.min(
                    brightness,
                    maxBrightness
                )
            );

        // Keep the Pi in sync if the loaded value
        // was below the minimum brightness.
        setBrightness();

    }
    catch(error){

        console.log(
            "Brightness unavailable"
        );

    }

}





function setBrightness(){

    brightness =
        Math.round(
            Math.max(
                MIN_BRIGHTNESS,
                Math.min(
                    brightness,
                    maxBrightness
                )
            )
        );

    fetch(
        "/brightness/"
        +
        brightness
    );

    showBrightness();

}





function showBrightness(){


    let percent =
        Math.round(
            brightness /
            maxBrightness *
            100
        );



    brightnessValue.innerText =
        percent
        +
        "%";



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

startSlideshow();