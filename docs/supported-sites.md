# Supported public links

No browser cookies, account passwords or extra downloader API keys are requested. Some hosts create an anonymous guest session automatically; that is different from using your browser account. Public availability still depends on the source host, IP, region, quotas and the installed extractor version.

Use `/mirror URL`. Add `-e` for archives, or send a folder, album or playlist URL to create a collection task. The owner sets the collection limit in `/botsettings` (1,000 files by default). Files retain their relative paths; internal task IDs are never added to Hugging Face paths.

## File hosts

Each adapter is in `bot/helper/mirror_leech_utils/download_utils/providers/`.

| Host | Inputs | Adapter | Limits |
| --- | --- | --- | --- |
| MediaFire | Public files, recursively listed folders | `mediafire.py` | Expiring file links refresh before download. Browser challenges can prevent access. |
| Gofile | Public `/d/` links, paginated folders and subfolders | `gofile.py` | Automatically obtains a guest token. Private/password-protected links, quotas and website API changes can prevent access. |
| Pixeldrain | `/u/` files, `/l/` lists, direct API file URLs | `pixeldrain.py` | Free-transfer limits and challenges still apply. |
| MEGA | Public keyed files/folders, selected nodes, legacy share links | `mega.py` | Requires the complete share link including its `#key`; no MEGA account needed. Streams AES-CTR decryption and verifies integrity before upload. Quota-limited transfers fail clearly; partial encrypted streams restart on retry. |
| Google Drive | Public files and folders | `google_drive.py` | Uses gdown without saved cookies. Google quotas and its folder-listing limitations apply; an incomplete listing is rejected rather than silently truncated. |
| Dropbox | Shared file downloads and downloadable shared folder ZIPs | `dropbox.py` | Preserves modern `rlkey` query parameters. The host must allow anonymous downloading. |
| Yandex Disk | Public files and recursively listed folders | `yandex_disk.py` | Anonymous public-resources API; blocked or withdrawn shares cannot be downloaded. |
| GitLab/Bitbucket | Public blob/raw files and direct archives | `gitlab.py`, `bitbucket.py` | Private repositories require authentication and are unavailable. |
| Internet Archive | Public item originals or direct file URLs | `internet_archive.py` | Item pages select original files, excluding service metadata, SQLite and torrent files. Restricted items are unavailable. |
| Hugging Face | Public `blob`/`resolve` file links | `huggingface.py` | Source downloads do not borrow your destination token; gated/private source files are unavailable. |
| GitHub | Public raw/blob files, release assets and archive downloads | `github.py` | A repository homepage is not a download request; use a file, asset or archive URL. |
| Buzzheavier | Public file pages | `buzzheavier.py` | Uses the page's download endpoint; a browser challenge may block it. |
| Krakenfiles | Public file pages | `krakenfiles.py` | Requires an accessible download form. |
| Catbox/Litterbox | Public direct files | `catbox.py` | Expired Litterbox files cannot be recovered. |
| Cyberdrop | Public file pages and no-JavaScript albums | `cyberdrop.py` | Host API or access-policy changes can interrupt availability. |
| Other HTTP(S) hosts | Direct files, including signed S3/R2/CDN URLs | `direct.py` | Signed URLs must remain valid. Redirect targets are checked. HTML pages are passed to a media extractor, not uploaded as fake downloads. |

## Video, audio, galleries and playlists

These adapters delegate extraction to pinned yt-dlp or gallery-dl versions. They do not implement a login or browser-cookie service. A listed site means there is a matching handler; it does not promise every URL or account on that site can be downloaded anonymously.

| Sites | Individual adapter files | Engine |
| --- | --- | --- |
| YouTube, Vimeo, Dailymotion | `youtube.py`, `vimeo.py`, `dailymotion.py` | yt-dlp |
| TikTok, Facebook, X/Twitter | `tiktok.py`, `facebook.py`, `twitter.py` | yt-dlp |
| Instagram, Reddit, Pinterest | `instagram.py`, `reddit.py`, `pinterest.py` | gallery-dl, with a yt-dlp fallback where available |
| Twitch, SoundCloud, Bandcamp | `twitch.py`, `soundcloud.py`, `bandcamp.py` | yt-dlp |
| Bilibili, Douyin, Xiaohongshu, VK, Rutube | `bilibili.py`, `douyin.py`, `xiaohongshu.py`, `vk.py`, `rutube.py` | yt-dlp |
| Streamable, Streamtape, Mixdrop, Redgifs, Giphy | `streamable.py`, `streamtape.py`, `mixdrop.py`, `redgifs.py`, `giphy.py` | yt-dlp |
| Bunkr, Imgur, Flickr | `bunkr.py`, `imgur.py`, `flickr.py` | gallery-dl |
| ImageBam, Imgbox, Pixhost, ImgBB, Lensdump | `imagebam.py`, `imgbox.py`, `pixhost.py`, `imgbb.py`, `lensdump.py` | gallery-dl |
| Tumblr, DeviantArt, Bluesky | `tumblr.py`, `deviantart.py`, `bluesky.py` | gallery-dl |

Other public sites recognized by the installed [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) and [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md) versions are tried through the shared fallback. There is no hard-coded claim that all of those sites work without authentication.

Video downloads can use native HLS/DASH downloading and FFmpeg merging. FFmpeg and FFprobe are installed during the normal build. The selected output can be MKV when audio and video need merging; the final filename follows the actual output extension. Live streams and very large playlists can exceed time, size or collection limits.

## What is deliberately unavailable

The bot does not bypass DRM, paid access, CAPTCHAs or login requirements. It does not send your URLs to the third-party downloader API used by btch-downloader. Spotify full-track audio and sites available only through that API are not advertised as supported. It does not run a headless browser, FlareSolverr, a torrent client or a remote desktop.

A public link can stop working when a provider changes its API. That is why each named host has a separate module. Updating an extractor version or a single adapter should not require changing transfer orchestration.

## Verification

Local checks exercise direct HTTP transfers, MediaFire/Pixeldrain/Dropbox/Gofile/folder parsing fixtures, MEGA key and integrity handling, collection bounds, and a real generated HLS stream. Public GitHub file and Internet Archive item resolution were checked live. Full live transfers from every provider were not performed; credentials and large copyrighted test downloads are not part of validation. See the README for the broader application checks.
