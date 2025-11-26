from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from git import InvalidGitRepositoryError, Repo

def parse_patch_for_changes(patch_text):
    """
    Return list of change dicts: {type: 'add'|'del', lineno: int, content: str}
    Line numbers are relative to the old/new file as per hunk header.
    """
    changes = []
    lines = patch_text.splitlines()
    i = 0
    hunk_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
    while i < len(lines):
        m = hunk_re.match(lines[i])
        if not m:
            i += 1
            continue
        old_line = int(m.group(1))
        new_line = int(m.group(3))
        i += 1
        while i < len(lines) and not lines[i].startswith('@@ '):
            l = lines[i]
            if l.startswith('+') and not l.startswith('+++'):
                changes.append({'type': 'add', 'lineno': new_line, 'content': l[1:]})
                new_line += 1
            elif l.startswith('-') and not l.startswith('---'):
                changes.append({'type': 'del', 'lineno': old_line, 'content': l[1:]})
                old_line += 1
            else:
                # context line
                old_line += 1
                new_line += 1
            i += 1
    return changes

def load_file_at_commit(repo: Repo, path: str, commit_ref: str):
    try:
        return repo.git.show(f'{commit_ref}:{path}')
    except Exception:
        return None

def find_enclosing_python_chain(source_text, target_lineno):
    try:
        tree = ast.parse(source_text)
    except Exception:
        return None
    # build parent map
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    # collect candidate nodes (FunctionDef, AsyncFunctionDef, ClassDef) with lineno/end_lineno
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
                if node.lineno <= target_lineno <= node.end_lineno:
                    candidates.append(node)
    if not candidates:
        return None
    # pick the innermost (deepest) candidate: the one with largest lineno (closest to target)
    node = max(candidates, key=lambda n: (n.lineno, (n.end_lineno - n.lineno)))
    # build chain upwards
    chain = []
    while node in parent:
        if isinstance(node, ast.ClassDef):
            chain.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(f"def {node.name}")
        node = parent[node]
    chain.reverse()
    return " -> ".join(chain) if chain else "module"

def find_enclosing_by_regex(source_text, target_lineno):
    """
    Fallback for non-Python files or when AST isn't available.
    Scans lines upward for nearest def/class and attempts to build a simple nesting chain by indentation.
    """
    src_lines = source_text.splitlines()
    idx = target_lineno - 1
    if idx < 0 or idx >= len(src_lines):
        return "module"
    # collect all defs/classes with lineno and indent
    decl_re = re.compile(r'^(\s*)(?:def|class)\s+([A-Za-z_]\w*)')
    decls = []
    for i, line in enumerate(src_lines):
        m = decl_re.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            kind = 'def' if line.strip().startswith('def') else 'class'
            name = m.group(2)
            decls.append({'lineno': i+1, 'indent': indent, 'kind': kind, 'name': name})
    # filter decls that start before target_lineno
    prev = [d for d in decls if d['lineno'] <= target_lineno]
    if not prev:
        return "module"
    # build nesting by walking backwards and picking those with non-decreasing indent
    chain = []
    current_min_indent = 10**9
    for d in reversed(prev):
        if d['indent'] < current_min_indent:
            chain.append(f"{d['kind']} {d['name']}")
            current_min_indent = d['indent']
    chain.reverse()
    return " -> ".join(chain) if chain else "module"

def collect_change_contexts(repo: Repo, commit, parent) -> List[Dict[str, str]]:
    """Collect structured change contexts between commit and parent."""
    contexts: List[Dict[str, str]] = []

    for diff in commit.diff(parent, create_patch=True):
        patch_bytes = diff.diff
        if not patch_bytes:
            continue
        patch = patch_bytes.decode('utf-8', errors='ignore') if isinstance(patch_bytes, (bytes, bytearray)) else patch_bytes
        changes = parse_patch_for_changes(patch)
        if not changes:
            continue
        for ch in changes:
            if ch['type'] == 'add':
                path = diff.b_path
                ref = commit.hexsha
            else:
                path = diff.a_path
                ref = parent.hexsha
            if not path:
                continue
            file_text = load_file_at_commit(repo, path, ref)
            if file_text is None:
                continue
            lineno = ch['lineno']
            src_lines = file_text.splitlines()
            ctx_start = max(0, lineno - 3)
            ctx_end = min(len(src_lines), lineno + 2)
            context_snippet = "\n".join(src_lines[ctx_start:ctx_end])
            if path.endswith('.py'):
                chain = find_enclosing_python_chain(file_text, lineno)
                if chain is None:
                    chain = find_enclosing_by_regex(file_text, lineno)
            else:
                chain = find_enclosing_by_regex(file_text, lineno)

            contexts.append({
                "file_path": path,
                "ref": ref,
                "change_type": ch['type'],
                "lineno": lineno,
                "changed_line": ch['content'],
                "context": context_snippet,
                "enclosing_chain": chain,
                "commit": commit.hexsha,
                "parent_commit": parent.hexsha,
            })
    return contexts


