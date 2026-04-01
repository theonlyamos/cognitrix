---
name: write-file
description: Write content to a file, creating it if it doesn't exist
context: fork
args:
  - name: file_path
    description: Path to the file to write
    required: true
  - name: content
    description: Content to write to the file
    required: true
tags: [file, write, create]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
safety:
  risk-level: medium
---

# Write File

Write content to a file at "$(arg file_path)".

## Input Format

The argument should be in format: `<filepath> <content>`

Example: `example.txt "Hello world"`

## Steps

1. Parse the filepath and content from arguments
2. Use `printf "%s" "$(arg content)" > "$(arg file_path)"` to write content to file
3. Use `cat -n "$(arg file_path)"` to verify the file was created correctly
4. If file already exists and you want to append, use `printf "%s" "<content>" >> <filepath>`

## Notes

- Use `printf` instead of `echo` for more reliable output
- For multi-line content, use `\n` for newlines
- If content contains special characters, ensure proper escaping