"""
Batch generate PageIndex tree structures for all SEC 10-K filings.
Uses Anthropic Claude via LiteLLM.
"""

import os
import sys
import json
import time
from pathlib import Path

# Add PageIndex to path
sys.path.insert(0, str(Path(__file__).parent / "PageIndex"))

from dotenv import load_dotenv
load_dotenv()

from pageindex.page_index_md import md_to_tree
from pageindex.utils import ConfigLoader
import asyncio


MODEL = "anthropic/claude-sonnet-4-20250514"
MD_DIR = Path("./documents/markdown")
RESULTS_DIR = Path("./results")


def generate_tree_for_file(md_path: Path) -> dict:
    """Generate PageIndex tree structure for a single markdown file."""
    
    print(f"\n{'='*60}")
    print(f"📄 Processing: {md_path.name}")
    print(f"{'='*60}")
    
    start_time = time.time()
    
    config = ConfigLoader(default_path=Path("./PageIndex/pageindex/config.yaml"))
    opt = config.load({
        'model': MODEL,
        'if_add_node_summary': 'yes',
        'if_add_doc_description': 'yes',
        'if_add_node_text': 'yes',
        'if_add_node_id': 'yes',
    })
    
    result = asyncio.run(md_to_tree(
        md_path=str(md_path),
        if_thinning=False,
        if_add_node_summary=opt.if_add_node_summary,
        summary_token_threshold=200,
        model=opt.model,
        if_add_doc_description=opt.if_add_doc_description,
        if_add_node_text=opt.if_add_node_text,
        if_add_node_id=opt.if_add_node_id,
    ))
    
    elapsed = time.time() - start_time
    
    # Save result
    output_name = md_path.stem + "_structure.json"
    output_path = RESULTS_DIR / output_name
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    # Stats
    def count_nodes(node):
        if isinstance(node, dict):
            count = 1
            for child in node.get('nodes', []):
                count += count_nodes(child)
            return count
        elif isinstance(node, list):
            return sum(count_nodes(n) for n in node)
        return 0
    
    structure = result.get('structure', result)
    n_nodes = count_nodes(structure)
    
    print(f"  ✅ Generated: {output_path}")
    print(f"  📊 {n_nodes} nodes, {elapsed:.1f}s")
    
    return result


def main():
    md_files = sorted(MD_DIR.glob("*.md"))
    
    if not md_files:
        print("❌ No markdown files found!")
        sys.exit(1)
    
    # Check which ones already have results
    existing = set()
    for f in RESULTS_DIR.glob("*_structure.json"):
        existing.add(f.stem.replace("_structure", ""))
    
    to_process = [f for f in md_files if f.stem not in existing]
    
    print(f"Found {len(md_files)} markdown filings total")
    print(f"Already processed: {len(existing)}")
    print(f"To process: {len(to_process)}")
    
    if not to_process:
        print("\n✅ All filings already have tree structures!")
        return
    
    for md_file in to_process:
        try:
            generate_tree_for_file(md_file)
        except Exception as e:
            print(f"\n❌ Error processing {md_file.name}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"\n{'='*60}")
    print(f"✅ Batch processing complete!")
    print(f"📁 Results saved to: {RESULTS_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
