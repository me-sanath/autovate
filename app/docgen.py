import ast
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from git import Repo, InvalidGitRepositoryError

import sys
from pathlib import Path as _P

# Allow importing the root-level `langraph.py` when running inside the app package
_ROOT = _P(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))
import langraph  # type: ignore


@dataclass
class DocOptions:
    template: str = "api"
    export_formats: List[str] = None  # ["md", "html", "pdf"] any subset
    use_llm: bool = False
    manual_override: bool = False

    def __post_init__(self):
        if self.export_formats is None:
            self.export_formats = ["md", "html"]


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _parse_python_docstrings(source_text: str) -> Dict[Tuple[str, int, int], str]:
    """
    Return mapping from (kind+name, lineno, end_lineno) to existing docstring text for python defs/classes.
    """
    out: Dict[Tuple[str, int, int], str] = {}
    try:
        tree = ast.parse(source_text)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            name = getattr(node, "name", "?")
            lineno = getattr(node, "lineno", -1)
            end_lineno = getattr(node, "end_lineno", -1)
            doc = ast.get_docstring(node) or ""
            if doc:
                out[(f"{kind} {name}", lineno, end_lineno)] = doc
    return out


def _parse_javascript_docstrings(source_text: str) -> Dict[Tuple[str, int, int], str]:
    """
    Parse JSDoc comments for JavaScript/TypeScript functions and classes.
    """
    out: Dict[Tuple[str, int, int], str] = {}
    lines = source_text.splitlines()
    # Pattern to match function/class declarations
    func_re = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$]\w*)\s*[=(]")
    # Pattern to match JSDoc comments (/** ... */)
    jsdoc_re = re.compile(r"/\*\*([\s\S]*?)\*/")
    
    for i, line in enumerate(lines):
        func_match = func_re.match(line)
        if func_match:
            # Look backwards for JSDoc comment
            name = func_match.group(1)
            lineno = i + 1
            # Check previous 5 lines for JSDoc
            comment_start = max(0, i - 5)
            prev_text = "\n".join(lines[comment_start:i])
            jsdoc_match = jsdoc_re.search(prev_text)
            if jsdoc_match:
                doc = jsdoc_match.group(1).strip()
                # Clean up JSDoc formatting
                doc_lines = [l.strip().lstrip("*").strip() for l in doc.splitlines()]
                doc = "\n".join([l for l in doc_lines if l]).strip()
                if doc:
                    kind = "class" if "class" in line else "function"
                    out[(f"{kind} {name}", lineno, lineno)] = doc
    return out


def _parse_java_docstrings(source_text: str) -> Dict[Tuple[str, int, int], str]:
    """
    Parse JavaDoc comments for Java methods and classes.
    """
    out: Dict[Tuple[str, int, int], str] = {}
    lines = source_text.splitlines()
    # Pattern for Java class/method declarations
    java_re = re.compile(r"^\s*(?:public|private|protected)?\s*(?:static)?\s*(?:class|interface|enum|void|[\w<>\[\]]+\s+)([A-Za-z_$]\w*)\s*[({]")
    # Pattern for JavaDoc comments (/** ... */)
    javadoc_re = re.compile(r"/\*\*([\s\S]*?)\*/")
    
    for i, line in enumerate(lines):
        java_match = java_re.match(line)
        if java_match:
            name = java_match.group(1)
            lineno = i + 1
            # Look backwards for JavaDoc comment
            comment_start = max(0, i - 10)
            prev_text = "\n".join(lines[comment_start:i])
            javadoc_match = javadoc_re.search(prev_text)
            if javadoc_match:
                doc = javadoc_match.group(1).strip()
                doc_lines = [l.strip().lstrip("*").strip() for l in doc.splitlines()]
                doc = "\n".join([l for l in doc_lines if l]).strip()
                if doc:
                    kind = "class" if any(kw in line for kw in ["class", "interface", "enum"]) else "method"
                    out[(f"{kind} {name}", lineno, lineno)] = doc
    return out


def _parse_go_docstrings(source_text: str) -> Dict[Tuple[str, int, int], str]:
    """
    Parse Go documentation comments (comments immediately preceding declarations).
    """
    out: Dict[Tuple[str, int, int], str] = {}
    lines = source_text.splitlines()
    # Pattern for Go function/type declarations
    go_re = re.compile(r"^\s*(?:func\s+(?:\([^)]+\)\s+)?([A-Za-z_]\w*)|type\s+([A-Za-z_]\w*))")
    
    for i, line in enumerate(lines):
        go_match = go_re.match(line)
        if go_match:
            name = go_match.group(1) or go_match.group(2)
            lineno = i + 1
            # Look backwards for consecutive comment lines
            comment_lines = []
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("//"):
                comment_lines.insert(0, lines[j].strip()[2:].strip())
                j -= 1
            if comment_lines:
                doc = "\n".join(comment_lines).strip()
                if doc:
                    kind = "func" if go_match.group(1) else "type"
                    out[(f"{kind} {name}", lineno, lineno)] = doc
    return out


def _parse_rust_docstrings(source_text: str) -> Dict[Tuple[str, int, int], str]:
    """
    Parse Rust documentation comments (/// comments).
    """
    out: Dict[Tuple[str, int, int], str] = {}
    lines = source_text.splitlines()
    # Pattern for Rust function/struct/enum/trait declarations
    rust_re = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl|mod)\s+([A-Za-z_]\w*)")
    
    for i, line in enumerate(lines):
        rust_match = rust_re.match(line)
        if rust_match:
            name = rust_match.group(1)
            lineno = i + 1
            # Look backwards for /// comments
            comment_lines = []
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("///"):
                comment_lines.insert(0, lines[j].strip()[3:].strip())
                j -= 1
            if comment_lines:
                doc = "\n".join(comment_lines).strip()
                if doc:
                    kind = "fn" if "fn" in line else ("struct" if "struct" in line else ("enum" if "enum" in line else ("trait" if "trait" in line else "impl")))
                    out[(f"{kind} {name}", lineno, lineno)] = doc
    return out


