import argparse
import collections
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
from datetime import datetime, timezone
from enum import Enum
from typing import List

argparser = argparse.ArgumentParser()
argparser.add_argument('destdir', help='Destination directory')
argparser.add_argument('--include', action='append', help='Include pattern', default=[])
argparser.add_argument('--include-file', action='append', help='File containing list of include patterns', default=[])
argparser.add_argument('--exclude', action='append', help='Exclude pattern', default=[])
argparser.add_argument('--exclude-file', action='append', help='File containing list of exclude patterns', default=[])

base_url = 'https://myrient.erista.me/files'

everything_re = re.compile('.*')
nothing_re = re.compile('$^')
ignore_line_re = re.compile(r'^\s*(?:#.*)?$')

# Don't match paths with ..
valid_path_re = re.compile(r'^((?!\.\./)[^/\\]+/)*(?!\.\./)[^/\\]+/?$')

@dataclass
class FileEntry:
    name: str
    dirpath: str
    mtime: datetime

    @property
    def path(self) -> str:
        return self.dirpath + self.name

def list_dir(session: requests.Session, path: str) -> List[FileEntry]:
    request_url = base_url + urllib.parse.quote(path)
    response = session.get(request_url)
    if response.status_code != 200:
        raise Exception(f'Failed to fetch {base_url}')
    soup = BeautifulSoup(response.text, 'html.parser')
    result = []
    table_tag = soup.find('table', id='list')
    if not table_tag:
        return result
    for tr_tag in table_tag.find_all('tr'):
        link_td = tr_tag.find('td', class_='link', recursive=False)
        date_td = tr_tag.find('td', class_='date', recursive=False)
        if link_td and date_td:
            a_tag = link_td.find('a', recursive=False)
            if a_tag and a_tag.has_attr('href'):
                name = urllib.parse.unquote(a_tag['href'])
                if valid_path_re.match(name):
                    mtime_str = date_td.get_text(strip=True)
                    try:
                        mtime = datetime.strptime(mtime_str, '%d-%b-%Y %H:%M').replace(tzinfo=timezone.utc)
                    except ValueError:
                        print(f'Warning: Invalid date for {path}{name}: "{mtime_str}"')
                        mtime = datetime.now(tz=timezone.utc)
                    entry = FileEntry(name=name, dirpath=path, mtime=mtime)
                    result.append(entry)
    return result

def get_file_list(session: requests.Session, root_dir_path='/', include_re=everything_re, exclude_re=nothing_re) -> List[FileEntry]:
    dir_queue = collections.deque([root_dir_path])
    dirs_seen = set()
    file_entries = []
    while dir_queue:
        dir_path = dir_queue.popleft()
        print(f'Scanning {dir_path}')
        for file_entry in list_dir(session, dir_path):
            sub_path = dir_path + file_entry.name
            if sub_path.endswith('/'):
                if sub_path not in dirs_seen and include_re.match(sub_path) and not exclude_re.match(sub_path):
                    dirs_seen.add(sub_path)
                    dir_queue.append(sub_path)
            elif include_re.match(sub_path) and not exclude_re.match(sub_path):
                file_entries.append(file_entry)
    file_entries.sort(key=lambda fe: (fe.dirpath, fe.name))
    return file_entries

def format_size(size: int) -> str:
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

def download_file(session: requests.Session, src_file: FileEntry, dest_dir: str) -> DownloadStatus:
    src_file_path = src_file.path
    dst_file_path = os.path.join(dest_dir, src_file_path.lstrip('/'))
    os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)
    headers = {}
    if os.path.exists(dst_file_path):
        modified_date = datetime.fromtimestamp(os.path.getmtime(dst_file_path), tz=timezone.utc)
        if modified_date >= src_file.mtime:
            print(f'Skipping {src_file_path}')
            return DownloadStatus.Skipped
        headers['If-Modified-Since'] = email.utils.format_datetime(modified_date, usegmt=True)
    request_url = base_url + urllib.parse.quote(src_file_path)
    response = session.get(request_url, headers=headers, stream=True)
    if response.status_code == 200:
        content_length = int(response.headers.get('Content-Length', 0))
        last_modified = email.utils.parsedate_to_datetime(response.headers['Last-Modified'])
        print(f'Downloading {src_file_path} ({format_size(content_length)})')
        tmp_file_path = dst_file_path + '.tmp'
        # Stream file data to temporary file
        try:
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
        except Exception:
            try:
                os.remove(tmp_file_path)
            except OSError:
                pass
            raise
    elif response.status_code == 304:
        print(f'Skipping {src_file_path}')
        return DownloadStatus.Skipped
    else:
        return DownloadStatus.Failed

def download_file_with_retry(session, src_file_path, dest_dir, num_retries=3, retry_delay=0.5) -> DownloadStatus:
    for try_count in range(1, num_retries + 1):
        try:
            status = download_file(session, src_file_path, dest_dir)
            if status != DownloadStatus.Failed:
                return status
        except Exception as e:
            print(f'Error: {e}')
        time.sleep(retry_delay)
    print(f'Failed to download {src_file_path}')
    return DownloadStatus.Failed

def compile_include_patterns(patterns) -> re.Pattern:
    if not patterns:
        return everything_re
    parts = []
    for pattern in patterns:
        dir_parts = pattern.split('/')[:-1]
        for i in range(1, len(dir_parts) + 1):
            sub_dir = '/'.join(dir_parts[:i])
            parts.append('^/' + re.escape(sub_dir).replace(r'\*', '[^/]*') + '/$')
        parts.append('^/' + re.escape(pattern).replace(r'\*', '[^/]*') + '(?:/.*)?$')
    re_pattern = '|'.join(parts)
    return re.compile(re_pattern)

def compile_exclude_patterns(patterns) -> re.Pattern:
    if not patterns:
        return nothing_re
    parts = []
    for pattern in patterns:
        parts.append('^/' + re.escape(pattern).replace(r'\*', '[^/]*') + '(?:/.*)?$')
    re_pattern = '|'.join(parts)
    return re.compile(re_pattern)

def get_include_re(args) -> re.Pattern:
    includes = list(args.include)
    for include_file in args.include_file:
        with open(include_file) as f:
            for line in f:
                if not ignore_line_re.match(line):
                    includes.append(line.rstrip())
    return compile_include_patterns(includes)

def get_exclude_re(args) -> re.Pattern:
    excludes = list(args.exclude)
    for exclude_file in args.exclude_file:
        with open(exclude_file) as f:
            for line in f:
                if not ignore_line_re.match(line):
                    excludes.append(line.rstrip())
    return compile_exclude_patterns(excludes)

def main():
    try:
        args = argparser.parse_args()
        include_re = get_include_re(args)
        exclude_re = get_exclude_re(args)
        session = requests.Session()
        session.headers['Accept'] = '*/*'
        session.headers['Accept-Encoding'] = 'gzip, deflate'
        file_entries = get_file_list(session, include_re=include_re, exclude_re=exclude_re)
        download_count = 0
        skipped_count = 0
        failed_count = 0
        for file_entry in file_entries:
            status = download_file_with_retry(session, file_entry, args.destdir)
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
