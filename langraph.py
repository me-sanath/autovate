import os
import re
import ast
import json
from pathlib import Path
from collections import Counter, defaultdict

# Simple extension -> language mapping
_EXT_LANG = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.java': 'java',
    '.go': 'go',
    '.rs': 'rust',
    '.c': 'c',
    '.cpp': 'cpp',
    '.h': 'c_header',
    '.html': 'html',
    '.css': 'css',
    '.json': 'json',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.sh': 'shell',
    'Dockerfile': 'dockerfile',
}

# Common project markers
_PROJECT_MARKERS = {
    'python': ['requirements.txt', 'pyproject.toml', 'setup.py', 'Pipfile'],
    'node': ['package.json', 'yarn.lock'],
    'docker': ['Dockerfile', 'docker-compose.yml'],
    'rust': ['Cargo.toml'],
    'go': ['go.mod'],
}

def detect_language_from_path(p: Path):
    if p.name in _EXT_LANG:
        return _EXT_LANG[p.name]
    ext = p.suffix.lower()
    return _EXT_LANG.get(ext, 'unknown')

def read_text_file(path: Path, max_size=1_000_000):
    try:
        if path.stat().st_size > max_size:
            return None
        return path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None

def parse_python_structure(source_text):
    """
    Return list of {'type': 'class'|'def', 'name': str, 'lineno': int, 'end_lineno': int}
    """
    try:
        tree = ast.parse(source_text)
    except Exception:
        return []
    items = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            typ = 'class' if isinstance(node, ast.ClassDef) else 'def'
            lineno = getattr(node, 'lineno', None)
            end_lineno = getattr(node, 'end_lineno', None)
            items.append({'type': typ, 'name': name, 'lineno': lineno, 'end_lineno': end_lineno})
    # prefer top-level ordering
    items.sort(key=lambda x: (x['lineno'] or 0))
    return items

_generic_decl_re = re.compile(r'^\s*(class|def|interface|func|function|struct)\s+([A-Za-z_]\w*)', re.IGNORECASE | re.MULTILINE)

def parse_generic_structure(source_text):
    """
    Very light-weight scanner for non-Python languages to find named declarations.
    """
    items = []
    for m in _generic_decl_re.finditer(source_text):
        kind = m.group(1).lower()
        name = m.group(2)
        lineno = source_text[:m.start()].count('\n') + 1
        items.append({'type': kind, 'name': name, 'lineno': lineno, 'end_lineno': None})
    return items

def scan_codebase(root_path, max_files=2000, ignore_dirs=None):
    """
    Walk the directory and gather metadata and parsed structure for files.
    Returns a dict with files, languages, markers, and basic stats.
    """
    root = Path(root_path)
    if ignore_dirs is None:
        ignore_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}
    files = []
    languages = Counter()
    markers = defaultdict(list)
    parsed = {}
    seen = 0

    for p in root.rglob('*'):
        if seen >= max_files:
            break
        if any(part in ignore_dirs for part in p.parts):
            continue
        if p.is_file():
            seen += 1
            lang = detect_language_from_path(p)
            languages[lang] += 1
            rel = p.relative_to(root).as_posix()
            files.append({'path': rel, 'size': p.stat().st_size, 'language': lang})
            # check markers
            for key, marker_list in _PROJECT_MARKERS.items():
                if p.name in marker_list:
                    markers[key].append(rel)
            txt = read_text_file(p)
            if txt is None:
                continue
            if lang == 'python':
                parsed[rel] = parse_python_structure(txt)
            else:
                parsed[rel] = parse_generic_structure(txt)
    summary = {
        'root': str(root),
        'file_count': len(files),
        'languages': dict(languages),
        'markers': {k: v for k, v in markers.items()},
        'files': files,
        'parsed': parsed,
    }
    return summary

def infer_project_type(scan_summary):
    """
    Heuristic inference based on presence of markers and language predominance.
    """
    markers = scan_summary.get('markers', {})
    langs = scan_summary.get('languages', {})
    guesses = []
    if markers.get('python'):
        guesses.append('python-package')
    if markers.get('node'):
        guesses.append('nodejs-app')
    if markers.get('docker'):
        guesses.append('dockerized')
    if markers.get('rust'):
        guesses.append('rust-crate')
    if markers.get('go'):
        guesses.append('go-module')
    # fallback by dominant language
    if not guesses:
        if not langs:
            guesses.append('unknown')
        else:
            dominant = max(langs.items(), key=lambda x: x[1])[0]
            guesses.append(f'dominant-language:{dominant}')
    return guesses

