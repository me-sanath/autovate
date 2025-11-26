from __future__ import annotations

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

def call_groq_api(prompt, api_key, model='llama-3.1-8b-instant', endpoint=None, timeout=30, system_prompt=None, max_tokens=4096):
    """
    Proper Groq API call using chat completions format. Requires 'requests' package.
    Returns dict with 'status' and 'response' or error.
    """
    try:
        import requests
    except Exception:
        return {'status': 'error', 'error': "requests package not installed; install requests to call Groq API."}
    if not api_key:
        return {'status': 'error', 'error': 'No API key provided.'}
    if endpoint is None:
        endpoint = 'https://api.groq.com/openai/v1/chat/completions'
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': 0.1,  # Lower temperature for code fixes
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {'status': 'error', 'error': f'HTTP {resp.status_code}: {resp.text}'}
        data = resp.json()
        if 'choices' in data and len(data['choices']) > 0:
            content = data['choices'][0].get('message', {}).get('content', '')
            return {'status': 'ok', 'response': content, 'raw': data}
        return {'status': 'error', 'error': 'Unexpected response format'}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

def chunk_code_with_context(file_path: Path, content: str, max_chunk_size: int = 2000, context_lines: int = 5):
    """
    Split code file into chunks with surrounding context.
    Returns list of dicts with 'chunk_id', 'start_line', 'end_line', 'content', 'context_before', 'context_after'.
    """
    lines = content.split('\n')
    chunks = []
    chunk_id = 0
    
    i = 0
    while i < len(lines):
        chunk_start = i
        chunk_lines = []
        context_before = []
        context_after = []
        
        # Add context before
        if chunk_start > 0:
            context_before = lines[max(0, chunk_start - context_lines):chunk_start]
        
        # Build chunk
        current_size = 0
        while i < len(lines) and current_size < max_chunk_size:
            line = lines[i]
            chunk_lines.append(line)
            current_size += len(line) + 1  # +1 for newline
            i += 1
        
        # Add context after
        if i < len(lines):
            context_after = lines[i:min(len(lines), i + context_lines)]
        
        chunk_content = '\n'.join(chunk_lines)
        full_context = '\n'.join(context_before + chunk_lines + context_after)
        
        chunks.append({
            'chunk_id': chunk_id,
            'file_path': str(file_path),
            'start_line': chunk_start + 1,  # 1-indexed
            'end_line': i,
            'content': chunk_content,
            'context_before': '\n'.join(context_before),
            'context_after': '\n'.join(context_after),
            'full_context': full_context,
        })
        chunk_id += 1
    
    return chunks


def detect_code_issues(file_path: Path, content: str, language: str = 'python'):
    """
    Detect potential issues in code file using basic heuristics.
    Returns list of issue dicts with 'type', 'line', 'message', 'severity'.
    """
    issues = []
    lines = content.split('\n')
    
    # Basic Python issue detection
    if language == 'python':
        # Check for syntax errors
        try:
            ast.parse(content)
        except SyntaxError as e:
            issues.append({
                'type': 'syntax_error',
                'line': e.lineno or 0,
                'message': str(e),
                'severity': 'error',
            })
        
        # Check for common issues
        for i, line in enumerate(lines, 1):
            # Long lines
            if len(line) > 120:
                issues.append({
                    'type': 'long_line',
                    'line': i,
                    'message': f'Line exceeds 120 characters ({len(line)} chars)',
                    'severity': 'warning',
                })
            
            # TODO/FIXME comments
            if 'TODO' in line.upper() or 'FIXME' in line.upper():
                issues.append({
                    'type': 'todo',
                    'line': i,
                    'message': f'TODO/FIXME found: {line.strip()}',
                    'severity': 'info',
                })
            
            # Potential issues
            if 'except:' in line and 'except Exception:' not in line:
                issues.append({
                    'type': 'bare_except',
                    'line': i,
                    'message': 'Bare except clause, consider specifying exception type',
                    'severity': 'warning',
                })
    
    return issues


