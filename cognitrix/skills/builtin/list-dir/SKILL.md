---
name: list-dir
description: List contents of a directory with details (files, folders, permissions)
context: fork
argument-hint: <directory_path>
tags: [directory, list, ls]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
safety:
  risk-level: low
---

# List Directory

List the contents of a directory with detailed information.

## Steps

1. Use `ls -la "$ARGUMENTS"` to list all files with details (permissions, size, date, name)
2. If no path provided, use current directory `.`
3. Handle ~ for home directory: expand to actual path or use directly with ls
4. If directory doesn't exist, report error

## Output Includes

- File permissions (drwxr-xr-x)
- Owner and group
- File size
- Last modified date
- File/folder name
- Symlinks

## Notes

- Use `ls -lh` for human-readable file sizes
- Use `ls -lt` to sort by modification time
- Use `ls -R` for recursive listing