def build_aggregated_prompt(scan_summary, project_guesses, max_changes_display=10):
    """
    Build a concise prompt describing the codebase structure for an LLM.
    """
    files_sample = scan_summary.get('files', [])[:max_changes_display]
    entries = []
    for f in files_sample:
        path = f['path']
        lang = f['language']
        parsed = scan_summary.get('parsed', {}).get(path, [])
        decls = ", ".join([f"{it['type']} {it['name']}" for it in parsed[:6]])
        entries.append(f"- {path} ({lang}) decls: {decls}")
    header = (
        f"Repository root: {scan_summary.get('root')}\n"
        f"File count: {scan_summary.get('file_count')}\n"
        f"Languages: {json.dumps(scan_summary.get('languages', {}))}\n"
        f"Marker files: {json.dumps(scan_summary.get('markers', {}))}\n"
        f"Project type guesses: {project_guesses}\n\n"
        "Sample files and top declarations:\n" + "\n".join(entries) + "\n\n"
    )
    instructions = (
        "Task: Based on the information above, (1) summarize the project's purpose and likely runtime/framework, "
        "(2) enumerate the key modules/components and where to start reading (top 5 files), "
        "(3) list integration/deployment artifacts, and (4) propose 3 focused questions to help refine the analysis.\n"
        "Return a short structured JSON-like summary with keys: summary, entry_points, artifacts, questions, confidence.\n"
        "Keep output concise and actionable."
    )
    return header + instructions

def call_groq_api(prompt, api_key, model='groq-1', endpoint=None, timeout=20):
    """
    Minimal Groq API call. Requires 'requests' package. endpoint can be overridden.
    Returns dict with 'status' and 'response' or error.
    """
    try:
        import requests
    except Exception:
        return {'status': 'error', 'error': "requests package not installed; install requests to call Groq API."}
    if not api_key:
        return {'status': 'error', 'error': 'No API key provided.'}
    if endpoint is None:
        endpoint = 'https://api.groq.ai/v1'  # conservative default; user may override
    payload = {
        'model': model,
        'input': prompt,
        'max_output_tokens': 600,
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {'status': 'error', 'error': f'HTTP {resp.status_code}: {resp.text}'}
        return {'status': 'ok', 'response': resp.json()}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

def analyze_project(path='.', groq_api_key=None, use_llm=False, model='groq-1', endpoint=None):
    """
    High-level entry point. Scans the codebase, infers project type, builds prompt.
    If use_llm=True and groq_api_key provided, sends prompt to Groq and returns LLM output.
    Returns a dict {scan_summary, project_guesses, prompt, llm_result (optional)}.
    """
    scan_summary = scan_codebase(path)
    guesses = infer_project_type(scan_summary)
    prompt = build_aggregated_prompt(scan_summary, guesses)
    result = {'scan_summary': scan_summary, 'project_guesses': guesses, 'prompt': prompt}
    if use_llm:
        llm_out = call_groq_api(prompt, groq_api_key, model=model, endpoint=endpoint)
        result['llm_result'] = llm_out
    return result

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Langraph: summarize codebase structure and optionally query Groq LLM.')
    parser.add_argument('path', nargs='?', default='.', help='Path to project root')
    parser.add_argument('--llm', action='store_true', help='Call Groq LLM with aggregated prompt')
    parser.add_argument('--key', default=None, help='Groq API key (or set env var GROQ_API_KEY)')
    parser.add_argument('--model', default='groq-1', help='Groq model name')
    parser.add_argument('--endpoint', default=None, help='Override Groq endpoint')
    args = parser.parse_args()
    api_key = args.key or os.environ.get('GROQ_API_KEY')
    out = analyze_project(args.path, groq_api_key=api_key, use_llm=args.llm, model=args.model, endpoint=args.endpoint)
    # Print a concise JSON summary
    print(json.dumps({
        'root': out['scan_summary']['root'],
        'file_count': out['scan_summary']['file_count'],
        'languages': out['scan_summary']['languages'],
        'project_guesses': out['project_guesses'],
        'llm_status': out.get('llm_result', {}).get('status') if args.llm else 'skipped'
    }, indent=2))

