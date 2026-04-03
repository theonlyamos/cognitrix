---
name: grep
description: Search for text patterns in files
context: same
args:
  - name: pattern
    description: The text pattern to search for
    required: true
  - name: path
    description: Directory or file to search in
    required: false
  - name: include
    description: Glob pattern for files to include (e.g., "*.py")
    required: false
  - name: exclude
    description: Glob pattern for files to exclude (e.g., "*.log")
    required: false
  - name: context
    description: Number of lines to show before/after match
    required: false
  - name: ignore_case
    description: Case-insensitive search
    required: false
tags: [search, grep, find]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [Grep]
safety:
  risk-level: low
---

# Grep

Search for text patterns in files using the Grep tool.

## Usage

### Search all files
```
Grep pattern="function_name"
```

### Search Python files only
```
Grep pattern="TODO" include="*.py"
```

### Search with context (3 lines before/after)
```
Grep pattern="error" context=3
```

### Case-sensitive search
```
Grep pattern="Error" ignore_case=false
```

### Exclude directories
```
Grep pattern="TODO" exclude="node_modules"
```

## Notes

- Supports both absolute and relative paths
- Use `~` for home directory paths
- Set context > 0 to show surrounding lines
- Use exclude to skip directories like node_modules, .git