def build_fix_prompt(chunk_data: dict, issues: list, project_context: dict = None):
    """
    Build a prompt for the LLM to fix code issues in a chunk.
    """
    file_path = chunk_data.get('file_path', 'unknown')
    content = chunk_data.get('content', '')
    start_line = chunk_data.get('start_line', 0)
    context_before = chunk_data.get('context_before', '')
    context_after = chunk_data.get('context_after', '')
    
    # Filter issues for this chunk
    chunk_issues = [iss for iss in issues if start_line <= iss.get('line', 0) <= chunk_data.get('end_line', 0)]
    
    prompt_parts = []
    
    # Project context
    if project_context:
        prompt_parts.append(f"Project Context:\n{json.dumps(project_context, indent=2)}\n")
    
    # File information
    prompt_parts.append(f"File: {file_path}")
    prompt_parts.append(f"Lines: {start_line}-{chunk_data.get('end_line', 0)}\n")
    
    # Issues to fix
    if chunk_issues:
        prompt_parts.append("Issues to fix:")
        for iss in chunk_issues:
            prompt_parts.append(f"  - Line {iss['line']}: [{iss['severity']}] {iss['type']}: {iss['message']}")
        prompt_parts.append("")
    
    # Code with context
    prompt_parts.append("Code to fix (with context):")
    if context_before:
        prompt_parts.append("# ... previous code ...")
        prompt_parts.extend(context_before.split('\n')[-3:])  # Last 3 lines of context
    prompt_parts.append("```python")
    prompt_parts.append(content)
    prompt_parts.append("```")
    if context_after:
        prompt_parts.append("# ... following code ...")
        prompt_parts.extend(context_after.split('\n')[:3])  # First 3 lines of context
    
    prompt_parts.append("\nInstructions:")
    prompt_parts.append("1. Analyze the code and identified issues")
    prompt_parts.append("2. Provide a fixed version of the code")
    prompt_parts.append("3. Return ONLY the fixed code block (no explanations, no markdown formatting)")
    prompt_parts.append("4. Preserve the exact structure and functionality")
    prompt_parts.append("5. Ensure the code is syntactically correct and follows best practices")
    
    return "\n".join(prompt_parts)


def extract_fixed_code(llm_response: str):
    """
    Extract fixed code from LLM response, handling various formats.
    """
    # Try to extract code from markdown code blocks
    code_block_pattern = r'```(?:python)?\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, llm_response, re.DOTALL)
    if matches:
        return matches[0].strip()
    
    # If no code block, try to find code-like content
    lines = llm_response.split('\n')
    code_lines = []
    in_code = False
    
    for line in lines:
        # Skip explanation lines
        if line.strip().startswith('#') or 'explanation' in line.lower() or 'fixed' in line.lower():
            continue
        if line.strip():
            code_lines.append(line)
    
    if code_lines:
        return '\n'.join(code_lines).strip()
    
    # Fallback: return as-is
    return llm_response.strip()


def analyze_project(path='.', groq_api_key=None, use_llm=False, model='llama-3.1-8b-instant', endpoint=None):
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

# LangGraph-style tools for self-healing workflow

class CodeAnalyzerTool:
    """Tool for analyzing code and detecting issues"""
    
    @staticmethod
    def analyze_file(file_path: Path, content: str, language: str = 'python'):
        """Analyze a single file for issues"""
        return detect_code_issues(file_path, content, language)
    
    @staticmethod
    def analyze_codebase(root_path: Path, max_files: int = 100):
        """Analyze entire codebase and return summary"""
        scan_summary = scan_codebase(str(root_path), max_files=max_files)
        all_issues = []
        
        root = Path(root_path)
        for file_info in scan_summary.get('files', [])[:max_files]:
            file_path = root / file_info['path']
            if file_path.exists():
                content = read_text_file(file_path)
                if content:
                    lang = file_info.get('language', 'python')
                    issues = detect_code_issues(file_path, content, lang)
                    for issue in issues:
                        issue['file'] = file_info['path']
                    all_issues.extend(issues)
        
        return {
            'scan_summary': scan_summary,
            'issues': all_issues,
            'total_issues': len(all_issues),
        }


