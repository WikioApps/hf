"""Telegram's command menu and command-specific help share one catalogue."""
from html import escape

COMMANDS = {
    'start': ('Open the uploader', 'Set a destination with /usettings, then send a file or use /mirror URL. Forwarded files wait for your Upload button.'),
    'help': ('Commands and examples', 'Use /help mirror for transfer options, /help usettings for destinations, or /sites for supported public links.'),
    'mirror': ('Upload a link, collection or replied file', '''/mirror https://example.org/file.zip
Reply to a Telegram attachment with /mirror.

OPTIONS
-e or -unzip — extract an archive, then upload its members
-e -s — choose members before extraction (one archive)
-z — create a ZIP before uploading
-7z — create a 7Z before uploading
-n "new name.ext" — rename one input file
-p "folder/subfolder" — override your HF destination folder
-pw "password" — open an encrypted archive

EXAMPLES
/mirror https://example.org/archive.zip -e
/mirror https://example.org/archive.zip -e -s
/mirror https://example.org/file.bin -p "backups"

Put additional URLs on separate lines, with their own options. Public folders, galleries and playlists become one collection task. Rename and member selection apply to individual files. With -e on a collection, each input must be an archive.

Extraction overlaps concurrent uploads within your configured limits. Local files are deleted after HF confirmation and the result is saved. /status shows separate download, extract and upload progress. A Wait state protects disk or memory; it does not cancel other tasks.

Only public content is supported. You do not need to supply browser cookies. Hosts can still require login, restrict server IPs, expire links or enforce quotas.'''),
    'usettings': ('Your Hugging Face destination', '''Open this menu privately. Set HF token → owner/organization → repository → repository type → branch → upload folder. Your token needs write access to that destination.

Model/dataset, processing, visibility and filename collisions use buttons. Changes save to MongoDB; the owner uses the same personal settings. Enter a requested value as a new message: the bot removes that input and restores the settings menu.

Existing filenames: Rename keeps both; Overwrite replaces the same remote path; Skip leaves the existing HF file untouched. No task-ID folder is added.'''),
    'status': ('Live transfer progress', '''/status — active tasks visible to you in this chat
/status me — only your transfers

Task type stays visible: Upload, Extract → Upload, ZIP → Upload or 7Z → Upload. Each concurrent phase shows its actual bytes, recent speed and ETA when size is known. Hash/Prepare/Commit do not invent a transfer speed.

Refresh updates the view; arrows appear only when needed. The status message is removed when its visible transfers finish.'''),
    'cancel': ('Stop a transfer', '''/cancel ID
Use the /cancel_ID link on a status card. Stops only that task and its child processes. Confirmed HF uploads remain. Local download/extraction files are deleted after the workers stop; retry downloads the source again.

/cancel without an ID closes your current settings prompt.'''),
    'retry': ('Retry a failed or interrupted task', '''/retry ID
Find the ID in the failure message or /history. Reuses complete local files before their cleanup deadline and skips saved upload results. Failed files expire after 15 minutes by default; cancelled files are removed immediately. Public collections are resolved again so expired links can refresh.

Moving hosts preserves settings/history in MongoDB. Local bytes require persistent storage; otherwise the source must still be available for a new download.'''),
    'history': ('Recent transfers and results', 'Open privately to see your latest ten finished tasks, file links and Retry/Clean buttons. Clear finished history removes all your finished records and retained local files; the owner clears all users. Active transfers and HF uploads remain. History expires after seven days by default.'),
    'select': ('Choose archive members', '/select ID\nReopens selection for a single archive submitted with -e -s. Choose files, then confirm to start extraction. Selection expires after one hour.'),
    'clean': ('Remove retained local files', '/clean ID\nDeletes retained download/extraction files for a finished, failed or cancelled task. Cancel running tasks first. Hugging Face files and MongoDB history are preserved.'),
    'stats': ('CPU, memory, disk and transfer slots', 'Shows current CPU, memory, free disk, uptime and occupied download/upload slots. Set concurrency and disk reserve through the owner’s /botsettings. Unknown-length downloads use a small rolling reservation instead of claiming the whole disk. Available space subtracts the safety reserve and unwritten bytes promised to other tasks.'),
    'sites': ('Supported public links', 'Public file hosts, folders, video sites, galleries and playlists. Use /sites for the current list and documented limitations. No browser-cookie upload is required.'),
    'speedtest': ('Owner: measure network speed', 'Owner only, private chat. Runs a real Speedtest.net download/upload/latency measurement. Sends the official share image when available, otherwise the measured text result. One test at a time; five-minute cooldown after success. Provider failures are saved in /logs.'),
    'logs': ('Owner: download real bot logs', 'Owner only, private chat. /logs sends logs.txt with transfer phases, worker errors and service events. Tokens and source URLs are redacted. Unrelated host/system journals are not included.'),
    'web': ('Open your private web portal', 'Use /web privately for a single-use sign-in link valid for five minutes. The owner must first set the HTTPS Website URL in /botsettings. Never share your personal sign-in link.'),
    'botsettings': ('Owner: access, resources and limits', '''Owner only, private chat. Changes save to MongoDB and apply on every host.

Allow everyone — public access; off by default
Concurrent downloads/uploads — global slot limits, shared by tasks
Files prepared per task — bounds extraction/collection overlap
Keep disk free — space the bot reserves for the service
Keep failed files — 0, 15 or 60 minutes (default 15)
Keep transfer history — 1, 7 or 30 days (default 7)
Collection/archive/file limits — bound expansion and task size
Website URL/public statistics — portal origin and optional public counts

For a small host, start with two downloads, two uploads and two prepared files. More slots cannot exceed available disk or RAM.'''),
    'users': ('Owner: manage user access', 'Owner only, private chat. Authorize, block or reset users with buttons. With public access off, only the owner and authorized users can transfer. Every user configures a separate HF destination in /usettings.'),
}
ALIASES={'m':'mirror','upload':'mirror','u':'mirror','s':'status','us':'usettings','settings':'usettings','usetting':'usettings','log':'logs'}


