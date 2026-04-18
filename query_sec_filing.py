"""
Vectorless RAG Query Pipeline for SEC 10-K Filings
===================================================

Uses PageIndex tree structures + Anthropic Claude for reasoning-based
document retrieval without any vector database or embedding model.

The query pipeline:
1. Loads the pre-generated PageIndex tree structure (JSON)
2. Sends the tree + user query to Claude
3. Claude reasons over the tree to identify relevant sections
4. Retrieves the actual text content from those sections
5. Claude generates a final answer grounded in the retrieved context

This enables precise, traceable retrieval with page/section references.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Add PageIndex to path
sys.path.insert(0, str(Path(__file__).parent / "PageIndex"))

from dotenv import load_dotenv
load_dotenv()

import litellm
litellm.drop_params = True

MODEL = "anthropic/claude-sonnet-4-20250514"
RESULTS_DIR = Path("./results")
MD_DIR = Path("./documents/markdown")


def load_tree_structure(filing_name: str) -> dict:
    """Load PageIndex tree structure for a filing."""
    json_path = RESULTS_DIR / f"{filing_name}_structure.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Tree structure not found: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_tree_without_text(structure: dict) -> dict:
    """Strip text content from tree, keeping structure + summaries + text length for reasoning."""
    if isinstance(structure, dict):
        clean = {}
        for k, v in structure.items():
            if k == 'text':
                # Replace text with its length so Claude can assess which nodes have real content
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


def get_node_by_id(tree: dict, target_id: str) -> Optional[dict]:
    """Find a node in the tree by its node_id."""
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


def get_section_text(tree: dict, node_ids: List[str]) -> Dict[str, str]:
    """Retrieve text content for specific nodes by their IDs."""
    results = {}
    structure = tree.get('structure', tree)
    
    for nid in node_ids:
        node = get_node_by_id(structure, nid)
        if node and node.get('text'):
            results[nid] = {
                'title': node.get('title', 'Unknown'),
                'text': node['text'],
                'summary': node.get('summary', ''),
                'line_num': node.get('line_num', 'unknown'),
            }
    return results


def get_text_by_line_range(md_path: str, start_line: int, end_line: int) -> str:
    """Get text from markdown file by line range."""
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    return ''.join(lines[start_line-1:end_line])


def reasoning_retrieval(tree: dict, question: str, filing_name: str) -> Tuple[List[str], str]:
    """
    Step 1: Send tree structure (without text) + question to Claude.
    Claude reasons over the structure to identify which sections contain the answer.
    Returns node IDs of relevant sections.
    """
    # Get clean tree structure (no text, just hierarchy + summaries)
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

    response = litellm.completion(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    
    content = response.choices[0].message.content
    
    # Parse the JSON response
    try:
        # Extract JSON from potential markdown code block
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
        
        return node_ids, reasoning, confidence
    except (json.JSONDecodeError, IndexError):
        print(f"  ⚠️ Could not parse retrieval response, using fallback")
        return [], content, 'low'


def generate_answer(question: str, context: Dict[str, dict], filing_name: str, reasoning: str) -> str:
    """
    Step 2: Given the retrieved section text, generate a grounded answer.
    """
    if not context:
        return "I could not find relevant sections in the filing to answer this question."
    
    # Build context string from retrieved sections
    context_str = ""
    for nid, info in context.items():
        # Limit each section to avoid token overflow
        text = info['text'][:16000] if len(info['text']) > 16000 else info['text']
        context_str += f"\n\n--- Section: {info['title']} (Node {nid}, Line {info['line_num']}) ---\n{text}\n"
    
    prompt = f"""You are an expert financial analyst answering questions about SEC 10-K filings.

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

    response = litellm.completion(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    
    return response.choices[0].message.content


def query_filing(filing_name: str, question: str, verbose: bool = True) -> dict:
    """
    Full vectorless RAG pipeline:
    1. Load tree structure
    2. Reasoning-based retrieval (identify relevant sections)
    3. Retrieve section text
    4. Generate grounded answer
    """
    start_time = time.time()
    
    if verbose:
        print(f"\n{'='*60}")
        print(f"📋 Filing: {filing_name}")
        print(f"❓ Question: {question}")
        print(f"{'='*60}")
    
    # Step 1: Load tree
    tree = load_tree_structure(filing_name)
    if verbose:
        n_nodes = count_nodes(tree.get('structure', tree))
        print(f"\n📊 Tree loaded: {n_nodes} nodes")
    
    # Step 2: Reasoning-based retrieval
    if verbose:
        print(f"\n🧠 Step 1: Reasoning over tree structure...")
    
    retrieval_start = time.time()
    node_ids, reasoning, confidence = reasoning_retrieval(tree, question, filing_name)
    retrieval_time = time.time() - retrieval_start
    
    if verbose:
        print(f"  Reasoning: {reasoning[:200]}...")
        print(f"  Selected nodes: {node_ids}")
        print(f"  Confidence: {confidence}")
        print(f"  ⏱️  Retrieval: {retrieval_time:.2f}s")
    
    # Step 3: Retrieve section text
    if verbose:
        print(f"\n📖 Step 2: Retrieving section content...")
    
    context = get_section_text(tree, node_ids)
    
    if verbose:
        for nid, info in context.items():
            text_len = len(info['text'])
            print(f"  [{nid}] {info['title']} ({text_len} chars)")
    
    # Step 4: Generate answer
    if verbose:
        print(f"\n💡 Step 3: Generating answer...")
    
    answer_start = time.time()
    answer = generate_answer(question, context, filing_name, reasoning)
    answer_time = time.time() - answer_start
    
    total_time = time.time() - start_time
    
    if verbose:
        print(f"\n{'─'*60}")
        print(f"📝 ANSWER:")
        print(f"{'─'*60}")
        print(answer)
        print(f"\n⏱️  Total: {total_time:.2f}s (retrieval: {retrieval_time:.2f}s, answer: {answer_time:.2f}s)")
    
    return {
        'filing': filing_name,
        'question': question,
        'answer': answer,
        'retrieved_nodes': node_ids,
        'retrieved_sections': {nid: info['title'] for nid, info in context.items()},
        'reasoning': reasoning,
        'confidence': confidence,
        'retrieval_time': retrieval_time,
        'answer_time': answer_time,
        'total_time': total_time,
    }


def count_nodes(node):
    if isinstance(node, dict):
        count = 1
        for child in node.get('nodes', []):
            count += count_nodes(child)
        return count
    elif isinstance(node, list):
        return sum(count_nodes(n) for n in node)
    return 0


def list_available_filings():
    """List all filings that have generated tree structures."""
    filings = []
    for f in sorted(RESULTS_DIR.glob("*_structure.json")):
        filings.append(f.stem.replace("_structure", ""))
    return filings


# ─── Interactive Mode ───────────────────────────────────────

def interactive_mode():
    """Interactive Q&A mode for SEC filings."""
    print("\n" + "="*60)
    print("🌲 Vectorless RAG for SEC 10-K Filings")
    print("   Powered by PageIndex + Anthropic Claude")
    print("="*60)
    
    filings = list_available_filings()
    if not filings:
        print("\n❌ No indexed filings found. Run batch_index.py first.")
        return
    
    print(f"\n📁 Available filings ({len(filings)}):")
    for i, f in enumerate(filings, 1):
        print(f"  {i}. {f}")
    
    print("\n💡 Example questions:")
    print('  - "What was Apple\'s total revenue in fiscal year 2025?"')
    print('  - "What are the main risk factors mentioned?"')
    print('  - "How much did Apple spend on R&D?"')
    print('  - "What is Apple\'s employee count?"')
    
    while True:
        print(f"\n{'─'*60}")
        filing_input = input("📄 Filing name (or number, or 'all', or 'quit'): ").strip()
        
        if filing_input.lower() in ('quit', 'exit', 'q'):
            break
        
        # Handle number input
        if filing_input.isdigit():
            idx = int(filing_input) - 1
            if 0 <= idx < len(filings):
                filing_input = filings[idx]
            else:
                print("Invalid number")
                continue
        
        if filing_input == 'all':
            selected_filings = filings
        elif filing_input in filings:
            selected_filings = [filing_input]
        else:
            print(f"Filing '{filing_input}' not found. Available: {filings}")
            continue
        
        question = input("❓ Question: ").strip()
        if not question:
            continue
        
        results = []
        for filing in selected_filings:
            result = query_filing(filing, question)
            results.append(result)
        
        # If querying multiple filings, show comparison
        if len(results) > 1:
            print(f"\n{'='*60}")
            print("📊 CROSS-FILING COMPARISON")
            print(f"{'='*60}")
            for r in results:
                print(f"\n📄 {r['filing']}:")
                print(f"   {r['answer'][:300]}...")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # CLI mode: python query_sec_filing.py <filing_name> "<question>"
        filing = sys.argv[1]
        question = sys.argv[2] if len(sys.argv) > 2 else "What was the total revenue?"
        query_filing(filing, question)
    else:
        interactive_mode()