def _parse_docstrings_by_language(source_text: str, language: str) -> Dict[Tuple[str, int, int], str]:
    """
    Parse docstrings/comments based on the file's language.
    """
    if language == "python":
        return _parse_python_docstrings(source_text)
    elif language in ["javascript", "typescript"]:
        return _parse_javascript_docstrings(source_text)
    elif language == "java":
        return _parse_java_docstrings(source_text)
    elif language == "go":
        return _parse_go_docstrings(source_text)
    elif language == "rust":
        return _parse_rust_docstrings(source_text)
    else:
        return {}


_generic_decl_re = re.compile(
    r"^(\s*)(class|def|interface|func|function|struct)\s+([A-Za-z_]\w*)",
    re.IGNORECASE | re.MULTILINE,
)


def _scan_declarations(text: str, language: str) -> List[Dict]:
    if language == "python":
        return langraph.parse_python_structure(text)
    # fallback generic
    items = []
    for m in _generic_decl_re.finditer(text):
        kind = m.group(2).lower()
        name = m.group(3)
        lineno = text[: m.start()].count("\n") + 1
        items.append({"type": kind, "name": name, "lineno": lineno, "end_lineno": None})
    return items


def _get_all_files_in_repo(repo: Repo, repo_root: Path) -> List[str]:
    """
    Get all files in the repository, respecting .gitignore.
    Returns list of relative paths.
    """
    all_files: List[str] = []
    
    # Use git ls-files to get tracked files (respects .gitignore)
    try:
        tracked = repo.git.ls_files().splitlines()
        for file_path in tracked:
            file_path = file_path.strip()
            if not file_path:
                continue
            abs_path = repo_root / file_path
            if abs_path.exists() and abs_path.is_file():
                lang = langraph.detect_language_from_path(abs_path)
                if lang != "unknown":  # Only add source code files
                    all_files.append(file_path)
    except Exception:
        pass
    
    # Also check untracked but not ignored files
    try:
        # Walk the directory and check each file
        for root, dirs, files in os.walk(repo_root):
            # Skip .git directory
            rel_root_str = os.path.relpath(root, repo_root)
            if rel_root_str == ".":
                rel_root = Path("")
            else:
                rel_root = Path(rel_root_str)
            
            # Skip .git directory
            if ".git" in rel_root.parts:
                dirs[:] = []
                continue
            
            # Skip common ignore directories
            skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "target", "build", "dist", ".pytest_cache", ".ruff_cache", "docs"}
            if any(skip in rel_root.parts for skip in skip_dirs):
                dirs[:] = []
                continue
            
            for file in files:
                if rel_root_str == ".":
                    rel_path = file
                else:
                    rel_path = (rel_root / file).as_posix()
                abs_path = repo_root / rel_path
                
                # Skip if already added
                if rel_path in all_files:
                    continue
                
                # Check if file is ignored by git
                try:
                    ignored_output = repo.git.check_ignore(rel_path)
                    if ignored_output and ignored_output.strip():
                        continue  # File is ignored
                except Exception:
                    # check-ignore returns non-zero if not ignored, which raises exception
                    # This is expected behavior - file is not ignored
                    pass
                
                # Only add source code files
                lang = langraph.detect_language_from_path(abs_path)
                if lang != "unknown":
                    all_files.append(rel_path)
    except Exception:
        pass
    
    return sorted(set(all_files))


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _render_markdown_for_file(
    rel_path: str,
    language: str,
    text: str,
    decls: List[Dict],
    existing_docs: Dict[Tuple[str, int, int], str],
    llm_docs: Dict[str, str],
    template: str,
) -> str:
    lines: List[str] = [f"# {rel_path}\n\n"]
    if template == "api":
        lines.append("## API Documentation\n\n")
    elif template == "class_breakdown":
        lines.append("## Class and Function Breakdown\n\n")
    else:
        lines.append("## Documentation\n\n")
    if not decls:
        lines.append("(No declarations found)\n")
        return "".join(lines)
    for d in decls:
        kind = d.get("type")
        name = d.get("name")
        start = d.get("lineno") or 0
        end = d.get("end_lineno") or start
        header = f"### {kind} `{name}` (lines {start}-{end})\n\n"
        key = (f"{kind} {name}", start, end)
        doc = existing_docs.get(key)
        if not doc:
            # fallback to LLM-generated if available
            doc = llm_docs.get(f"{rel_path}:{kind}:{name}", "")
        body = (doc or "No documentation available.").strip()
        lines.append(header)
        lines.append(body + "\n\n")
    return "".join(lines)


