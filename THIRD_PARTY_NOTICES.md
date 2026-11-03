# Third-party components

This project installs dependencies rather than copying complete downloader applications into its source tree. Package metadata and bundled license files remain in the virtual environment.

- **siphon**: MIT, Copyright (c) 2026 Mustafa Berat Demirci. MEGA protocol implementation reference and public-host research. Full notice: `docs/licenses/siphon.txt`. Source: https://github.com/mustafaberatdemirci/siphon
- **gallery-dl 1.32.19**: GPL-2.0-only. Installed extractor dependency. Source and license: https://github.com/mikf/gallery-dl
- **yt-dlp 2026.10.26**: Unlicense for the main project; see upstream notices for included components. Source: https://github.com/yt-dlp/yt-dlp
- **aria2**: GPL-2.0, installed through `aria2==0.0.1b0`. Wheel/build sources: https://github.com/WSH032/aria2-wheel/ ; aria2 sources: https://github.com/aria2/aria2
- **7-Zip 26.05**: upstream mixed LGPL/BSD/unRAR licensing. The installer retains included license texts beside the executable. Source: https://github.com/ip7z/7zip
- **imageio-ffmpeg 0.8.0**: BSD-2-Clause Python wrapper; its bundled FFmpeg binary has its own upstream licensing. Sources/build instructions: https://github.com/imageio/imageio-ffmpeg
- **FFprobe 9.0.2 (BtbN build `autobuild-2026-11-02-12-51`)**: LGPL build, with component-specific notices provided by the build. Download archives are verified against pinned SHA-256 values; only FFprobe and license files are extracted. Source/build configuration: https://github.com/BtbN/FFmpeg-Builds ; upstream FFmpeg: https://ffmpeg.org/download.html

Other dependency licenses are included in their installed distribution metadata. These notices do not relicense upstream components. When distributing an image containing GPL/LGPL components, preserve the relevant notices and satisfy the corresponding source-distribution requirements; a link in this file is not a substitute for those obligations.
