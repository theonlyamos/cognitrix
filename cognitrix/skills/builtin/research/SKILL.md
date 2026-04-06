---
name: research
description: >
  Conduct comprehensive and exhaustive research on a given topic on the web. Use when the user asks for deep dives, thorough investigations, or comprehensive reports.
context: same
args:
  - name: topic
    description: The topic to research
    required: true
tags: [research, web, search, deep-dive]
category: research
version: "1.1.0"
author: theonlyamos
allowed-tools: [use_skill, WebFetch]
safety:
  risk-level: low
---

# Comprehensive Web Research

Conduct an exhaustive and deeply thorough research on the following topic: "$(arg topic)"

Your primary goal is to gather multiple perspectives, deep insights, and up-to-date facts. You MUST NOT rely on a single search or a single webpage. 

## Research Methodology (Strictly Follow)

1. **Initial Broad Search**: Use the `use_skill` tool to call the `brave-search` or `internet-search` skill to perform broad queries on the topic to establish the baseline landscape.
2. **First Wave Fetch**: Use the `WebFetch` tool to extract the content of the top 10 most promising results from your initial search.
3. **Targeted Deep-Dive Searches**: Based on what you just read, identify knowledge gaps, sub-topics, controversies, or specific details that require deeper investigation. Perform at least 2 to 3 MORE distinct search queries using the `brave-search` or `internet-search` skills.
4. **Second Wave Fetch**: Use `WebFetch` on additional URLs discovered in the deep-dive searches to expand your knowledge base.
5. **Synthesis & Cross-referencing**: Combine the information. If sources disagree, document the differing perspectives. 

Remember, you MUST perform **multiple searches** and **multiple web fetches** before beginning your synthesis. Do not stop at surface-level information.

## Requirements

- Be exhaustive, structured, and comprehensive. 
- Use detailed headings, bullet points, and tables (if applicable) for readability.
- Cite specific claims with numbered references `[n]`.
- Focus on authoritative and recent sources where possible.

## Output Format

Your final output must be structured as follows:

```markdown
# Comprehensive Research Report: [Topic]

## Executive Summary
A concise but detailed 3-5 sentence overview capturing the core essence of the topic.

## Key Findings
Break down the main insights logically.
### [Subtopic 1]
- ...
### [Subtopic 2]
- ...

## In-Depth Analysis / Deep Dive
Provide the nuanced perspectives, history, mechanics, or deeper context you discovered during your multiple fetches. 
- Competing theories or controversies (if any)
- Recent developments

## Discrepancies & Gaps (Optional)
Note any conflicting information found across different sources or areas where information was sparse.

## Sources
Provide a well-formatted list of all URLs you fetched and used.
1. [Title](url)
2. [Title](url)
...
```

## Notes

- Use the `use_skill` tool to invoke the `brave-search` or `internet-search` skills to search the web.
- Use `WebFetch` to fetch the contents of individual pages.
- Act autonomously to gather enough data. Ensure the research is genuinely thorough rather than just a quick summary.
