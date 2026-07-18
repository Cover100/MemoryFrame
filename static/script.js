// ======================================================
// Memory Frame Slideshow Controller
// ======================================================


// =====================
// Settings
// =====================

const IMAGE_DURATION = 8000;

const REFRESH_INTERVAL = 30000;

const FADE_TIME = 1500;

const SWIPE_THRESHOLD = 80;


// =====================
// Elements
// =====================

const imageElement =
    document.getElementById("image");


const videoElement =
    document.getElementById("video");



// =====================
// Variables
// =====================

let playlist = [];

let currentIndex = 0;

let isPlaying = false;

let slideTimer = null;


// Pointer tracking
// Works with:
// - Raspberry Pi touchscreen
// - Mouse
// - Stylus

let startX = 0;

let endX = 0;



// =====================
// Shuffle
// =====================

function shuffle(array) {


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



// =====================
// Create playlist
// =====================

function createPlaylist(files){


    playlist =
        shuffle(files);


    currentIndex = 0;

}



// =====================
// Start slideshow
// =====================

function startSlideshow(){


    if(playlist.length === 0){


        console.log(
            "No media found"
        );


        return;

    }



    isPlaying = true;


    showCurrent();

}



// =====================
// Show current media
// =====================

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



    if(item.type === "image"){


        showImage(item);


    }


    else if(item.type === "video"){


        showVideo(item);

    }

}



// =====================
// Show image
// =====================

function showImage(item){


    clearTimeout(slideTimer);



    hideVideo();



    imageElement.className =
        "media";



    imageElement.onload = function(){



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



// =====================
// Show video
// =====================

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



// =====================
// Hide elements
// =====================

function hideImage(){


    imageElement.classList.remove(
        "visible"
    );

}



function hideVideo(){


    videoElement.classList.remove(
        "visible"
    );


}



// =====================
// Next media
// =====================

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



// =====================
// Previous media
// =====================

function previousMedia(){


    clearTimeout(slideTimer);



    hideImage();

    hideVideo();



    setTimeout(()=>{


        currentIndex--;



        if(currentIndex < 0){


            currentIndex =
                playlist.length - 1;

        }



        showCurrent();



    },
    FADE_TIME);



}



// =====================
// Ken Burns
// =====================

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



// =====================
// Auto refresh uploads
// =====================

async function refreshMedia(){


    try{


        let response =
            await fetch("/media");



        let newMedia =
            await response.json();



        let current =
            playlist
            .map(
                x=>x.filename
            )
            .sort();



        let updated =
            newMedia
            .map(
                x=>x.filename
            )
            .sort();




        if(
            JSON.stringify(current)
            !==
            JSON.stringify(updated)
        ){


            console.log(
                "Media updated"
            );



            playlist =
                shuffle(newMedia);



            currentIndex = 0;


        }



    }
    catch(error){


        console.log(
            "Refresh error:",
            error
        );


    }


}



// =====================
// Preload next image
// =====================

function preloadNext(){


    let next =
        playlist[currentIndex + 1];



    if(!next){

        return;

    }



    if(next.type === "image"){


        let img =
            new Image();



        img.src =
            "/uploads/"
            +
            next.filename;


    }


}



// =====================
// Swipe Controls
// Mouse + Touch
// =====================


document.addEventListener(
    "pointerdown",
    function(event){


        startX =
            event.clientX;


    }
);





document.addEventListener(
    "pointerup",
    function(event){


        endX =
            event.clientX;



        handleSwipe();


    }
);





function handleSwipe(){


    let distance =
        endX - startX;



    if(
        distance < -SWIPE_THRESHOLD
    ){


        console.log(
            "Swipe left"
        );


        nextMedia();


    }



    else if(
        distance > SWIPE_THRESHOLD
    ){


        console.log(
            "Swipe right"
        );


        previousMedia();


    }



}




// =====================
// Timers
// =====================


setInterval(
    refreshMedia,
    REFRESH_INTERVAL
);



setInterval(
    preloadNext,
    5000
);




// =====================
// Boot
// =====================


createPlaylist(mediaFiles);


startSlideshow();