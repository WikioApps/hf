import time

def size(value):
    if value is None:
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024:
            return f'{value:.2f}{unit}'
        value /= 1024
    return f'{value:.2f}PB'


def duration(value):
    if value is None:
        return '-'
    value = max(0, int(value))
    parts = []
    for label, span in [('d',86400), ('h',3600), ('m',60), ('s',1)]:
        count, value = divmod(value, span)
        if count:
            parts.append(f'{count}{label}')
    return ''.join(parts) or '0s'


def progress(done, total):
    if not total:
        return '[□□□□□□□□□□□□] —'
    percent = min(100, max(0, done * 100 / total))
    blocks = int(percent * 12 / 100)
    return '[' + '■' * blocks + '□' * (12-blocks) + f'] {percent:.2f}%'
