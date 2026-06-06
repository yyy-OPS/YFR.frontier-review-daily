// 安全 markdown 渲染 (对应 v0.6 render_markdown_safe: commonmark + 白名单 sanitize)
// 在此之上补两件事:
//   1. 流式安全 —— 渲染半成品正文时闭合未完成的代码围栏, 避免后文被吞;
//   2. 引用三色标记语义化 —— 把后端 cite_check 插入的行内 emoji 转成带文字的徽标。
import { marked } from "marked";
import DOMPurify from "dompurify";
import katex from "katex";

marked.setOptions({ gfm: true, breaks: false });

// 引用校验三色标记。后端 cite_check._annotate 在正文行内插入 ✅/⚠️/❌ (CITE_MARK),
// 裸 emoji 嵌在中文正文里极难分辨, 这里统一转成带文字的语义徽标 (复用设计系统 .badge)。
type CiteKind = "ok" | "warn" | "bad";
const CITE: Record<CiteKind, { cls: string; label: string; title: string }> = {
  ok: { cls: "badge badge-ok cite-mark", label: "已核验", title: "DOI/PMID 精确命中语料" },
  warn: { cls: "badge badge-warn cite-mark", label: "待核", title: "作者+年模糊命中, 或编号待人工复核" },
  bad: { cls: "badge badge-danger cite-mark", label: "存疑", title: "语料中未找到, 疑似虚构" },
};

function citeBadge(kind: CiteKind): string {
  const b = CITE[kind];
  return `<span class="${b.cls}" title="${b.title}">${b.label}</span>`;
}

// 行内 emoji → 徽标 HTML (marked 原样透传行内 HTML, 随后由 DOMPurify 白名单清洗)。
function markCitations(md: string): string {
  return md
    .replace(/✅/g, citeBadge("ok")) // ✅
    .replace(/⚠️?/g, citeBadge("warn")) // ⚠ / ⚠️ (含可选变体选择符)
    .replace(/❌/g, citeBadge("bad")); // ❌
}

// 流式渲染时正文可能停在半截语法上, 最常见的是未闭合的 ``` 代码围栏——
// 它会把后续所有正文吞进代码块。补一个收尾围栏即可平滑渲染。
function balanceFences(md: string): string {
  const fences = (md.match(/```/g) || []).length;
  return fences % 2 === 1 ? `${md}\n\`\`\`` : md;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderMath(tex: string, displayMode: boolean): string {
  const source = tex.trim();
  if (!source) return "";
  try {
    return katex.renderToString(source, {
      displayMode,
      throwOnError: false,
      strict: "ignore",
      trust: false,
      output: "html",
    });
  } catch {
    return `<code class="math-fallback">${escapeHtml(source)}</code>`;
  }
}

function protectInlineCode(segment: string): [string, string[]] {
  const code: string[] = [];
  const protectedSegment = segment.replace(/`[^`\n]*`/g, (match) => {
    const token = `@@INLINE_CODE_${code.length}@@`;
    code.push(match);
    return token;
  });
  return [protectedSegment, code];
}

function restoreInlineCode(segment: string, code: string[]): string {
  return segment.replace(/@@INLINE_CODE_(\d+)@@/g, (_match, index) => code[Number(index)] ?? "");
}

function restoreTokens(segment: string, tokens: string[], prefix: string): string {
  return segment.replace(new RegExp(`@@${prefix}_(\\d+)@@`, "g"), (_match, index) => tokens[Number(index)] ?? "");
}

function renderPlainScientificNotation(segment: string): string {
  return segment
    .replace(/([A-Za-z0-9)\]])_\(([-+−A-Za-z0-9,.\s·/]+?)\)/g, (_match, head, value) => `${head}<sub>${escapeHtml(value)}</sub>`)
    .replace(/([A-Za-z0-9)\]])_\{([-+−A-Za-z0-9,.\s·/]+?)\}/g, (_match, head, value) => `${head}<sub>${escapeHtml(value)}</sub>`)
    .replace(/([A-Za-z0-9)\]])\^\(([-+−A-Za-z0-9,.\s·/]+?)\)/g, (_match, head, value) => `${head}<sup>${escapeHtml(value)}</sup>`)
    .replace(/([A-Za-z0-9)\]])\^\{([-+−A-Za-z0-9,.\s·/]+?)\}/g, (_match, head, value) => `${head}<sup>${escapeHtml(value)}</sup>`);
}

function renderMathInSegment(segment: string): string {
  const [protectedSegment, code] = protectInlineCode(segment);
  const math: string[] = [];
  const withMathTokens = protectedSegment
    .replace(/\$\$([\s\S]+?)\$\$/g, (_match, tex) => {
      const token = `@@MATH_HTML_${math.length}@@`;
      math.push(renderMath(tex, true));
      return token;
    })
    .replace(/\\\[([\s\S]+?)\\\]/g, (_match, tex) => {
      const token = `@@MATH_HTML_${math.length}@@`;
      math.push(renderMath(tex, true));
      return token;
    })
    .replace(/\\\(([\s\S]+?)\\\)/g, (_match, tex) => {
      const token = `@@MATH_HTML_${math.length}@@`;
      math.push(renderMath(tex, false));
      return token;
    })
    .replace(/(^|[^\\$])\$([^\n$]+?)\$(?!\$)/g, (_match, prefix, tex) => {
      const token = `@@MATH_HTML_${math.length}@@`;
      math.push(renderMath(tex, false));
      return `${prefix}${token}`;
    });
  const rendered = restoreTokens(renderPlainScientificNotation(withMathTokens), math, "MATH_HTML");
  return restoreInlineCode(rendered, code);
}

function renderMathBlocks(md: string): string {
  return md
    .split(/(```[\s\S]*?```|~~~[\s\S]*?~~~)/g)
    .map((part, index) => (index % 2 === 1 ? part : renderMathInSegment(part)))
    .join("");
}

export function renderMarkdown(md: string, opts?: { streaming?: boolean }): string {
  const src = markCitations(renderMathBlocks(opts?.streaming ? balanceFences(md) : md));
  const html = marked.parse(src, { async: false }) as string;
  return DOMPurify.sanitize(html);
}
