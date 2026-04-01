---
name: edit-file
description: Edit an existing file using replace, insert, append, or line range operations
context: fork
args:
  - name: file_path
    description: Path to the file to edit
    required: true
  - name: operation
    description: Operation to perform (replace, insert, append, replace_range)
    required: true
  - name: line_number
    description: Line number for the operation
    required: true
  - name: end_line
    description: End line for replace_range operation
    required: false
  - name: new_content
    description: New content to insert/replace
    required: true
tags: [file, edit, modify]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
safety:
  risk-level: medium
---

# Edit File

Edit an existing file using various operations.

## Input Format

The argument should be: `<filepath> <operation> <start_line> [end_line] <new_content>`

Operations:
- `replace` - Replace a specific line
- `insert` - Insert before a line
- `append` - Add content after a line  
- `replace_range` - Replace range of lines

Example: `test.txt replace 5 "new content"`

## Steps

1. Parse filepath, operation, line numbers, and content from arguments
2. For `replace <n>`: Use `sed -i '<n>s/.*/new_content/' <filepath>`
3. For `insert <n>`: Use `sed -i '<n>i new_content' <filepath>`
4. For `append <n>`: Use `sed -i '$a new_content' <filepath>` or `sed -i '<n>a new_content' <filepath>`
5. For `replace_range <start> <end>`: Use `sed -i '<start>,<end>c new_content' <filepath>`
6. Verify with `cat -n <filepath>`

## Notes

- Line numbers are 1-based
- For special characters in content, use proper escaping
- Make a backup first with `cp <filepath> <filepath>.bak`