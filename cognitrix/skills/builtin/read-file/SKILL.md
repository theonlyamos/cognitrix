---
name: read-file
description: Read the contents of a file with line numbers for easy reference
context: fork
argument-hint: <file_path>
tags: [file, read, view]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
---

# Read File

Read the file at "$ARGUMENTS" with appropriate tool based on use case.

## Use Cases

### Basic file read (small files <200 lines)
Use `cat -n "<filepath>"` to display with line numbers

### Large files - show first N lines
Use `head -n <N> "<filepath>"` to show first N lines

### Large files - show last N lines  
Use `tail -n <N> "<filepath>"` to show last N lines

### Read specific line number
Use `sed -n '<line>p' "<filepath>"` to read a specific line

### Read line range (e.g., lines 10-20)
Use `sed -n '10,20p' "<filepath>"`

### Search for pattern in file
Use `grep -n "pattern" "<filepath>"` to find lines containing pattern

### Search with context (show 3 lines before/after match)
Use `grep -n -C 3 "pattern" "<filepath>"`

### Recursive search in directory
Use `grep -rn "pattern" <directory>` to search all files in directory recursively

### Search specific file types only
Use `grep -rn --include="*.py" "pattern" <directory>` to search only .py files

### Search multiple patterns
Use `grep -rn -e "pattern1" -e "pattern2" <directory>` to search multiple patterns

### Exclude directories from search
Use `grep -rn --exclude-dir="node_modules" --exclude-dir=".git" "pattern" <directory>`

### Invert match (show lines NOT containing pattern)
Use `grep -rn -v "pattern" "<filepath>"`

### Count lines in file
Use `wc -l "<filepath>"` to count lines

### Find files matching pattern in directory
Use `ls "<filepath>"` or `glob` equivalent (on Windows: `dir /b` or `Get-ChildItem`)

## Notes

- Supports both absolute and relative paths
- Use `~` for home directory paths
- If file doesn't exist, report the error clearly
- Windows: use `type` instead of `cat`, `findstr` instead of `grep`