class CodeChunkerTool:
    """Tool for chunking code with context"""
    
    @staticmethod
    def chunk_file(file_path: Path, content: str, max_chunk_size: int = 2000, context_lines: int = 5):
        """Chunk a file into manageable pieces with context"""
        return chunk_code_with_context(file_path, content, max_chunk_size, context_lines)
    
    @staticmethod
    def chunk_codebase(root_path: Path, max_files: int = 50, max_chunk_size: int = 2000):
        """Chunk multiple files from codebase"""
        scan_summary = scan_codebase(str(root_path), max_files=max_files)
        all_chunks = []
        
        root = Path(root_path)
        for file_info in scan_summary.get('files', [])[:max_files]:
            file_path = root / file_info['path']
            if file_path.exists():
                content = read_text_file(file_path)
                if content and file_info.get('language') == 'python':  # Focus on Python for now
                    chunks = chunk_code_with_context(file_path, content, max_chunk_size)
                    all_chunks.extend(chunks)
        
        return {
            'chunks': all_chunks,
            'total_chunks': len(all_chunks),
        }


class LLMFixTool:
    """Tool for using LLM to fix code issues"""
    
    def __init__(self, api_key: str, model: str = 'llama-3.1-8b-instant', endpoint: str = None):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
    
    def fix_chunk(self, chunk_data: dict, issues: list, project_context: dict = None):
        """Use LLM to fix issues in a code chunk"""
        prompt = build_fix_prompt(chunk_data, issues, project_context)
        
        system_prompt = (
            "You are an expert Python code reviewer and fixer. "
            "Your task is to fix code issues while preserving functionality. "
            "Return only the fixed code, no explanations."
        )
        
        result = call_groq_api(
            prompt,
            self.api_key,
            model=self.model,
            endpoint=self.endpoint,
            system_prompt=system_prompt,
            max_tokens=4096
        )
        
        if result.get('status') == 'ok':
            fixed_code = extract_fixed_code(result.get('response', ''))
            return {
                'status': 'success',
                'fixed_code': fixed_code,
                'original_code': chunk_data.get('content', ''),
            }
        else:
            return {
                'status': 'error',
                'error': result.get('error', 'Unknown error'),
            }


