# myrient-sync

## Introduction

`myrient-sync` is a program to syncronise [Myrient](https://myrient.erista.me/) to a local directory. It supports include and exclude patterns so that only the required files will be downloaded.

## Installation

    $ pipx install .

## Usage

    myrient-sync <destdir>
        [--include <pattern>]
        [--include-file <include-file>]
        [--exclude <pattern>]
        [--exclude-file <exclude-file>]
        [--delete-unsynced]

## Include/exclude Patterns

Patterns support simple glob patterns using the `*` character.

Include/exclude files contain patterns on separate lines and may include blank lines or comment lines starting with the `#` character. See [includes.txt](includes.txt) and [excludes.txt](excludes.txt) for an example.