def build_aggregated_prompt(change_contexts, commit, parent) -> Optional[str]:
    """Build a single aggregated RAG prompt from collected change contexts."""
    if not change_contexts:
        return None

    header = (
        "You are an assistant helping to reason about code changes for retrieval.\n"
        f"Repository commit: {commit.hexsha}\n"
        f"Parent commit: {parent.hexsha}\n\n"
        "Below are all detected changes. Each change is numbered and includes file/ref, change type, line number, the changed line, a few surrounding lines, and the enclosing structure.\n\n"
    )
    entries = []
    for i, c in enumerate(change_contexts, start=1):
        entry = (
            f"Change {i}:\n"
            f"File: {c['file_path']} @ {c['ref']}\n"
            f"Change type: {c['change_type']}  Line: {c['lineno']}\n\n"
            f"Changed line:\n{c['changed_line'].rstrip()}\n\n"
            f"Surrounding context (few lines):\n{c['context']}\n\n"
            f"Enclosing structure: {c['enclosing_chain']}\n\n"
            "----\n\n"
        )
        entries.append(entry)
    body = "".join(entries)

    instructions = (
        "Goal: Given the aggregated changes above, (1) identify likely intents or bugs introduced by these changes, "
        "(2) enumerate keywords, symbols, and docs to retrieve (APIs, language constructs, common patterns), "
        "(3) propose minimal corrective patches or improvements for each change, and (4) list concrete tests/verification steps.\n\n"
        "Return a short structured answer with sections: Summary, Possible Root Causes (per change), Retrieval Keywords, Suggested Patches (per change), Validation Steps, Confidence.\n"
        "Keep suggestions minimal and clearly indicate any assumptions."
    )

    return header + body + instructions


def analyze_commit(repo_path: str, commit_ref: Optional[str] = None, parent_ref: Optional[str] = None) -> Dict[str, object]:
    """Analyze a commit and return RAG-ready context plus aggregated prompt."""
    repo_path_resolved = Path(repo_path).resolve()
    try:
        repo = Repo(str(repo_path_resolved))
    except InvalidGitRepositoryError as err:
        raise FileNotFoundError(f"Not a git repository: {repo_path}") from err

    commit = repo.commit(commit_ref) if commit_ref else repo.head.commit
    if parent_ref:
        parent = repo.commit(parent_ref)
    else:
        if not commit.parents:
            raise ValueError("No parent commit to diff against. Provide --parent to compare explicitly.")
        parent = commit.parents[0]

    change_contexts = collect_change_contexts(repo, commit, parent)
    prompt = build_aggregated_prompt(change_contexts, commit, parent)
    return {
        "repo": str(repo_path_resolved),
        "commit": commit.hexsha,
        "parent": parent.hexsha,
        "change_count": len(change_contexts),
        "changes": change_contexts,
        "prompt": prompt,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze git commits and build RAG prompts.")
    parser.add_argument("repo", nargs="?", default="demo-repo", help="Path to the repository to analyze.")
    parser.add_argument("--commit", help="Commit hash/refs to analyze (defaults to HEAD).")
    parser.add_argument("--parent", help="Parent commit hash/refs (defaults to first parent).")
    parser.add_argument("--json", action="store_true", help="Print JSON result instead of formatted prompt.")
    args = parser.parse_args(argv)

    try:
        result = analyze_commit(args.repo, args.commit, args.parent)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        prompt = result.get("prompt")
        if not prompt:
            print("No changes collected to build an aggregated prompt.")
        else:
            print(f"Collected {result['change_count']} changes. Aggregated RAG Prompt:\n")
            print(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
