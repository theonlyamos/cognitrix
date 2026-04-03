---
name: read-file
description: Read the contents of a file with line numbers for easy reference
context: same
args:
  - name: file_path
    description: Path to the file to read
    required: true
  - name: start_line
    description: Starting line number (1-based). Defaults to 1.
    required: false
  - name: end_line
    description: Ending line number (1-based). Leave empty to read to end.
    required: false
tags: [file, read, view]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [Read]
safety:
  risk-level: low
---

# Read File

Read the file at `$(arg file_path)` using the Read tool.

## Usage

### Basic file read
Use `Read` with the file path:
```
<filepath>
```

### Read with line range
To read lines 10-20:
```
<filepath> start_line=10 end_line=20
```

### Read first N lines
To read first 50 lines:
```
<filepath> end_line=50
```

### Read last N lines
First read entire file, then use end_line parameter.

## Notes

- Supports both absolute and relative paths
- Use `~` for home directory paths
- Line numbers are 1-based
- Set show_line_numbers=false to hide line numbers
