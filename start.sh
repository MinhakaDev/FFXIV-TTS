#!/bin/bash

docker run -it   --network host   -e PULSE_SERVER=unix:/run/user/1000/pulse/native   -v /run/user/1000/pulse:/run/user/1000/pulse   -v $(pwd):/app   -v kokoro-cache:/root/.cache   ffxiv-tts