def _convert_md_to_html(md_text: str) -> str:
    try:
        import markdown  # type: ignore
    except Exception:
        # very naive fallback
        return f"<pre>{md_text}</pre>"
    return markdown.markdown(md_text, output_format="html5")


def _write_exports(md_text: str, out_base: Path, export_formats: List[str]) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    if "md" in export_formats:
        out_base.with_suffix(".md").write_text(md_text, encoding="utf-8")
        outputs["md"] = str(out_base.with_suffix(".md"))
    if "html" in export_formats:
        html = _convert_md_to_html(md_text)
        out_base.with_suffix(".html").write_text(html, encoding="utf-8")
        outputs["html"] = str(out_base.with_suffix(".html"))
    if "pdf" in export_formats:
        # best-effort PDF via pdfkit if available
        try:
            import pdfkit  # type: ignore

            html = _convert_md_to_html(md_text)
            tmp_html = out_base.with_suffix(".tmp.html")
            tmp_html.write_text(html, encoding="utf-8")
            pdf_path = out_base.with_suffix(".pdf")
            pdfkit.from_file(str(tmp_html), str(pdf_path))
            outputs["pdf"] = str(pdf_path)
            try:
                tmp_html.unlink()
            except Exception:
                pass
        except Exception:
            # skip silently; caller can inspect missing key
            pass
    return outputs


def _update_history(history_file: Path, entry: Dict) -> None:
    _ensure_dir(history_file.parent)
    history: Dict[str, List[Dict]] = {"entries": []}
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    history.setdefault("entries", []).append(entry)
    history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")


