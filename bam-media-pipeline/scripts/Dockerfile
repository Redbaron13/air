FROM node:20-bookworm-slim

# Install FFmpeg for video processing
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Node packages (Sharp requires specific binaries for Linux containers)
COPY package.json .
RUN npm install

ENV SHARP_IGNORE_GLOBAL_LIBVIPS=1