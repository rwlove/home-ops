#!/bin/bash

/bin/ffmpeg > -re -i rtsp://frigate.home:8554/birdcam -f lavfi -t 3600 -i anullsrc=r=44100:cl=stereo -vcodec libx264 -preset veryfast -maxrate 3000k -bufsize 6000k -keyint_min 25 -g 50 -acodec aac -ar 44100 -b:a 128k -f flv rtmp://a.rtmp.youtube.com/live2/${YOUTUBE_KEY}