def help_text(command=''):
    command=ALIASES.get(command,command)
    if command in COMMANDS:
        title,body=COMMANDS[command]
        return '<b>/'+command+' · '+escape(title)+'</b>\n\n'+escape(body)
    groups=[('Transfers',('mirror','status','cancel','retry','select','clean','history')),
            ('Your account',('usettings','sites','web','stats')),
            ('Owner tools',('botsettings','users','logs','speedtest'))]
    text='<b>Hugging Face Uploader</b>\nPublic links and Telegram files → your repository.\n'
    for title,names in groups:
        text+='\n<b>'+title+'</b>\n'+'\n'.join('/'+name+' — '+escape(COMMANDS[name][0]) for name in names)+'\n'
    return text+'\n<code>/help mirror</code> shows options and examples.'


def sites_text():
    return '''<b>Public-link support</b>

<b>Files &amp; folders</b>
MediaFire · Gofile · Pixeldrain files/lists · MEGA files/folders · Google Drive files/folders · Dropbox shared downloads · Yandex Disk · Internet Archive items · GitHub/GitLab/Bitbucket files · Hugging Face files · Buzzheavier · Krakenfiles · Catbox

<b>Video &amp; audio</b>
YouTube · Vimeo · Dailymotion · TikTok · Instagram · Facebook · X/Twitter · Reddit · Twitch · SoundCloud · Bandcamp · Bilibili · Douyin · Xiaohongshu · VK · Rutube · Streamable · Streamtape · Mixdrop · Redgifs · Giphy

<b>Images &amp; albums</b>
Cyberdrop · Bunkr · Imgur · Flickr · ImageBam · Imgbox · Pixhost · ImgBB · Lensdump · Pinterest · Tumblr · DeviantArt · Bluesky

Other public sites recognized by the installed yt-dlp/gallery-dl versions are tried automatically. Playlists and collections are bounded by the owner's limits. HLS/DASH can use the build-installed FFmpeg helper.

No supplied cookies are needed. Login-only, DRM-protected, paid, region/IP-blocked or browser-challenged content may be unavailable; automatic guest sessions cannot remove those restrictions.

<a href="https://github.com/WikioApps/hf/blob/main/docs/supported-sites.md">Support details and limitations</a>
/help mirror · /usettings'''
