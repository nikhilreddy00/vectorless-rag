"""
Vectorless RAG — Web Application
=================================

Flask backend with Server-Sent Events (SSE) for real-time streaming
of the tree-based reasoning retrieval process.

Endpoints:
  GET  /                  → Main UI
  GET  /api/filings       → List all indexed filings
  GET  /api/tree/<name>   → Get tree structure for a filing
  POST /api/query         → Run a query with SSE streaming
"""

import os
import sys
import json
import time
from pathlib import Path
from flask import Flask, jsonify, request, Response, send_from_directory

# Add PageIndex to path
sys.path.insert(0, str(Path(__file__).parent / "PageIndex"))

from dotenv import load_dotenv
load_dotenv()

import litellm
litellm.drop_params = True

MODEL = "anthropic/claude-sonnet-4-20250514"
RESULTS_DIR = Path("./results")
MD_DIR = Path("./documents/markdown")

# ─── Rate Limiting (protects API costs) ─────────────────────
MAX_QUERIES = 5
USAGE_FILE = Path("./usage.json")

def get_usage() -> dict:
    """Load usage stats from file."""
    if USAGE_FILE.exists():
        with open(USAGE_FILE, 'r') as f:
            return json.load(f)
    return {'query_count': 0, 'queries': []}

def save_usage(usage: dict):
    """Persist usage stats to file."""
    with open(USAGE_FILE, 'w') as f:
        json.dump(usage, f, indent=2)

def increment_usage(question: str, filing: str):
    """Record a query and increment counter."""
    usage = get_usage()
    usage['query_count'] += 1
    usage['queries'].append({
        'question': question[:200],
        'filing': filing,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
    })
    save_usage(usage)
    return usage['query_count']

def queries_remaining() -> int:
    """How many queries are left."""
    return max(0, MAX_QUERIES - get_usage()['query_count'])


app = Flask(__name__, static_folder="static")


# ─── Utility Functions ──────────────────────────────────────

def load_tree(filing_name: str) -> dict:
    """Load PageIndex tree structure for a filing."""
    json_path = RESULTS_DIR / f"{filing_name}_structure.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Not found: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_tree_for_display(structure, parent_path=""):
    """Transform tree into a flat list for UI display with metadata."""
    if isinstance(structure, list):
        result = []
        for item in structure:
            result.extend(get_tree_for_display(item, parent_path))
        return result
    
    if not isinstance(structure, dict):
        return []
    
    node_id = structure.get('node_id', '?')
    title = structure.get('title', 'Untitled')
    summary = structure.get('summary', '')
    text = structure.get('text', '')
    text_length = len(text) if text else 0
    line_num = structure.get('line_num', None)
    children = structure.get('nodes', [])
    
    node = {
        'node_id': node_id,
        'title': title,
        'summary': summary[:200] if summary else '',
        'text_length': text_length,
        'line_num': line_num,
        'has_children': len(children) > 0,
        'is_content': text_length > 200,  # Content vs TOC indicator
        'children': []
    }
    
    for child in children:
        child_nodes = get_tree_for_display(child, f"{parent_path}/{node_id}")
        node['children'].extend(child_nodes)
    
    return [node]


def get_tree_without_text(structure):
    """Strip text for reasoning, add text_length."""
    if isinstance(structure, dict):
        clean = {}
        for k, v in structure.items():
            if k == 'text':
                clean['text_length'] = len(v) if v else 0
                continue
            elif k == 'nodes':
                clean[k] = get_tree_without_text(v)
            else:
                clean[k] = v
        return clean
    elif isinstance(structure, list):
        return [get_tree_without_text(item) for item in structure]
    return structure


def get_node_by_id(tree, target_id):
    """Find a node in the tree by node_id."""
    if isinstance(tree, dict):
        if tree.get('node_id') == target_id:
            return tree
        for child in tree.get('nodes', []):
            result = get_node_by_id(child, target_id)
            if result:
                return result
    elif isinstance(tree, list):
        for item in tree:
            result = get_node_by_id(item, target_id)
            if result:
                return result
    return None


def count_nodes(node):
    if isinstance(node, dict):
        count = 1
        for child in node.get('nodes', []):
            count += count_nodes(child)
        return count
    elif isinstance(node, list):
        return sum(count_nodes(n) for n in node)
    return 0


# ─── API Endpoints ──────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/usage')
def get_usage_api():
    """Get current usage stats."""
    usage = get_usage()
    return jsonify({
        'used': usage['query_count'],
        'limit': MAX_QUERIES,
        'remaining': queries_remaining()
    })


