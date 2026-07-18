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

const BRIGHTNESS_STEP = 20;

const BRIGHTNESS_DISPLAY_TIME = 1500;



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



// Swipe tracking

let startX = 0;

let startY = 0;

let endX = 0;

let endY = 0;



// Brightness

let brightness = 128;

let maxBrightness = 255;

let brightnessTimer = null;



// ======================================================
// Shuffle
// Ensures every item is seen once
// before reshuffling
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
                Math.random() * (i + 1)
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
// Playlist creation
// ======================================================

function createPlaylist(files){


    playlist =
        shuffle(files);


    currentIndex = 0;


}




// ======================================================
// Start
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
// Display current media
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
// Images
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
// Videos
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
// Hide layers
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
// Next
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
// Previous
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



    let selected =
        effects[
            Math.floor(
                Math.random()
                *
                effects.length
            )
        ];



    imageElement.classList.add(
        selected
    );


}





// ======================================================
// Auto refresh media
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


            console.log(
                "Media changed"
            );



            playlist =
                shuffle(newMedia);


            currentIndex = 0;


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



    if(
        !next
    ){

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
// Touch + Mouse gestures
// ======================================================

document.addEventListener(
    "pointerdown",
    function(event){


        startX =
            event.clientX;


        startY =
            event.clientY;


    }
);



document.addEventListener(
    "pointerup",
    function(event){


        endX =
            event.clientX;


        endY =
            event.clientY;



        handleGesture();


    }
);





function handleGesture(){


    let xDistance =
        endX - startX;


    let yDistance =
        endY - startY;



    // Vertical swipe

    if(
        Math.abs(yDistance)
        >
        Math.abs(xDistance)
    ){


        if(
            yDistance < -SWIPE_THRESHOLD
        ){


            increaseBrightness();


        }


        else if(
            yDistance > SWIPE_THRESHOLD
        ){


            decreaseBrightness();


        }


    }



    // Horizontal swipe

    else{


        if(
            xDistance < -SWIPE_THRESHOLD
        ){


            nextMedia();

        }


        else if(
            xDistance > SWIPE_THRESHOLD
        ){


            previousMedia();

        }


    }


}






// ======================================================
// Brightness
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



    }
    catch(error){

        console.log(
            "Brightness unavailable"
        );

    }


}





function increaseBrightness(){


    brightness +=
        BRIGHTNESS_STEP;



    brightness =
        Math.min(
            brightness,
            maxBrightness
        );



    setBrightness();


}




function decreaseBrightness(){


    brightness -=
        BRIGHTNESS_STEP;



    brightness =
        Math.max(
            brightness,
            0
        );



    setBrightness();


}





function setBrightness(){


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
            brightness
            /
            maxBrightness
            *
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
// Boot
// ======================================================

createPlaylist(mediaFiles);

startSlideshow();