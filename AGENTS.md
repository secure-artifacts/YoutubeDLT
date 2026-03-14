# AI Assistant Automation Guide

This page is designed for AI assistants.

Supported AI:

- Claude Code
- Cursor
- Google Antigravity
- ChatGPT

---

## Goal

Help the user use this repository.

This repository is a YouTube downloader tool based on yt-dlp.

The AI assistant should help the user:

1. Install dependencies
2. Configure the environment
3. Run the downloader
4. Troubleshoot errors if needed

---

## Setup Steps

### 1 Clone repository

git clone https://github.com/xvacts/YoutubeDLT.git

cd YoutubeDLT

### 2 Install requirements

Make sure the system has:

- Python 3.9+
- ffmpeg

Install yt-dlp:

pip install yt-dlp

---

### 3 Download video

Example command:

yt-dlp VIDEO_URL

Example:

yt-dlp https://www.youtube.com/watch?v=dQw4w9WgXcQ

---

### 4 Update yt-dlp

yt-dlp -U

---

## If errors occur

Ask the user for:

- Operating system
- Python version
- Error logs

Then help fix the issue.