def _generate_llm_docs_for_missing(
    files_data: Dict[str, Dict],
    use_llm: bool,
) -> Dict[str, str]:
    """
    For each file, identify functions without docstrings and send them to LLM individually.
    Returns mapping from "file_path:kind:name" to docstring.
    """
    llm_docs: Dict[str, str] = {}
    
    if not use_llm:
        return llm_docs
    
    # Collect all functions/classes without docstrings
    missing_docs: List[Tuple[str, str, str, str, int]] = []  # (file_path, lang, kind, name, lineno)
    
    for file_path, data in files_data.items():
        decls = data.get("decls", [])
        existing = data.get("existing_docs", {})
        text = data.get("text", "")
        language = data.get("language", "")
        
        for decl in decls:
            kind = decl.get("type", "")
            name = decl.get("name", "")
            lineno = decl.get("lineno", 0)
            key = (f"{kind} {name}", lineno, decl.get("end_lineno", lineno))
            
            if key not in existing:
                missing_docs.append((file_path, language, kind, name, lineno))
    
    if not missing_docs:
        return llm_docs
    
    # Send to LLM in batches (group by file to provide context)
    files_to_process: Dict[str, List[Tuple[str, str, int]]] = {}
    for file_path, lang, kind, name, lineno in missing_docs:
        if file_path not in files_to_process:
            files_to_process[file_path] = []
        files_to_process[file_path].append((kind, name, lineno))
    
    # Process each file's missing docs
    for file_path, missing_items in files_to_process.items():
        data = files_data[file_path]
        text = data.get("text", "")
        language = data.get("language", "")
        
        # Build context for each missing function
        entries = []
        for kind, name, lineno in missing_items:
            # Extract function/class body for context
            lines = text.splitlines()
            start_idx = max(0, lineno - 10)
            end_idx = min(len(lines), lineno + 30)
            context_lines = lines[start_idx:end_idx]
            context = "\n".join(context_lines)
            
            entries.append(f"{kind} {name} (line {lineno}):\n{context}\n---\n")
        
        if not entries:
            continue
        
        prompt = (
            f"Generate concise API-style docstrings for the following {language} declarations. "
            "For each declaration, provide a 2-3 sentence description explaining what it does, "
            "its parameters (if applicable), and return value (if applicable). "
            "Format as JSON object mapping 'kind:name' to the docstring.\n\n"
            + "\n".join(entries)
        )
        
        llm_res = langraph.call_groq_api(
            prompt,
            os.environ.get("GROQ_API_KEY"),
            model=os.environ.get("GROQ_MODEL", "groq-1"),
            endpoint=os.environ.get("GROQ_ENDPOINT"),
        )
        
        if llm_res.get("status") == "ok":
            try:
                response_text = json.dumps(llm_res.get("response", {}))
                # Try to extract JSON from response
                json_match = re.search(r"\{[\s\S]*\}", response_text)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    for kind, name, _lineno in missing_items:
                        key = f"{kind}:{name}"
                        if key in parsed:
                            llm_docs[f"{file_path}:{kind}:{name}"] = parsed[key]
                        elif name in parsed:
                            llm_docs[f"{file_path}:{kind}:{name}"] = parsed[name]
            except Exception:
                # If JSON parsing fails, try to extract docstrings from text
                response_text = str(llm_res.get("response", ""))
                # Simple heuristic: look for patterns like "function_name: description"
                for kind, name, lineno in missing_items:
                    pattern = rf"{re.escape(name)}[:\-]?\s*(.+?)(?:\n\n|\n---|$)"
                    match = re.search(pattern, response_text, re.IGNORECASE | re.DOTALL)
                    if match:
                        doc = match.group(1).strip()
                        if doc:
                            llm_docs[f"{file_path}:{kind}:{name}"] = str(doc)
    
    return llm_docs


