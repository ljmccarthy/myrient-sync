# myrient-sync

## Introduction

`myrient-sync` is a program to syncronise [Myrient](https://myrient.erista.me/) to a local directory. It supports exclude patterns so that only the required files will be downloaded.

## Installation

    $ python3 -m pip install .

## Usage

    myrient-sync <destdir> [--exclude <exclude-pattern>] [--exclude-file <exclude-file>]

## Exclude Patterns

Exclude patterns support simple glob patterns using the `*` character.

Exclude files contain exclude patterns on separate lines and may include blank lines or comment lines starting with the `#` character. See [excludes.txt](excludes.txt) for an example exclude file.
