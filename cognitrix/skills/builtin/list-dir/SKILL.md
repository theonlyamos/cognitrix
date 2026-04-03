---
name: list-dir
description: Find files matching a pattern in a directory
context: same
args:
  - name: pattern
    description: Glob pattern to match (e.g., "*.py", "*.txt")
    required: false
  - name: path
    description: Directory to search in
    required: false
  - name: recursive
    description: Search recursively
    required: false
tags: [directory, list, glob]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [Glob]
safety:
  risk-level: low
---

# List Directory

Find files matching a pattern using the Glob tool.

## Usage

### List all files in current directory
```
Glob pattern="*"
```

### List Python files
```
Glob pattern="*.py"
```

### List files in specific directory
```
Glob pattern="*.py" path="src"
```

### Recursive search (default)
```
Glob pattern="**/*.py" path="src"
```

### Include directories in results
```
Glob pattern="*" path="." recursive=true include_dirs=true
```

## Notes

- Supports both absolute and relative paths
- Use `~` for home directory paths
- Use `**` for recursive matching
- Use `include_dirs=true` to include directories in results
