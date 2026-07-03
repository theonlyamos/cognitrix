---
name: edit-file
description: Edit an existing file by replacing text
context: same
args:
  - name: file_path
    description: Path to the file to edit
    required: true
  - name: old_string
    description: The text to find and replace
    required: true
  - name: new_string
    description: The replacement text
    required: true
  - name: replace_all
    description: If true, replace all occurrences. Defaults to false.
    required: false
tags: [file, edit, modify]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [Edit]
safety:
  risk-level: medium
---

# Edit File

Edit an existing file by replacing text using the Edit tool.

## Usage

### Replace first occurrence
```
<filepath> old_string="old text" new_string="new text"
```

### Replace all occurrences
```
<filepath> old_string="old text" new_string="new text" replace_all=true
```

### Create file if it doesn't exist
```
<filepath> old_string="" new_string="new content" create_if_missing=true
```

## Notes

- Supports both absolute and relative paths
- Use `~` for home directory paths
- old_string cannot be empty
- The first occurrence is replaced by default
- Set replace_all=true to replace all occurrences
