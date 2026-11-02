COMMANDS = {
    'start': ('Open the bot', 'Welcome. Use /mirror to upload a link or reply to a file. Configure your destination in /usettings.'),
    'help': ('Command help', 'Use /help command for details. Send /help mirror for upload options.'),
    'mirror': ('Upload a link or replied file', '/mirror URL\nOr reply to a file with /mirror.\n-n "name.ext": rename\n-p "folder": destination folder\n-e: extract archive\n-s: select archive members (with -e)\n-z: compress as ZIP\n-7z: compress as 7Z\n-pw "password": archive password\nOne URL per line, each with its own options.'),
    'usettings': ('Your Hugging Face settings', 'Set your HF token, owner or organization, repository, branch and folder. Fixed choices use buttons. This private menu applies to the owner too.'),
    'status': ('Live transfer progress', 'Shows active transfers in this chat. Use /status me for your tasks only. The message is removed when its view has no active tasks.'),
    'cancel': ('Stop a transfer', '/cancel ID stops an active task. Files are kept for retry. /cancel without an ID closes a settings prompt.'),
    'retry': ('Retry a retained task', '/retry ID reuses available files and skips confirmed archive uploads. On a new host it re-downloads the original source.'),
    'history': ('Recent uploads and failures', 'Your latest ten tasks, with completion links or retry buttons. The owner can view everyone’s records.'),
    'select': ('Select archive members', '/select ID opens the file selection menu for a task started with -e -s.'),
    'clean': ('Remove retained local files', '/clean ID removes local files for a finished task. Remote HF files and MongoDB history are kept.'),
    'stats': ('Storage and transfer overview', 'Shows CPU, memory, disk space and active download/upload counts.'),
    'speedtest': ('Measure network speed', 'Runs a real Speedtest.net download, upload and latency measurement. Sends the official result photo when available, with detailed server and transfer statistics. Falls back to the measured text result if sharing fails. One test at a time, with a five-minute cooldown after success.'),
    'logs': ('Owner: download bot logs', 'Owner only, private chat. /logs sends the current logs.txt with transfer stages, worker errors and service events. Credentials and source URLs are redacted. Logs start with this deployment; unrelated host/system logs are not collected.'),
    'web': ('Open your private web portal', 'Get a one-time sign-in link for the website. Set its HTTPS base URL in the owner’s /botsettings first. Do not share your personal sign-in link.'),
    'botsettings': ('Owner: access and limits', 'Owner only. Set public access, public website statistics, concurrency, file limits and the website URL.'),
    'users': ('Owner: manage user access', 'Owner only. View users and authorize or block them with buttons. Users keep their own HF destinations.'),
}
ALIASES = {'m':'mirror','upload':'mirror','u':'mirror','s':'status','us':'usettings','settings':'usettings','usetting':'usettings','log':'logs'}


def help_text(command=''):
    from html import escape
    if command in COMMANDS:
        return '<b>/'+command+'</b>\n\n'+escape(COMMANDS[command][1])
    return '<b>Commands</b>\n\n'+'\n'.join('/'+name+' — '+escape(info[0]) for name,info in COMMANDS.items())+'\n\nUse /help command for examples.'
