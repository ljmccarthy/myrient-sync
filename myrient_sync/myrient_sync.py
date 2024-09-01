import argparse
import collections
import datetime
import email.utils
import os.path
import progress.bar
import re
import requests
import sys
import time
import urllib.parse
from bs4 import BeautifulSoup
from dataclasses import dataclass
from enum import Enum
from typing import List

argparser = argparse.ArgumentParser()
argparser.add_argument('destdir', help='Destination directory')
argparser.add_argument('--exclude', action='append', help='Exclude pattern', default=[])
argparser.add_argument('--exclude-file', action='append', help='File containing list of exclude patterns', default=[])

base_url = 'https://myrient.erista.me/files'

@dataclass(frozen=True)
class DirEntry:
    name: str
    date: datetime.datetime

# Don't match paths with ..
valid_path_re = re.compile(r'^((?!\.\./)[^/\\]+/)*(?!\.\./)[^/\\]+/?$')

def list_dir(path) -> List[DirEntry]:
    request_url = base_url + urllib.parse.quote(path)
    response = requests.get(request_url)
    if response.status_code != 200:
        raise Exception(f'Failed to fetch {base_url}')
    soup = BeautifulSoup(response.text, 'html.parser')
    result = []
    for td_tag in soup.find_all('td', class_='link'):
        for a_tag in td_tag.find_all('a', recursive=False):
            if a_tag.has_attr('href'):
                name = urllib.parse.unquote(a_tag['href'])
                if valid_path_re.match(name):
                    date = email.utils.parsedate_to_datetime(response.headers['Date'])
                    result.append(DirEntry(name=name, date=date))
    return result

nothing_re = re.compile('$^')

def get_file_list(root_dir_path='/', exclude_re=nothing_re) -> List[str]:
    dir_queue = collections.deque([root_dir_path])
    dirs_seen = set()
    file_paths = []
    while dir_queue:
        dir_path = dir_queue.popleft()
        print(dir_path)
        for entry in list_dir(dir_path):
            sub_path = dir_path + entry.name
            if sub_path.endswith('/'):
                if sub_path not in dirs_seen and not exclude_re.match(sub_path):
                    dirs_seen.add(sub_path)
                    dir_queue.append(sub_path)
            elif not exclude_re.match(sub_path):
                file_paths.append(sub_path)
    file_paths.sort()
    return file_paths

def compile_exclude_patterns(patterns):
    if not patterns:
        return nothing_re    
    return re.compile('|'.join('^/' + re.escape(pattern).replace(r'\*', '[^/]*') + '(?:/.*)?$' for pattern in patterns))

exclude_file_ignore_line_re = re.compile(r'^\s*(?:#.*)?$')

def get_exclude_re(args):
    excludes = list(args.exclude)
    for exclude_file in args.exclude_file:
        with open(exclude_file) as f:
            for line in f:
                if not exclude_file_ignore_line_re.match(line):
                    excludes.append(line.rstrip())
    return compile_exclude_patterns(excludes)

def format_size(size):
    if size < 1024:
        return f'{size} B'
    elif size < 1024 * 1024:
        return f'{size//1024} KB'
    else:
        return f'{size//1024//1024} MB'

class DownloadStatus(Enum):
    Success = 1
    Skipped = 2
    Failed = 3

def download_file(src_file_path, dest_dir):
    dst_file_path = os.path.join(dest_dir, src_file_path.lstrip('/'))
    os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)
    headers = {}
    if os.path.exists(dst_file_path):
        modified_date = datetime.datetime.fromtimestamp(os.path.getmtime(dst_file_path))
        headers['If-Modified-Since'] = email.utils.format_datetime(modified_date)
    request_url = base_url + urllib.parse.quote(src_file_path)
    response = requests.get(request_url, headers=headers, stream=True)
    if response.status_code == 200:
        content_length = int(response.headers.get('Content-Length', 0))
        last_modified = email.utils.parsedate_to_datetime(response.headers['Last-Modified'])
        print(f'Downloading {src_file_path} ({format_size(content_length)})')
        tmp_file_path = dst_file_path + '.tmp'
        # Stream file data to temporary file
        with open(tmp_file_path, 'wb') as tmp_file:
            bytes_downloaded = 0
            with progress.bar.Bar(max=content_length, suffix='%(percent)d%%') as bar:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                    bytes_downloaded += len(chunk)
                    bar.goto(bytes_downloaded)
        # Clear progress bar from the terminal
        sys.stdout.write('\x1B[A\x1B[2K\r')
        sys.stdout.flush()
        # Set the modification time
        os.utime(tmp_file_path, (os.path.getatime(tmp_file_path), last_modified.timestamp()))
        # Rename the temporary file
        os.replace(tmp_file_path, dst_file_path)
        return DownloadStatus.Success
    elif response.status_code == 304:
        print(f'Skipping {src_file_path}')
        return DownloadStatus.Skipped
    else:
        return DownloadStatus.Failed

def download_file_with_retry(src_file_path, dest_dir, num_retries=3, retry_delay=2):
    for try_count in range(1, num_retries + 1):
        status = download_file(src_file_path, dest_dir)
        if status != DownloadStatus.Failed:
            return status
        print(f'Retrying {src_file_path} ({try_count} of {num_retries})')
        time.sleep(retry_delay)
    print(f'Failed to download {src_file_path}')
    return DownloadStatus.Failed

def main():
    try:
        args = argparser.parse_args()
        exclude_re = get_exclude_re(args)
        file_paths = get_file_list(exclude_re=exclude_re)
        download_count = 0
        skipped_count = 0
        failed_count = 0
        for file_path in file_paths:
            status = download_file_with_retry(file_path, args.destdir)
            if status == DownloadStatus.Success:
                download_count += 1
            elif status == DownloadStatus.Skipped:
                skipped_count += 1
            else:
                failed_count += 1
        print(f'Downloaded {download_count} files ({skipped_count} skipped, {failed_count} failed)')
        sys.exit(1 if failed_count > 0 else 0)
    except KeyboardInterrupt:
        print('Aborted')
        sys.exit(1)

if __name__ == '__main__':
    main()
