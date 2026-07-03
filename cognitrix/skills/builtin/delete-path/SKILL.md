---
name: delete-path
description: Delete a file or directory (recursively if directory)
context: fork
args:
  - name: path
    description: Path to the file or directory to delete
    required: true
tags: [delete, remove, rm]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
safety:
  risk-level: high
---

# Delete Path

Delete a file or directory.

## Steps

1. Determine if path is a file or directory:
   - If file: use `rm "$(arg path)"`
   - If directory: use `rm -r "$(arg path)"`
2. First, check if path exists with `ls -ld "$(arg path)"` 
3. Confirm deletion was successful with `ls` to verify file/dir is gone
4. Report the result

## Safety

- Always verify the path exists before deleting
- For directories, use `-r` for recursive deletion (removes all contents)
- Use `-i` flag for interactive mode (prompts before each deletion): `rm -ri`
- Use `-f` to force and ignore non-existent files: `rm -rf`

## Notes

- This action is irreversible - deleted files cannot be recovered
- Be careful with wildcards (e.g., `rm *` deletes everything in current directory)
- Consider using `ls` first to verify what will be deleted