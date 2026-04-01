---
name: code-review
description: >
  Review code for bugs, style issues, and improvements. Use when asked to
  review, audit, or critique code quality.
args:
  - name: file_or_directory
    description: Path to the file or directory to review
    required: true
tags: [code, review, quality]
category: development
version: "1.0.0"
author: theonlyamos
allowed-tools: [bash, read_file, python_repl]
safety:
  risk-level: low
---

# Code Review

Review the code at "$(arg file_or_directory)":

1. **Read the file(s)** thoroughly
2. **Analyze** for the following categories:
   - 🐛 **Bugs and logic errors**
   - 🔒 **Security vulnerabilities**
   - ⚡ **Performance issues**
   - 📖 **Code style and readability**
   - ⚠️ **Missing error handling**
   - 🧪 **Test coverage gaps**

3. **Rate each issue** by severity:
   - 🔴 **Critical** — will cause failures or security breaches
   - 🟡 **Warning** — potential problems or bad practices
   - 🔵 **Suggestion** — improvements for readability or maintainability

## Output Format

For each issue found:

```
### [severity emoji] [category]: Brief description
- **File:** `filename:line_number`
- **Issue:** What's wrong and why it matters
- **Suggestion:** How to fix it
```

End with a summary table:

```
## Summary
| Severity | Count |
|----------|-------|
| 🔴 Critical | N |
| 🟡 Warning  | N |
| 🔵 Suggestion | N |
```

If no issues are found, say so — don't invent problems.
