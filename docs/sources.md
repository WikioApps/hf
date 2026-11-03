# Downloader research and attribution

The following repositories were reviewed for their download engines, public-link handling, folder traversal, progress or supported-site rules. The bot keeps its own Python task manager and Hugging Face upload implementation.

| Reference | What informed this update | What was not imported |
| --- | --- | --- |
| [siphon](https://github.com/mustafaberatdemirci/siphon) — MIT | MediaFire/Gofile/Pixeldrain public API behavior, guest sessions, MEGA file/folder keys and streaming integrity checks | Go/Wails desktop UI, browser extension and separate queue application |
| [md_downloader](https://github.com/NicKoehler/md_downloader) — MIT | MediaFire recursive folders, pagination and separation of metadata from download jobs | Rust runtime, browser impersonation and FlareSolverr service |
| [All-Url-Uploader](https://github.com/kalanakt/All-Url-Uploader) — MIT | Separation of direct downloads and yt-dlp media handling; bounded file streaming | Telegram uploads, per-user downloader credentials and unrelated bot features |
| [Mediafire-Downloader](https://github.com/miryu09/Mediafire-Downloader) — GPL-3.0 | MediaFire API paths, public folder/file distinctions and pagination behavior | GUI, web application and source code |
| [btch-downloader](https://github.com/hostinger-bot/btch-downloader) — MIT | Its catalogue helped identify public media sites to route through maintained extractors | Node.js SDK and its remote third-party downloader API; unsupported endpoints are not represented as working handlers |
| [Cyberdrop-DL](https://github.com/Cyberdrop-DL/cyberdrop-dl) — GPL-3.0 | Host capability/limitation documentation, public album layouts and content endpoints | Full crawler application, authentication/browser stack, database and source code |

MEGA's key layout, AES modes, chunk boundaries and integrity calculation were checked against siphon's documented implementation. Its MIT notice is retained in [licenses/siphon.txt](licenses/siphon.txt). The Python implementation uses `cryptography` and bounded chunks.

Provider wrappers use upstream yt-dlp/gallery-dl as installed dependencies. Those packages retain their own licenses; consult [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) when redistributing code or container images.