class SelfHealWorkflow:
    """LangGraph-style workflow for self-healing code"""
    
    def __init__(self, api_key: str, model: str = 'llama-3.1-8b-instant', endpoint: str = None):
        self.analyzer = CodeAnalyzerTool()
        self.chunker = CodeChunkerTool()
        self.fixer = LLMFixTool(api_key, model, endpoint)
        self.api_key = api_key
        self.model = model
    
    def execute(
        self,
        root_path: Path,
        max_files: int = 50,
        job_id: str = None,
        log_callback=None,
        apply_changes: bool = True,
        max_total_bytes: int | None = None,
    ):
        """
        Execute the self-heal workflow:
        1. Analyze codebase for issues
        2. Chunk code with context
        3. Fix issues using LLM
        4. Apply fixes
        
        log_callback: Optional function(job_id, message, level) for logging
        """
        def emit_log(jid, msg, level="INFO"):
            if log_callback:
                log_callback(jid, msg, level)
        
        def emit_error(jid, msg):
            emit_log(jid, msg, "ERROR")
        
        root = Path(root_path).resolve()
        
        if job_id:
            emit_log(job_id, f"Starting AI-powered self-heal for {root_path}")
        
        # Step 1: Analyze codebase
        if job_id:
            emit_log(job_id, "Step 1: Analyzing codebase for issues...")
        analysis = self.analyzer.analyze_codebase(root, max_files=max_files)
        
        if job_id:
            emit_log(job_id, f"Found {analysis['total_issues']} issues across {len(analysis['scan_summary'].get('files', []))} files")
        
        # Step 2: Chunk codebase
        if job_id:
            emit_log(job_id, "Step 2: Chunking code with context...")
        chunking_result = self.chunker.chunk_codebase(root, max_files=max_files)
        
        if job_id:
            emit_log(job_id, f"Created {chunking_result['total_chunks']} chunks")
        
        # Step 3: Fix issues using LLM
        if job_id:
            emit_log(job_id, "Step 3: Fixing issues using AI...")
        
        project_context = {
            'project_type': infer_project_type(analysis['scan_summary']),
            'languages': analysis['scan_summary'].get('languages', {}),
            'file_count': analysis['scan_summary'].get('file_count', 0),
        }
        
        fixes_applied = []
        errors = []
        
        # Group issues by file
        issues_by_file = defaultdict(list)
        for issue in analysis['issues']:
            issues_by_file[issue['file']].append(issue)
        
        # Process chunks that have issues
        for chunk in chunking_result['chunks']:
            file_path = chunk['file_path']
            try:
                if Path(file_path).is_absolute():
                    rel_path = Path(file_path).relative_to(root)
                else:
                    rel_path = Path(file_path)
            except ValueError:
                # If relative_to fails, use the path as-is
                rel_path = Path(file_path)
            
            # Get issues for this chunk
            file_issues = issues_by_file.get(str(rel_path), [])
            chunk_issues = [
                iss for iss in file_issues
                if chunk['start_line'] <= iss.get('line', 0) <= chunk['end_line']
            ]
            
            if chunk_issues:
                if job_id:
                    emit_log(job_id, f"Fixing chunk {chunk['chunk_id']} in {rel_path} ({len(chunk_issues)} issues)")
                
                fix_result = self.fixer.fix_chunk(chunk, chunk_issues, project_context)
                
                if fix_result.get('status') == 'success':
                    fixes_applied.append({
                        'file': str(rel_path),
                        'chunk_id': chunk['chunk_id'],
                        'start_line': chunk['start_line'],
                        'end_line': chunk['end_line'],
                        'fixed_code': fix_result['fixed_code'],
                        'original_code': fix_result['original_code'],
                    })
                else:
                    errors.append({
                        'file': str(rel_path),
                        'chunk_id': chunk['chunk_id'],
                        'error': fix_result.get('error', 'Unknown error'),
                    })
                    if job_id:
                        emit_error(job_id, f"Failed to fix chunk {chunk['chunk_id']}: {fix_result.get('error')}")
        
        # Step 4: Apply fixes to files
        if job_id:
            emit_log(job_id, f"Step 4: Applying {len(fixes_applied)} fixes to files...")
        
        applied_count = 0
        applied_bytes = 0
        for fix in fixes_applied:
            try:
                file_path = root / fix['file']
                if file_path.exists():
                    content = read_text_file(file_path)
                    if content:
                        if '\x00' in content:
                            errors.append({'file': fix['file'], 'error': 'Binary file skipped'})
                            continue
                        lines = content.split('\n')
                        start_idx = fix['start_line'] - 1
                        end_idx = fix['end_line'] - 1
                        
                        # Replace the chunk with fixed code
                        fixed_lines = fix['fixed_code'].split('\n')
                        new_lines = lines[:start_idx] + fixed_lines + lines[end_idx + 1:]
                        new_content = '\n'.join(new_lines)
                        delta = abs(len(new_content) - len(content))
                        if max_total_bytes is not None and applied_bytes + delta > max_total_bytes:
                            errors.append({'file': fix['file'], 'error': 'Max byte budget exceeded'})
                            break
                        fix['file_before'] = content
                        fix['file_after'] = new_content
                        applied_bytes += delta
                        if apply_changes:
                            file_path.write_text(new_content, encoding='utf-8')
                            applied_count += 1

                            if job_id:
                                emit_log(job_id, f"Applied fix to {fix['file']} (chunk {fix['chunk_id']})")
                        else:
                            # Mark as pending application
                            fix['pending_apply'] = True
            except Exception as e:
                errors.append({
                    'file': fix['file'],
                    'error': str(e),
                })
                if job_id:
                    emit_error(job_id, f"Failed to apply fix to {fix['file']}: {str(e)}")
        
        result = {
            'status': 'completed',
            'issues_found': analysis['total_issues'],
            'chunks_processed': chunking_result['total_chunks'],
            'fixes_applied': applied_count,
            'applied_changes': applied_count,
            'applied_bytes': applied_bytes,
            'auto_applied': apply_changes,
            'errors': errors,
            'analysis': analysis,
            'fixes': fixes_applied,
        }
        
        if job_id:
            emit_log(job_id, f"Self-heal completed: {applied_count} fixes applied, {len(errors)} errors")
        
        return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Langraph: summarize codebase structure and optionally query Groq LLM.')
    parser.add_argument('path', nargs='?', default='.', help='Path to project root')
    parser.add_argument('--llm', action='store_true', help='Call Groq LLM with aggregated prompt')
    parser.add_argument('--key', default=None, help='Groq API key (or set env var GROQ_API_KEY)')
    parser.add_argument('--model', default='llama-3.1-8b-instant', help='Groq model name')
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