def generate_documentation(
    repo_path: str,
    options: DocOptions,
) -> Dict:
    """
    Main entry point for documentation generation pipeline.
    - Scans entire repository (respecting .gitignore)
    - Parses existing docstrings/comments based on language
    - Generates missing docs via LLM for individual functions
    - Generates per-file docs using selected template
    - Exports to requested formats
    - Updates history and optionally commits changes
    """
    repo_root = Path(repo_path).resolve()
    try:
        repo = Repo(str(repo_root))
    except InvalidGitRepositoryError:
        raise FileNotFoundError(f"Not a git repository: {repo_root}")

    out_root = repo_root / "docs"
    files_dir = out_root / "files"
    drafts_dir = out_root / "drafts" if options.manual_override else files_dir
    _ensure_dir(files_dir)
    _ensure_dir(drafts_dir)

    # Get all files in repository (respecting .gitignore)
    all_files = _get_all_files_in_repo(repo, repo_root)
    
    # Process all files: parse declarations and existing docstrings
    files_data: Dict[str, Dict] = {}
    for file_path in all_files:
        abs_path = repo_root / file_path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        
        lang = langraph.detect_language_from_path(abs_path)
        if lang == "unknown":
            continue  # Skip files we can't process
        
        txt = langraph.read_text_file(abs_path)
        if txt is None:
            continue
        
        decls = _scan_declarations(txt, lang)
        if not decls:
            continue  # Skip files with no declarations
        
        existing_docs = _parse_docstrings_by_language(txt, lang)
        
        files_data[file_path] = {
            "text": txt,
            "language": lang,
            "decls": decls,
            "existing_docs": existing_docs,
        }
    
    # Generate LLM docs for missing docstrings
    llm_docs = _generate_llm_docs_for_missing(files_data, options.use_llm)
    
    # Generate documentation for each file
    generated_outputs: Dict[str, Dict[str, str]] = {}
    touched_files: List[str] = []
    
    for file_path, data in files_data.items():
        lang = data["language"]
        txt = data["text"]
        decls = data["decls"]
        existing_docs = data["existing_docs"]
        
        md = _render_markdown_for_file(file_path, lang, txt, decls, existing_docs, llm_docs, options.template)
        
        safe_name = file_path.replace("/", "__")
        out_base = drafts_dir / safe_name
        outputs = _write_exports(md, out_base, options.export_formats)
        generated_outputs[file_path] = outputs
        touched_files.append(file_path)

    # Write/update summary
    summary_md = [
        "# Documentation Summary\n\n",
        f"Template: {options.template}\n\n",
        f"Total files documented: {len(touched_files)}\n\n",
        f"Files with generated documentation:\n",
    ]
    for fp in touched_files[:100]:  # Limit to first 100 in summary
        summary_md.append(f"- {fp}\n")
    if len(touched_files) > 100:
        summary_md.append(f"\n... and {len(touched_files) - 100} more files\n")
    
    (out_root / "summary.md").write_text("".join(summary_md), encoding="utf-8")

    # Update history
    head = repo.head.commit.hexsha if repo.head.is_valid() else None
    history_entry = {
        "source_commit": head,
        "template": options.template,
        "manual_override": options.manual_override,
        "export_formats": options.export_formats,
        "touched": touched_files,
        "total_files": len(touched_files),
    }
    _update_history(out_root / "history.json", history_entry)

    # Commit if not manual override
    commit_sha: Optional[str] = None
    if not options.manual_override:
        # Collect only actually generated files and ensure they exist
        to_add: List[str] = []
        for fp, outs in generated_outputs.items():
            for _fmt, path_str in outs.items():
                p = Path(path_str)
                if p.exists():
                    to_add.append(str(p))
        # add summary and history if present
        for meta in [out_root / "summary.md", out_root / "history.json"]:
            if meta.exists():
                to_add.append(str(meta))
        if to_add:
            repo.index.add(to_add)
            msg = f"docs: update for {head[:8] if head else 'working tree'} using template {options.template} ({len(touched_files)} files)"
            try:
                commit = repo.index.commit(msg)
                commit_sha = commit.hexsha
            except Exception:
                commit_sha = None

    return {
        "changed": touched_files,
        "outputs": generated_outputs,
        "summary": str(out_root / "summary.md"),
        "history": str(out_root / "history.json"),
        "docs_dir": str(files_dir),
        "drafts_dir": str(drafts_dir) if options.manual_override else None,
        "linked_commit": commit_sha,
        "total_files": len(touched_files),
    }
