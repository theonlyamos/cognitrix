/** A reply as one line of plain text, for the caption under the stage. */
export function plainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, '')
    .replace(/^\s*\|?[-:| ]+\|?\s*$/gm, ' ')
    .replace(/[*_~`|]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}
