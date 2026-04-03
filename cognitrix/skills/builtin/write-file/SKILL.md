---
name: write-file
description: Write content to a file, creating it if it doesn't exist
context: same
args:
  - name: file_path
    description: Path to the file to write
    required: true
  - name: content
    description: Content to write to the file
    required: true
  - name: append
    description: If true, append to file instead of overwriting
    required: false
tags: [file, write, create]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [Write]
safety:
  risk-level: medium
---

# Write File

Write content to a file at `$(arg file_path)` using the Write tool.

## Usage

### Write new file
```
<filepath> content="Hello world"
```

### Write multi-line content
```
<filepath> content="Line 1\nLine 2\nLine 3"
```

### Append to existing file
```
<filepath> content="New content" append=true
```

## Notes

- Supports both absolute and relative paths
- Use `~` for home directory paths
- Parent directories are created automatically if they don't exist
- Set append=true to append instead of overwrite