@app.route('/api/filings')
def get_filings():
    """List all available indexed filings with metadata."""
    filings = []
    for f in sorted(RESULTS_DIR.glob("*_structure.json")):
        name = f.stem.replace("_structure", "")
        data = load_tree(name)
        nodes = count_nodes(data.get('structure', data))
        
        # Parse filing info from filename
        parts = name.split('-')
        ticker = parts[0].upper() if parts else name
        date = parts[1] if len(parts) > 1 else ''
        year = date[:4] if date else ''
        
        filings.append({
            'name': name,
            'ticker': ticker,
            'year': year,
            'date': date,
            'node_count': nodes,
            'file_size_kb': round(os.path.getsize(f) / 1024),
            'doc_name': data.get('doc_name', ''),
            'doc_description': data.get('doc_description', ''),
        })
    
    return jsonify(filings)


@app.route('/api/tree/<filing_name>')
def get_tree(filing_name):
    """Get the full tree structure for visualization."""
    try:
        data = load_tree(filing_name)
        tree = get_tree_for_display(data.get('structure', data))
        return jsonify({
            'filing': filing_name,
            'doc_name': data.get('doc_name', ''),
            'line_count': data.get('line_count', 0),
            'tree': tree
        })
    except FileNotFoundError:
        return jsonify({'error': f'Filing {filing_name} not found'}), 404


@app.route('/api/query', methods=['POST'])
def query_filing():
    """Run a vectorless RAG query with Server-Sent Events streaming."""
    data = request.json
    filing_name = data.get('filing')
    question = data.get('question')
    
    if not filing_name or not question:
        return jsonify({'error': 'Missing filing or question'}), 400
    
    # Rate limit check
    remaining = queries_remaining()
    if remaining <= 0:
        def rate_limited():
            yield sse_event('error', {
                'message': f'Demo limit reached ({MAX_QUERIES}/{MAX_QUERIES} queries used). Thank you for trying Vectorless RAG! 🎉'
            })
        return Response(rate_limited(), mimetype='text/event-stream',
                       headers={'Cache-Control': 'no-cache'})
    
    # Increment usage counter
    count = increment_usage(question, filing_name)
    
    def generate():
        """SSE generator — streams each step of the RAG pipeline."""
        total_start = time.time()
        
        # ── Step 0: Load tree ────────────────────────────────
        yield sse_event('step', {
            'step': 0,
            'title': 'Loading Tree Structure',
            'status': 'running',
            'detail': f'Loading {filing_name}_structure.json...'
        })
        
        try:
            tree = load_tree(filing_name)
        except FileNotFoundError:
            yield sse_event('error', {'message': f'Filing {filing_name} not found'})
            return
        
        n_nodes = count_nodes(tree.get('structure', tree))
        
        yield sse_event('step', {
            'step': 0,
            'title': 'Loading Tree Structure',
            'status': 'done',
            'detail': f'Tree loaded: {n_nodes} nodes',
            'node_count': n_nodes
        })
        
        # ── Step 1: Reasoning-based retrieval ────────────────
        yield sse_event('step', {
            'step': 1,
            'title': 'Reasoning Over Tree',
            'status': 'running',
            'detail': 'Claude is analyzing the tree structure to find relevant sections...'
        })
        
        tree_struct = get_tree_without_text(tree.get('structure', tree))
        
        prompt = f"""You are an expert financial analyst. You are given the hierarchical structure of an SEC 10-K filing and a question about the filing.

Each node has a "text_length" field indicating how many characters of actual content it contains.

DOCUMENT STRUCTURE (JSON tree with section titles, node IDs, summaries, and text_length):
```json
{json.dumps(tree_struct, indent=2, ensure_ascii=False)[:20000]}
```

QUESTION: {question}

Your task is to identify which sections (by node_id) are most likely to contain the answer to this question. Think step-by-step about where in a 10-K filing this information would typically be found.

Respond in JSON format only:
```json
{{
    "reasoning": "Your step-by-step reasoning about which sections to check",
    "relevant_node_ids": ["0039", "0041"],
    "confidence": "high/medium/low"
}}
```

CRITICAL RULES:
- This document has DUPLICATE section headings: early nodes (low IDs) are Table of Contents entries with very short text (text_length < 200). They contain NO useful content.
- ONLY select nodes with text_length > 500. These are the ACTUAL CONTENT sections.
- Nodes with text_length < 200 are just Table of Contents entries - NEVER select them.
- Select 1-4 most relevant CONTENT sections. Be precise, don't select too many."""

        retrieval_start = time.time()
        
        response = litellm.completion(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        
        retrieval_time = time.time() - retrieval_start
        content = response.choices[0].message.content
        
        # Parse response
        try:
            if '```json' in content:
                json_str = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                json_str = content.split('```')[1].split('```')[0]
            else:
                json_str = content
            
            result = json.loads(json_str)
            node_ids = result.get('relevant_node_ids', [])
            reasoning = result.get('reasoning', '')
            confidence = result.get('confidence', 'unknown')
        except (json.JSONDecodeError, IndexError):
            node_ids = []
            reasoning = content
            confidence = 'low'
        
        yield sse_event('step', {
            'step': 1,
            'title': 'Reasoning Over Tree',
            'status': 'done',
            'detail': reasoning,
            'selected_nodes': node_ids,
            'confidence': confidence,
            'time': round(retrieval_time, 2)
        })
        
        # ── Step 2: Retrieve section content ─────────────────
        yield sse_event('step', {
            'step': 2,
            'title': 'Retrieving Section Content',
            'status': 'running',
            'detail': f'Fetching text from {len(node_ids)} sections...'
        })
        
        context = {}
        retrieved_sections = []
        structure = tree.get('structure', tree)
        
        for nid in node_ids:
            node = get_node_by_id(structure, nid)
            if node and node.get('text'):
                text = node['text']
                context[nid] = {
                    'title': node.get('title', 'Unknown'),
                    'text': text,
                    'summary': node.get('summary', ''),
                    'line_num': node.get('line_num', 'unknown'),
                }
                retrieved_sections.append({
                    'node_id': nid,
                    'title': node.get('title', 'Unknown'),
                    'text_length': len(text),
                    'line_num': node.get('line_num', 'unknown'),
                    'preview': text[:300] + '...' if len(text) > 300 else text
                })
        
        yield sse_event('step', {
            'step': 2,
            'title': 'Retrieving Section Content',
            'status': 'done',
            'detail': f'Retrieved {len(context)} sections ({sum(len(c["text"]) for c in context.values())} chars total)',
            'sections': retrieved_sections
        })
        
        # ── Step 3: Generate answer ──────────────────────────
        yield sse_event('step', {
            'step': 3,
            'title': 'Generating Answer',
            'status': 'running',
            'detail': 'Claude is synthesizing the answer from retrieved sections...'
        })
        
        if not context:
            answer = "I could not find relevant sections to answer this question."
        else:
            context_str = ""
            for nid, info in context.items():
                text = info['text'][:16000] if len(info['text']) > 16000 else info['text']
                context_str += f"\n\n--- Section: {info['title']} (Node {nid}, Line {info['line_num']}) ---\n{text}\n"
            
            answer_prompt = f"""You are an expert financial analyst answering questions about SEC 10-K filings.

FILING: {filing_name}

RETRIEVED SECTIONS:
{context_str}

QUESTION: {question}

Instructions:
1. Answer the question based ONLY on the retrieved sections above.
2. Be precise and cite specific numbers, dates, and facts from the filing.
3. If the answer is a number, include the exact figure.
4. If the information is not in the retrieved sections, say so explicitly.
5. Reference which section the information came from.

Answer:"""

            answer_start = time.time()
            answer_response = litellm.completion(
                model=MODEL,
                messages=[{"role": "user", "content": answer_prompt}],
                temperature=0,
            )
            answer = answer_response.choices[0].message.content
            answer_time = time.time() - answer_start
        
        total_time = time.time() - total_start
        
        yield sse_event('step', {
            'step': 3,
            'title': 'Generating Answer',
            'status': 'done',
            'detail': 'Answer generated successfully',
            'time': round(answer_time if context else 0, 2)
        })
        
        # ── Final result ─────────────────────────────────────
        yield sse_event('result', {
            'answer': answer,
            'filing': filing_name,
            'question': question,
            'retrieved_nodes': node_ids,
            'retrieved_sections': {nid: info['title'] for nid, info in context.items()},
            'reasoning': reasoning,
            'confidence': confidence,
            'total_time': round(total_time, 2),
            'queries_remaining': queries_remaining()
        })
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


def sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


if __name__ == '__main__':
    print("\n" + "="*60)
    print("🌲 Vectorless RAG — Web Application")
    print("   Powered by PageIndex + Anthropic Claude")
    print("="*60)
    print(f"\n📁 Indexed filings: {len(list(RESULTS_DIR.glob('*_structure.json')))}")
    print(f"\n🌐 Starting server at http://localhost:5001")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=5001, debug=False)
